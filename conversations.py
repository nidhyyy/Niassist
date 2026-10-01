"""Persistent browser-owned chats. Complete turns are the only model history."""
import sqlite3
from contextlib import contextmanager
import time
from uuid import uuid4


class ChatError(Exception):
    def __init__(self, message, status=404):
        super().__init__(message)
        self.status = status


class Conversations:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    updated REAL NOT NULL, lease TEXT, lease_until REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS chat_owner ON chats(owner, updated);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, chat TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    question TEXT NOT NULL, answer TEXT NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner):
        cid = uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO chats(id,owner,title,updated) VALUES(?,?,?,?)',
                       (cid, owner, 'New chat', time.time()))
        return cid

    def list(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,title FROM chats WHERE owner=? ORDER BY updated DESC', (owner,))]

    def read(self, owner, cid):
        with self.connect() as db:
            self.owned(db, owner, cid)
            return [dict(r) for r in db.execute(
                'SELECT question,answer FROM turns WHERE chat=? ORDER BY id', (cid,))]

    def owned(self, db, owner, cid):
        row = db.execute('SELECT * FROM chats WHERE id=? AND owner=?', (cid, owner)).fetchone()
        if row is None:
            raise ChatError('Chat not found.')
        return row

    def delete(self, owner, cid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.owned(db, owner, cid)
            if row['lease_until'] > time.time():
                raise ChatError('An answer is still running. Try again shortly.', 409)
            db.execute('DELETE FROM chats WHERE id=?', (cid,))

    def acquire(self, owner, cid):
        token = uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.owned(db, owner, cid)
            if row['lease_until'] > time.time():
                raise ChatError('This chat is answering another message. Try again shortly.', 409)
            db.execute('UPDATE chats SET lease=?,lease_until=? WHERE id=?',
                       (token, time.time() + 180, cid))
            rows = db.execute('SELECT question,answer FROM turns WHERE chat=? ORDER BY id DESC LIMIT 10',
                              (cid,)).fetchall()
        # Bound cost without cutting a turn in half or mixing user/model roles.
        selected, size = [], 0
        for row in rows:
            length = len(row['question']) + len(row['answer'])
            if size + length > 24000:
                break
            selected.append(dict(row))
            size += length
        return token, list(reversed(selected))

    def finish(self, owner, cid, token, question, answer):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.owned(db, owner, cid)
            if row['lease'] != token:
                raise ChatError('Chat changed while answering. Please retry.', 409)
            db.execute('INSERT INTO turns(chat,question,answer) VALUES(?,?,?)', (cid, question, answer))
            db.execute('UPDATE chats SET title=?,updated=?,lease=NULL,lease_until=0 WHERE id=?',
                       (question[:60] if row['title'] == 'New chat' else row['title'], time.time(), cid))

    def release(self, cid, token):
        with self.connect() as db:
            db.execute('UPDATE chats SET lease=NULL,lease_until=0 WHERE id=? AND lease=?', (cid, token))
