"""Password accounts and revocable server-side login sessions."""
import hashlib
import secrets
import sqlite3
import time
from werkzeug.security import generate_password_hash, check_password_hash
from conversations import ChatError


class Accounts:
    def __init__(self, store):
        self.store = store
        self.dummy_hash = generate_password_hash('dummy-password-for-timing')
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS logins (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    bucket TEXT NOT NULL, timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_bucket ON auth_attempts(bucket, timestamp);
            ''')

    def limit(self, ip):
        bucket = hashlib.sha256(ip.encode()).hexdigest()
        now = time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_attempts WHERE timestamp < ?', (now - 900,))
            count = db.execute('SELECT count(*) FROM auth_attempts WHERE bucket=?', (bucket,)).fetchone()[0]
            if count >= 20:
                raise ChatError('Too many sign-in attempts. Try again in 15 minutes.', 429)
            db.execute('INSERT INTO auth_attempts VALUES(?,?)', (bucket, now))

    def register(self, email, password):
        uid = secrets.token_hex(16)
        hashed = generate_password_hash(password)
        try:
            with self.store.connect() as db:
                db.execute('INSERT INTO users VALUES(?,?,?)', (uid, email, hashed))
        except sqlite3.IntegrityError:
            raise ChatError('Unable to register this email. Try signing in instead.', 409)
        return {'id': uid, 'email': email}

    def authenticate(self, email, password):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        valid = check_password_hash(row['password_hash'] if row else self.dummy_hash, password)
        if not row or not valid:
            raise ChatError('Email or password is incorrect.', 401)
        return {'id': row['id'], 'email': row['email']}

    def new_session(self, uid):
        token = secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute('DELETE FROM logins WHERE expires < ?', (time.time(),))
            db.execute('INSERT INTO logins VALUES(?,?,?)',
                       (self.digest(token), uid, time.time() + 7 * 86400))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def user(self, token):
        if not isinstance(token, str):
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT users.id,users.email FROM logins JOIN users ON users.id=logins.user_id
                                WHERE token_hash=? AND expires>?''', (self.digest(token), time.time())).fetchone()
        return dict(row) if row else None

    def revoke(self, token):
        if token:
            with self.store.connect() as db:
                db.execute('DELETE FROM logins WHERE token_hash=?', (self.digest(token),))

    def import_chats(self, guest, uid):
        if not guest:
            return 0
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            busy = db.execute('SELECT 1 FROM chats WHERE owner=? AND lease_until>?', (guest, time.time())).fetchone()
            if busy:
                raise ChatError('A guest answer is still running. Import again shortly.', 409)
            return db.execute('UPDATE chats SET owner=? WHERE owner=?', ('user:' + uid, guest)).rowcount
