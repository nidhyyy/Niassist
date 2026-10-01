import logging
import json
import os
import secrets
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from time import perf_counter
from uuid import uuid4

from flask import Flask, Response, g, jsonify, render_template, request, session, redirect
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from settings import data_directory, database_path

from ai.gemini_client import AssistantError, ask_gemini, stream_gemini

from conversations import Conversations, ChatError
from accounts import Accounts
from workflow_store import WorkflowStore
from workflow_routes import bp as workflow_bp

MAX_TEXT_LENGTH = 4000


def create_app(config=None):
    app = Flask(__name__, instance_path=str(data_directory()))
    app.config.update(MAX_CONTENT_LENGTH=32 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE') == '1')
    app.config['DATABASE'] = database_path()
    app.config['PRODUCTION'] = os.getenv('NIASSIST_ENV') == 'production'
    app.config['TRUSTED_HOSTS'] = [h.strip() for h in os.getenv('NIASSIST_TRUSTED_HOSTS', '').split(',') if h.strip()] or None
    if config:
        app.config.update(config)
    if app.config['PRODUCTION']:
        if not app.config['SESSION_COOKIE_SECURE'] or not app.config['TRUSTED_HOSTS']:
            raise RuntimeError('Production requires COOKIE_SECURE=1 and NIASSIST_TRUSTED_HOSTS.')
    proxy_hops = os.getenv('NIASSIST_PROXY_HOPS', '0')
    if proxy_hops not in ('0', '1'):
        raise RuntimeError('NIASSIST_PROXY_HOPS must be 0 or 1 for the supported topology.')
    if proxy_hops == '1':
        # Only enable behind the supplied Nginx configuration; Gunicorn binds loopback.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.logger.setLevel(logging.INFO)
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    if not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    if not app.config.get('SECRET_KEY'):
        secret_file = instance / 'session-secret'
        if app.config['PRODUCTION'] and not secret_file.exists():
            raise RuntimeError('Provide SECRET_KEY or an existing persistent session-secret before production startup.')
        try:
            with secret_file.open('x') as handle:
                handle.write(secrets.token_hex(32))
            secret_file.chmod(0o600)
        except FileExistsError:
            pass
        app.config['SECRET_KEY'] = secret_file.read_text().strip()
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    if len(app.config['SECRET_KEY']) < 32 and app.config['PRODUCTION']:
        raise RuntimeError('Production session secret must contain at least 32 characters.')
    store = Conversations(app.config['DATABASE'])
    app.extensions['conversations'] = store
    accounts = Accounts(store)
    app.extensions['accounts'] = accounts
    app.extensions['workflow'] = WorkflowStore(store)
    app.register_blueprint(workflow_bp)
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
    release_file = Path(__file__).resolve().parent / 'RELEASE'
    release_id = release_file.read_text().strip() if release_file.is_file() else 'local'

    @app.before_request
    def identify_request():
        g.request_id = uuid4().hex
        g.user = accounts.user(session.get('login'))
        protected = (request.path.startswith('/api/') and not request.path.startswith('/api/auth/')) or request.path.startswith('/command')
        if request.path == '/api/documents' and request.method == 'POST':
            request.max_content_length = 6 * 1024 * 1024
        if protected and not g.user:
            return error('LOGIN_REQUIRED', 'Please sign in to continue.', 401)
        if request.method in ('POST', 'DELETE', 'PUT', 'PATCH'):
            origin = request.headers.get('Origin')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site' or (
                origin and urlsplit(origin).netloc != request.host
            ):
                return error('BAD_ORIGIN', 'Cross-site request rejected.', 403)
            token = request.headers.get('X-CSRF-Token', '')
            expected = session.get('csrf', '')
            if not expected or not secrets.compare_digest(token.encode('utf-8'), expected.encode('utf-8')):
                return error('CSRF_ERROR', 'Session changed. Refresh the page and try again.', 403)

    @app.after_request
    def identify_response(response):
        response.headers['X-Request-ID'] = getattr(g, 'request_id', uuid4().hex)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith(('/api/', '/command')) or request.path == '/workspace':
            response.headers['Cache-Control'] = 'no-store'
        return response

    def error(code, message, status):
        return jsonify(error={'code': code, 'message': message},
                       request_id=g.request_id), status

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        limit = '6 MB' if request.path == '/api/documents' else '32 KB'
        return error('REQUEST_TOO_LARGE', f'Request body exceeds {limit}.', 413)

    @app.route('/')
    def home():
        return render_template('index.html')

    @app.get('/workspace')
    def workflow_page():
        if not g.user:
            return redirect('/')
        return render_template('workspace.html')

    @app.get('/health')
    def health():
        # Liveness only: does not spend tokens or check provider credentials.
        return jsonify(status='ok')

    @app.get('/ready')
    def readiness():
        try:
            with store.connect() as db:
                db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
            worker = app.extensions['workflow'].worker_status()['online']
            key = os.getenv('GEMINI_API_KEY', '').strip()
            configured = bool(key and key != 'your_api_key_here')
            ready = worker and configured
            return jsonify(status='ready' if ready else 'not_ready', database=True,
                           worker=worker, ai_configured=configured, release=release_id), 200 if ready else 503
        except Exception:
            return jsonify(status='not_ready', database=False), 503

    def owner():
        return 'user:' + g.user['id']

    @app.get('/api/auth/session')
    def auth_session():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        guest = session.get('guest_owner') or session.get('owner')
        return jsonify(user=g.user, csrf_token=session['csrf'],
                       guest_chats=len(store.list(guest)) if guest else 0)

    @app.post('/api/auth/<action>')
    def auth_action(action):
        if action == 'logout':
            accounts.revoke(session.get('login'))
            guest = session.get('guest_owner') or session.get('owner')
            session.clear()
            if guest:
                session['guest_owner'] = guest
            return jsonify(ok=True)
        if action == 'import':
            if not g.user:
                return error('LOGIN_REQUIRED', 'Please sign in.', 401)
            count = accounts.import_chats(session.get('guest_owner') or session.get('owner'), g.user['id'])
            session.pop('guest_owner', None)
            session.pop('owner', None)
            return jsonify(imported=count)
        if action not in ('register', 'login'):
            return error('NOT_FOUND', 'Unknown action.', 404)
        if not request.is_json:
            return error('INVALID_CONTENT_TYPE', 'Send application/json.', 415)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error('INVALID_JSON', 'Send a JSON object.', 400)
        email, password = data.get('email'), data.get('password')
        if not isinstance(email, str) or not isinstance(password, str):
            return error('INVALID_ACCOUNT', 'Email and password are required.', 422)
        email = email.strip().lower()
        if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            return error('INVALID_ACCOUNT', 'Enter a valid email address.', 422)
        if not 12 <= len(password) <= 128:
            return error('INVALID_ACCOUNT', 'Use a password of 12–128 characters.', 422)
        accounts.limit(request.remote_addr or 'unknown')
        user = accounts.register(email, password) if action == 'register' else accounts.authenticate(email, password)
        guest = session.get('guest_owner') or session.get('owner')
        accounts.revoke(session.get('login'))
        session.clear()
        session['login'] = accounts.new_session(user['id'])
        session['csrf'] = secrets.token_urlsafe(32)
        session.permanent = True
        if guest:
            session['guest_owner'] = guest
        return jsonify(user=user, csrf_token=session['csrf']), 201 if action == 'register' else 200

    @app.errorhandler(AssistantError)
    def ai_error(exc):
        app.logger.warning('workflow_ai_failure request_id=%s code=%s', g.request_id, exc.code)
        return error(exc.code, exc.message, exc.status)

    @app.errorhandler(ChatError)
    def chat_error(exc):
        return error('CHAT_ERROR', str(exc), exc.status)

    @app.get('/api/chats')
    def chats():
        return jsonify(chats=store.list(owner()))

    @app.post('/api/chats')
    def new_chat():
        return jsonify(id=store.create(owner())), 201

    @app.get('/api/chats/<cid>')
    def read_chat(cid):
        return jsonify(turns=store.read(owner(), cid))

    @app.delete('/api/chats/<cid>')
    def delete_chat(cid):
        store.delete(owner(), cid)
        return jsonify(deleted=True)

    @app.post('/command/stream')
    @app.post('/command')
    def command():
        if not request.is_json:
            return error('INVALID_CONTENT_TYPE', 'Send application/json.', 415)
        try:
            data = request.get_json()
        except BadRequest:
            return error('INVALID_JSON', 'Request body must contain valid JSON.', 400)
        if not isinstance(data, dict) or not isinstance(data.get('text'), str):
            return error('INVALID_TEXT', 'text must be a string.', 422)
        text = data['text'].strip()
        if not text or len(text) > MAX_TEXT_LENGTH:
            return error('INVALID_TEXT', 'Enter between 1 and 4000 characters.', 422)
        app.extensions['workflow'].limit_ai(owner())
        if request.path == '/command/stream':
            request_id = g.request_id
            cid = data.get('conversation_id')
            chat_owner = None
            token = None
            history = []
            if cid is not None:
                if not isinstance(cid, str) or len(cid) != 32:
                    return error('INVALID_CHAT', 'Invalid conversation ID.', 422)
                chat_owner = owner()
                token, history = store.acquire(chat_owner, cid)

            def events():
                started = perf_counter()
                first_ms = None
                outcome = 'disconnected'
                source = None
                answer = []

                def event(kind, **payload):
                    return json.dumps(dict(type=kind, request_id=request_id, **payload)) + '\n'

                try:
                    yield event('start')
                    source = stream_gemini(text, history=history) if cid else stream_gemini(text)
                    for chunk in source:
                        if perf_counter() - started > 120:
                            raise AssistantError('AI_TIMEOUT', 'Answer exceeded the time limit. Please retry.', 504)
                        answer.append(chunk)
                        if first_ms is None:
                            first_ms = round((perf_counter() - started) * 1000)
                        yield event('delta', text=chunk)
                    if cid:
                        store.finish(chat_owner, cid, token, text, ''.join(answer))
                    outcome = 'completed'
                    yield event('done', first_text_ms=first_ms,
                                total_ms=round((perf_counter() - started) * 1000))
                except AssistantError as exc:
                    outcome = exc.code
                    yield event('error', code=exc.code, message=exc.message)
                except Exception as exc:
                    outcome = 'INTERNAL_ERROR'
                    app.logger.error('stream_failure request_id=%s type=%s',
                                     request_id, type(exc).__name__)
                    yield event('error', code=outcome,
                                message='The response was interrupted. Please try again.')
                finally:
                    if source is not None:
                        source.close()
                    if cid:
                        store.release(cid, token)
                    app.logger.info(
                        'chat_stream request_id=%s outcome=%s first_text_ms=%s total_ms=%s',
                        request_id, outcome, first_ms, round((perf_counter() - started) * 1000))

            return Response(events(), mimetype='application/x-ndjson', headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
            })
        try:
            response = ask_gemini(text)
        except AssistantError as exc:
            app.logger.warning('assistant_failure request_id=%s code=%s',
                               g.request_id, exc.code)
            return error(exc.code, exc.message, exc.status)
        except Exception as exc:
            # Do not log prompts, API keys, or raw upstream exception messages.
            app.logger.error('unexpected_failure request_id=%s type=%s',
                             g.request_id, type(exc).__name__)
            return error('INTERNAL_ERROR', 'Unable to complete your request. Please try again.', 500)
        return jsonify(response=response, request_id=g.request_id)

    return app


app = create_app()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    app.run(debug=False)
