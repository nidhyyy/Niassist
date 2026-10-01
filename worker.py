"""Run separately: python worker.py. Durable ingestion and in-app reminder delivery."""
import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from conversations import Conversations
from workflow_store import WorkflowStore
from settings import database_path

log=logging.getLogger('niassist.worker')


def process_document(workflow):
    store=workflow.store
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT * FROM documents WHERE status='queued' OR (status='processing' AND lease_until<?) ORDER BY created LIMIT 1",(time.time(),)).fetchone()
        if not row: return False
        token=uuid4().hex
        db.execute("UPDATE documents SET status='processing',lease=?,lease_until=? WHERE id=?",(token,time.time()+120,row['id']))
    try:
        with tempfile.TemporaryDirectory(prefix='niassist-extract-') as tmp:
            source=Path(tmp)/('source.'+row['kind']); output=Path(tmp)/'result.json'
            source.write_bytes(row['raw'])
            subprocess.run([sys.executable,str(Path(__file__).with_name('document_parser.py')),
                            str(source),str(output),row['kind']],timeout=30,check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            result=json.loads(output.read_text(encoding='utf-8'))
    except subprocess.TimeoutExpired:
        result={'error':'Document processing exceeded 30 seconds. Try a smaller or simpler file.'}
    except Exception:
        result={'error':'Document processing failed. Check dependencies or retry a simpler file.'}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT lease FROM documents WHERE id=?',(row['id'],)).fetchone()
        if not current or current['lease']!=token: return True
        if result.get('error'):
            db.execute("UPDATE documents SET status='failed',error=?,lease=NULL,lease_until=0 WHERE id=?",(result['error'],row['id']))
            workflow.event(db,row['owner'],'document_failed',row['name'])
        else:
            db.execute('DELETE FROM document_chunks WHERE document_id=?',(row['id'],))
            for ordinal,chunk in enumerate(result['chunks']):
                db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?)',
                           (uuid4().hex,row['id'],ordinal,chunk['page'],chunk['text']))
            db.execute("UPDATE documents SET status='ready',error=NULL,lease=NULL,lease_until=0 WHERE id=?",(row['id'],))
            workflow.event(db,row['owner'],'document_ready',row['name'])
    return True


def tick(workflow):
    with workflow.store.connect() as db:
        db.execute("INSERT INTO worker_health VALUES('worker',?) ON CONFLICT(name) DO UPDATE SET seen=excluded.seen",(time.time(),))
    workflow.deliver_due()
    process_document(workflow)
    workflow.deliver_due()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',default=str(database_path()))
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    Path(args.database).parent.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    workflow=WorkflowStore(Conversations(args.database))
    log.info('Worker started. Processing documents and delivering in-app reminders every 5 seconds.')
    try:
        while True:
            try: tick(workflow)
            except Exception as exc:
                log.error('Worker cycle failed (%s). Pending jobs will be retried.',type(exc).__name__)
                if args.once: raise
            if args.once: break
            time.sleep(5)
    except KeyboardInterrupt:
        log.info('Worker stopped.')
