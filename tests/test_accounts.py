import time
import pytest
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': tmp_path / 'auth.db'})


def post(client, path, data=None):
    csrf = client.get('/api/auth/session').json['csrf_token']
    return client.post(path, json=data, headers={'X-CSRF-Token': csrf})


def register(client, email='nidhi@example.test'):
    return post(client, '/api/auth/register', {'email': email, 'password': 'Long-password-123'})


def test_registration_hash_login_cross_browser_and_logout_revocation(app):
    first, second = app.test_client(), app.test_client()
    result = register(first)
    assert result.status_code == 201
    with app.extensions['conversations'].connect() as db:
        row = db.execute('SELECT password_hash FROM users').fetchone()
        assert row[0].startswith('scrypt:') and 'Long-password' not in row[0]
    cid = post(first, '/api/chats').json['id']
    login = post(second, '/api/auth/login', {'email': 'NIDHI@example.test', 'password': 'Long-password-123'})
    assert login.status_code == 200
    assert second.get('/api/chats/' + cid).status_code == 200
    cookie = first.get_cookie('session').value
    assert post(first, '/api/auth/logout').status_code == 200
    # Replay a previously valid cookie: server-side revocation must reject it.
    first.set_cookie('session', cookie)
    assert first.get('/api/chats').status_code == 401
    assert second.get('/api/chats').status_code == 200


def test_csrf_login_required_and_expiry(app):
    client = app.test_client()
    for path in ['/command', '/command/stream', '/api/chats']:
        assert client.post(path, json={'text': 'hello'}).status_code == 401
    assert client.post('/api/auth/register', json={}).status_code == 403
    assert register(client).status_code == 201
    assert client.post('/api/chats').status_code == 403
    with app.extensions['conversations'].connect() as db:
        db.execute('UPDATE logins SET expires=?', (time.time() - 1,))
    assert client.get('/api/chats').status_code == 401


def test_wrong_password_duplicate_validation_and_session_rotation(app):
    client = app.test_client()
    old = client.get('/api/auth/session').json['csrf_token']
    assert register(client).status_code == 201
    assert client.get('/api/auth/session').json['csrf_token'] != old
    assert register(app.test_client()).status_code == 409
    bad = post(app.test_client(), '/api/auth/login', {'email': 'nidhi@example.test', 'password': 'Wrong-password-123'})
    missing = post(app.test_client(), '/api/auth/login', {'email': 'nobody@example.test', 'password': 'Wrong-password-123'})
    assert bad.status_code == missing.status_code == 401
    assert bad.json['error']['message'] == missing.json['error']['message']
    assert post(client, '/api/auth/register', {'email': 'invalid', 'password': 'x'}).status_code == 422


def test_explicit_guest_import_preserves_other_owners_and_is_one_time(app):
    store = app.extensions['conversations']
    cid = store.create('old-guest')
    other = store.create('different-guest')
    client = app.test_client()
    with client.session_transaction() as session:
        session['owner'] = 'old-guest'  # Original M2A signed browser session.
    assert client.get('/api/auth/session').json['guest_chats'] == 1
    assert register(client).status_code == 201
    assert client.get('/api/chats/' + cid).status_code == 404
    assert post(client, '/api/auth/import').json['imported'] == 1
    assert client.get('/api/chats/' + cid).status_code == 200
    assert client.get('/api/chats/' + other).status_code == 404
    assert post(client, '/api/auth/import').json['imported'] == 0


def test_account_isolation(app):
    first, second = app.test_client(), app.test_client()
    register(first)
    register(second, 'other@example.test')
    cid = post(first, '/api/chats').json['id']
    assert second.get('/api/chats/' + cid).status_code == 404
    assert post(second, '/command/stream', {'text': 'hello', 'conversation_id': cid}).status_code == 404


def test_rate_limit_shared_between_clients(app):
    accounts = app.extensions['accounts']
    for _ in range(20):
        accounts.limit('127.0.0.1')
    result = register(app.test_client())
    assert result.status_code == 429
