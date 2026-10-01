"""Account-scoped documents, tasks, inbox reminders and action history."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from conversations import ChatError

MAX_UPLOAD = 5 * 1024 * 1024
STOP = set('a an the is are was were be been this that these those of on in at to for and or with by from as it its they them what how when where which please tell me about my document documents explain summarize summary'.split())


def terms(text):
    return [t for t in re.findall(r'\w+', text.lower()) if len(t) > 1 and t not in STOP][:300]


def stamp(value, label):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or len(value) > 50:
        raise ChatError(f'{label} must be a date with a time-zone offset.', 422)
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ChatError(f'{label} must include a valid date and time-zone offset.', 422)


def task_fields(data):
    if not isinstance(data, dict):
        raise ChatError('Each task must be an object.', 422)
    title, notes = data.get('title'), data.get('notes', '')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise ChatError('Task titles need 1–200 characters.', 422)
    if not isinstance(notes, str) or len(notes) > 3000:
        raise ChatError('Task notes must be at most 3000 characters.', 422)
    due = stamp(data.get('due_at'), 'Deadline')
    remind = stamp(data.get('remind_at'), 'Reminder')
    zone = data.get('timezone', 'UTC')
    try:
        if not isinstance(zone, str) or len(zone) > 80:
            raise ValueError()
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ChatError('Choose a valid IANA time zone.', 422)
    if remind:
        if datetime.fromisoformat(remind).timestamp() <= time.time():
            raise ChatError('Reminder time must be in the future.', 422)
        if due and datetime.fromisoformat(remind) > datetime.fromisoformat(due):
            raise ChatError('Set the reminder on or before the deadline.', 422)
    return dict(title=title.strip(), notes=notes.strip(), due_at=due, remind_at=remind, timezone=zone)


class WorkflowStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, raw BLOB NOT NULL, size INTEGER NOT NULL,
                created REAL NOT NULL, lease TEXT, lease_until REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS docs_owner ON documents(owner,created);
            CREATE TABLE IF NOT EXISTS document_chunks (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, page INTEGER, text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_doc ON document_chunks(document_id,ordinal);
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, notes TEXT NOT NULL,
                due_at TEXT, timezone TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                source TEXT NOT NULL DEFAULT '', created REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner,created);
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                owner TEXT NOT NULL, scheduled_at TEXT NOT NULL, status TEXT NOT NULL,
                delivered_at REAL
            );
            CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status,scheduled_at);
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, reminder_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL, read_at REAL
            );
            CREATE INDEX IF NOT EXISTS inbox_owner ON notifications(owner,created);
            CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL, action TEXT NOT NULL,
                title TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS confirmations (
                owner TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(owner,key)
            );
            CREATE TABLE IF NOT EXISTS worker_health (name TEXT PRIMARY KEY, seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_ai_limits (owner TEXT NOT NULL, at REAL NOT NULL);
            ''')

    @staticmethod
    def event(db, owner, action, title):
        db.execute('INSERT INTO workflow_events(owner,action,title,created) VALUES(?,?,?,?)',
                   (owner, action, title[:200], time.time()))

    def limit_ai(self, owner):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM workflow_ai_limits WHERE at<?', (time.time() - 60,))
            if db.execute('SELECT count(*) FROM workflow_ai_limits WHERE owner=?', (owner,)).fetchone()[0] >= 6:
                raise ChatError('Please wait a minute before sending more AI requests.', 429)
            db.execute('INSERT INTO workflow_ai_limits VALUES(?,?)', (owner,time.time()))

    def upload(self, owner, name, raw):
        name = str(name or '').replace('\\', '/').split('/')[-1][:150]
        kind = name.rsplit('.', 1)[-1].lower()
        if kind not in ('txt', 'pdf') or not raw or len(raw) > MAX_UPLOAD:
            raise ChatError('Upload a PDF or UTF-8 TXT file between 1 byte and 5 MB.', 422)
        if kind == 'pdf' and not raw.startswith(b'%PDF-'):
            raise ChatError('The file does not have a PDF header.', 422)
        if kind == 'txt':
            try:
                raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ChatError('TXT files must use UTF-8 encoding.', 422)
        did = uuid4().hex
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT count(*) FROM documents WHERE owner=?', (owner,)).fetchone()[0]
            if count >= 20:
                raise ChatError('Limit reached: delete a document before uploading another (20 per account).', 409)
            db.execute('INSERT INTO documents(id,owner,name,kind,status,raw,size,created) VALUES(?,?,?,?,?,?,?,?)',
                       (did,owner,name,kind,'queued',raw,len(raw),time.time()))
            self.event(db,owner,'document_uploaded',name)
        return did

    def document(self, owner, did):
        with self.store.connect() as db:
            row = db.execute('SELECT id,name,kind,status,error,size,created FROM documents WHERE id=? AND owner=?',
                             (did,owner)).fetchone()
        if not row:
            raise ChatError('Document not found.')
        return dict(row)

    def documents(self, owner):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,name,kind,status,error,size,created FROM documents WHERE owner=? ORDER BY created DESC', (owner,))]

    def delete_document(self, owner, did):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT name FROM documents WHERE id=? AND owner=?',(did,owner)).fetchone()
            if not row: raise ChatError('Document not found.')
            db.execute('DELETE FROM documents WHERE id=?',(did,))
            self.event(db,owner,'document_deleted',row['name'])

    def retry_document(self, owner, did):
        self.document(owner,did)
        with self.store.connect() as db:
            changed = db.execute("UPDATE documents SET status='queued',error=NULL WHERE id=? AND owner=? AND status='failed'", (did,owner)).rowcount
            if not changed: raise ChatError('Only failed documents can be retried.',409)

    def chunks(self, owner, ids):
        if not isinstance(ids,list) or not 1 <= len(ids) <= 5 or any(not isinstance(i,str) or len(i)!=32 for i in ids):
            raise ChatError('Select 1–5 documents.',422)
        for did in set(ids):
            if self.document(owner,did)['status'] != 'ready':
                raise ChatError('Wait for all selected documents to finish processing.',409)
        marks = ','.join('?' for _ in ids)
        with self.store.connect() as db:
            return [dict(r) for r in db.execute(f'''SELECT c.id,c.document_id,c.page,c.text,c.ordinal,d.name
                FROM document_chunks c JOIN documents d ON d.id=c.document_id
                WHERE d.owner=? AND d.id IN ({marks}) ORDER BY d.created,c.ordinal''', (owner,*ids))]

    def retrieve(self, owner, ids, question, overview=False):
        chunks = self.chunks(owner,ids)
        query = set(terms(question))
        bags = [Counter(terms(c['text'])) for c in chunks]
        avg = sum(sum(b.values()) for b in bags) / max(1,len(bags)) or 1
        df = Counter(t for b in bags for t in b)
        scores=[]
        for chunk,bag in zip(chunks,bags):
            score=0
            for t in query:
                f=bag.get(t,0)
                if f:
                    idf=math.log(1+(len(bags)-df[t]+.5)/(df[t]+.5))
                    score += idf * f * 2.2 / (f+1.2*(.25+.75*sum(bag.values())/avg))
            if score>0: scores.append((score,chunk))
        scores.sort(key=lambda p:p[0],reverse=True)
        found=[chunk for _,chunk in scores[:8]]
        if overview:
            # Always sample each selected document; large files still use a bounded subset.
            firsts=[next(c for c in chunks if c['document_id']==did) for did in dict.fromkeys(ids) if any(c['document_id']==did for c in chunks)]
            found = list({c['id']:c for c in firsts+found+chunks[:8]}.values())[:8]
        return found

    def excerpt(self,owner,chunk_id):
        with self.store.connect() as db:
            row=db.execute('''SELECT c.id,c.page,c.text,d.name,c.document_id FROM document_chunks c
                              JOIN documents d ON d.id=c.document_id WHERE c.id=? AND d.owner=?''',(chunk_id,owner)).fetchone()
        if not row: raise ChatError('Source not found (it may have been deleted).')
        return dict(row)

    def create_tasks(self,owner,data):
        if data.get('confirmed') is not True:
            raise ChatError('Review and confirm the tasks before saving.',422)
        key=data.get('idempotency_key')
        if not isinstance(key,str) or not 8 <= len(key) <= 100:
            raise ChatError('A confirmation key is required.',422)
        items=data.get('tasks')
        if not isinstance(items,list) or not 1 <= len(items) <= 10:
            raise ChatError('Confirm 1–10 tasks at a time.',422)
        digest=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            saved=db.execute('SELECT digest,result FROM confirmations WHERE owner=? AND key=?',(owner,key)).fetchone()
            if saved:
                if saved['digest']!=digest: raise ChatError('Confirmation key reused with different tasks.',409)
                return json.loads(saved['result'])
            if db.execute('SELECT count(*) FROM tasks WHERE owner=?',(owner,)).fetchone()[0]+len(items)>500:
                raise ChatError('Task limit reached (500). Delete old tasks first.',409)
            ids=[]
            for item in items:
                task=task_fields(item)
                source=item.get('source','')
                if not isinstance(source,str) or len(source)>500: raise ChatError('Source label is too long.',422)
                tid=uuid4().hex; ids.append(tid)
                db.execute('''INSERT INTO tasks(id,owner,title,notes,due_at,timezone,source,created)
                              VALUES(?,?,?,?,?,?,?,?)''',(tid,owner,task['title'],task['notes'],task['due_at'],task['timezone'],source,time.time()))
                if task['remind_at']:
                    db.execute('INSERT INTO reminders(id,task_id,owner,scheduled_at,status) VALUES(?,?,?,?,?)',
                               (uuid4().hex,tid,owner,task['remind_at'],'scheduled'))
                self.event(db,owner,'task_created',task['title'])
            db.execute('INSERT INTO confirmations VALUES(?,?,?,?)',(owner,key,digest,json.dumps(ids)))
        return ids

    def tasks(self,owner):
        with self.store.connect() as db:
            tasks=[dict(r) for r in db.execute('SELECT * FROM tasks WHERE owner=? ORDER BY created DESC',(owner,))]
            for task in tasks:
                task.pop('owner',None)
                task['reminders']=[dict(r) for r in db.execute('SELECT scheduled_at,status,delivered_at FROM reminders WHERE task_id=? ORDER BY rowid DESC',(task['id'],))]
        return tasks

    def update_task(self,owner,tid,data):
        if not isinstance(data.get('version'),int) or isinstance(data.get('version'),bool):
            raise ChatError('Refresh this task before updating it.',422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(tid,owner)).fetchone()
            if not row: raise ChatError('Task not found.')
            if row['version']!=data['version']: raise ChatError('Task changed in another window. Refresh and retry.',409)
            status=data.get('status',row['status'])
            if status not in ('pending','completed'): raise ChatError('Invalid task status.',422)
            if 'title' in data:
                task=task_fields(data)
                if task['remind_at'] and status=='completed':
                    raise ChatError('Reopen the task before scheduling a new reminder.',422)
                db.execute('UPDATE tasks SET title=?,notes=?,due_at=?,timezone=? WHERE id=?',
                           (task['title'],task['notes'],task['due_at'],task['timezone'],tid))
                db.execute("UPDATE reminders SET status='cancelled' WHERE task_id=? AND status='scheduled'",(tid,))
                if task['remind_at'] and status=='pending':
                    db.execute('INSERT INTO reminders(id,task_id,owner,scheduled_at,status) VALUES(?,?,?,?,?)',
                               (uuid4().hex,tid,owner,task['remind_at'],'scheduled'))
            db.execute('UPDATE tasks SET status=?,version=version+1 WHERE id=?',(status,tid))
            if status=='completed':
                db.execute("UPDATE reminders SET status='cancelled' WHERE task_id=? AND status='scheduled'",(tid,))
            self.event(db,owner,'task_'+('completed' if status=='completed' else 'updated'),data.get('title',row['title']))

    def delete_task(self,owner,tid):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT title FROM tasks WHERE id=? AND owner=?',(tid,owner)).fetchone()
            if not row: raise ChatError('Task not found.')
            db.execute('DELETE FROM tasks WHERE id=?',(tid,))
            db.execute('DELETE FROM notifications WHERE task_id=? AND owner=?',(tid,owner))
            self.event(db,owner,'task_deleted',row['title'])

    def inbox(self,owner):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,task_id,title,created,read_at FROM notifications WHERE owner=? ORDER BY created DESC LIMIT 100',(owner,))]

    def read_notification(self,owner,nid):
        with self.store.connect() as db:
            if not db.execute('UPDATE notifications SET read_at=? WHERE id=? AND owner=?',(time.time(),nid,owner)).rowcount:
                raise ChatError('Notification not found.')

    def events(self,owner):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT action,title,created FROM workflow_events WHERE owner=? ORDER BY id DESC LIMIT 100',(owner,))]

    def worker_status(self):
        with self.store.connect() as db:
            row=db.execute("SELECT seen FROM worker_health WHERE name='worker'").fetchone()
        return {'online':bool(row and time.time()-row['seen']<90)}

    def deliver_due(self, now=None):
        now=time.time() if now is None else now
        iso=datetime.fromtimestamp(now,timezone.utc).isoformat()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute('''SELECT r.id,r.owner,r.task_id,t.title FROM reminders r JOIN tasks t ON t.id=r.task_id
                               WHERE r.status='scheduled' AND r.scheduled_at<=? AND t.status='pending' ''',(iso,)).fetchall()
            for r in rows:
                db.execute('INSERT OR IGNORE INTO notifications(id,owner,reminder_id,task_id,title,created) VALUES(?,?,?,?,?,?)',
                           (uuid4().hex,r['owner'],r['id'],r['task_id'],r['title'],now))
                db.execute("UPDATE reminders SET status='delivered',delivered_at=? WHERE id=?",(now,r['id']))
                self.event(db,r['owner'],'reminder_delivered',r['title'])
        return len(rows)
