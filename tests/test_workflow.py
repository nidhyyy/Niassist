from datetime import datetime, timezone
from io import BytesIO
import json
import time
from unittest.mock import patch

import pytest

from app import create_app
from ai import workflow_ai
from ai.gemini_client import AssistantError
from conversations import ChatError
from tests.support import make_client
from worker import process_document, tick

TEXT=b'Placement notice. Applicants must submit their resume and college ID by 30 September 2026. Eligible students need a CGPA of 7.0 or above. Interviews will take place in the main auditorium.'


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING':True,'SECRET_KEY':'workflow-test','DATABASE':tmp_path/'test.db'})


def owner(app,client):
    with client.session_transaction() as session:
        return 'user:'+app.extensions['accounts'].user(session['login'])['id']


def upload(client,raw=TEXT,name='notice.txt'):
    return client.post('/api/documents',data={'file':(BytesIO(raw),name)},content_type='multipart/form-data')


def ready(app,client):
    response=upload(client)
    assert response.status_code==202
    assert process_document(app.extensions['workflow'])
    return response.json['document']['id']


def iso(seconds):
    return datetime.fromtimestamp(time.time()+seconds,timezone.utc).isoformat()


def task_payload(**fields):
    task=dict(title='Submit resume',notes='Review the notice',timezone='Asia/Kolkata',due_at=iso(7200),remind_at=iso(3600))
    task.update(fields)
    return dict(confirmed=True,idempotency_key='confirmation-123',tasks=[task])


def test_upload_processing_and_private_sources(app):
    client,other=make_client(app),make_client(app)
    did=ready(app,client)
    docs=client.get('/api/documents').json['documents']
    assert docs[0]['status']=='ready'
    assert other.get('/api/documents').json['documents']==[]
    assert other.delete('/api/documents/'+did).status_code==404
    chunks=app.extensions['workflow'].retrieve(owner(app,client),[did],'What is the eligibility CGPA?')
    assert chunks and 'CGPA' in chunks[0]['text'] and chunks[0]['page'] is None
    assert client.get('/api/documents/sources/'+chunks[0]['id']).status_code==200
    assert other.get('/api/documents/sources/'+chunks[0]['id']).status_code==404
    assert other.post('/api/documents/ask',json={'document_ids':[did],'question':'eligibility'}).status_code==404
    assert client.delete('/api/documents/'+did).status_code==200
    assert client.get('/api/documents/sources/'+chunks[0]['id']).status_code==404


@pytest.mark.parametrize(
    'raw,name',
    [
        (b'bad', 'fake.pdf'),
        (b'hello', 'virus.exe'),
        (b'\xff\xfe', 'broken.txt'),
        (b'x' * (5 * 1024 * 1024 + 1), 'large.txt'),
    ],
    ids=[
        'invalid-pdf',
        'unsupported-extension',
        'invalid-utf8',
        'oversized-file',
    ],
)
def test_invalid_uploads(app, raw, name):
    assert upload(make_client(app), raw, name).status_code == 422


def test_large_request(app):
    client=make_client(app)
    assert upload(client,b'x'*(6*1024*1024+1),'big.txt').status_code==413


def test_failed_ingestion_retry_and_worker_restart(app):
    client=make_client(app);workflow=app.extensions['workflow']
    did=upload(client,b'abc','tiny.txt').json['document']['id']
    tick(workflow)
    assert workflow.document(owner(app,client),did)['status']=='failed'
    assert client.post('/api/documents/'+did+'/retry').status_code==200
    with workflow.store.connect() as db:
        db.execute("UPDATE documents SET raw=?,status='processing',lease_until=0 WHERE id=?",(TEXT,did))
    tick(workflow)
    assert workflow.document(owner(app,client),did)['status']=='ready'
    assert workflow.worker_status()['online']


def test_worker_does_not_restore_document_deleted_during_processing(app):
    client=make_client(app);workflow=app.extensions['workflow']
    did=upload(client).json['document']['id']
    def delete_during(*args,**kwargs):
        workflow.delete_document(owner(app,client),did)
        raise RuntimeError('deleted')
    with patch('worker.subprocess.run',side_effect=delete_during):
        process_document(workflow)
    assert workflow.documents(owner(app,client))==[]


def test_grounded_answer_and_absence_of_evidence(app):
    client=make_client(app);did=ready(app,client)
    chunks=app.extensions['workflow'].retrieve(owner(app,client),[did],'CGPA')
    response=workflow_ai.Answer(supported=True,claims=[{'text':'You need a CGPA of at least 7.0.','citations':[{'chunk_id':chunks[0]['id'],'quote':'CGPA of 7.0 or above'}]}])
    with patch('ai.workflow_ai.structured',return_value=response):
        result=client.post('/api/documents/ask',json={'document_ids':[did],'question':'What CGPA is required?'})
    assert result.status_code==200 and result.json['supported']
    assert result.json['claims'][0]['citations'][0]['name']=='notice.txt'
    with patch('ai.workflow_ai.structured') as model:
        result=client.post('/api/documents/ask',json={'document_ids':[did],'question':'Spaceship quantum flux?'})
    assert result.json['supported'] is False
    model.assert_not_called()


def test_invalid_or_foreign_citation_rejected():
    sources=[dict(id='allowed',name='notice',text='Submit your resume tomorrow.',page=1)]
    for ref in [dict(chunk_id='foreign',quote='Submit your resume'),dict(chunk_id='allowed',quote='pay 100 dollars')]:
        response=workflow_ai.Answer(supported=True,claims=[{'text':'Made up','citations':[ref]}])
        with patch('ai.workflow_ai.structured',return_value=response),pytest.raises(AssistantError):
            workflow_ai.answer_question('What?',sources)


def test_suggestions_never_save_without_confirmation(app):
    client=make_client(app);did=ready(app,client)
    chunks=app.extensions['workflow'].retrieve(owner(app,client),[did],'resume',overview=True)
    response=workflow_ai.Suggestions(tasks=[{'title':'Submit resume','notes':'Check it first','deadline_hint':'31 December 2099',
        'citations':[{'chunk_id':chunks[0]['id'],'quote':'submit their resume and college ID'}]}])
    with patch('ai.workflow_ai.structured',return_value=response):
        result=client.post('/api/tasks/suggest',json={'document_ids':[did]})
    assert result.status_code==200 and result.json['tasks'][0]['deadline_hint']==''
    assert client.get('/api/tasks').json['tasks']==[]
    payload=task_payload();payload['confirmed']=False
    assert client.post('/api/tasks',json=payload).status_code==422


def test_confirm_duplicate_delivery_completion_and_read(app):
    client=make_client(app);workflow=app.extensions['workflow'];payload=task_payload()
    one=client.post('/api/tasks',json=payload)
    two=client.post('/api/tasks',json=payload)
    assert one.status_code==two.status_code==201 and one.json==two.json
    assert len(client.get('/api/tasks').json['tasks'])==1
    tid=one.json['ids'][0]
    payload['tasks'][0]['title']='Different'
    assert client.post('/api/tasks',json=payload).status_code==409
    assert workflow.deliver_due(time.time()+3700)==1
    assert workflow.deliver_due(time.time()+3700)==0
    inbox=client.get('/api/notifications').json['notifications']
    assert len(inbox)==1 and inbox[0]['read_at'] is None
    assert client.post('/api/notifications/'+inbox[0]['id']+'/read').status_code==200
    assert client.get('/api/notifications').json['notifications'][0]['read_at'] is not None
    assert client.patch('/api/tasks/'+tid,json={'version':1,'status':'completed'}).status_code==200
    assert client.patch('/api/tasks/'+tid,json={'version':1,'status':'pending'}).status_code==409
    assert len(client.get('/api/activity').json['events'])>=3


def test_cancelled_reminder_and_task_isolation(app):
    client,other=make_client(app),make_client(app)
    tid=client.post('/api/tasks',json=task_payload()).json['ids'][0]
    assert other.get('/api/tasks').json['tasks']==[]
    assert other.patch('/api/tasks/'+tid,json={'version':1,'status':'completed'}).status_code==404
    assert other.delete('/api/tasks/'+tid).status_code==404
    assert client.patch('/api/tasks/'+tid,json={'version':1,'status':'completed'}).status_code==200
    assert app.extensions['workflow'].deliver_due(time.time()+8000)==0
    assert client.get('/api/tasks').json['tasks'][0]['reminders'][0]['status']=='cancelled'
    assert client.delete('/api/tasks/'+tid).status_code==200


@pytest.mark.parametrize('fields',[{'remind_at':'2020-01-01T10:00:00Z'},{'due_at':'2028-01-01T10:00'},
    {'timezone':'invalid/zone'},{'title':''},{'due_at':iso(3600),'remind_at':iso(7200)}])
def test_task_validation(app,fields):
    client=make_client(app)
    assert client.post('/api/tasks',json=task_payload(**fields)).status_code==422
    assert client.get('/api/tasks').json['tasks']==[]


def test_all_workflow_routes_require_login_and_csrf(app):
    client=app.test_client()
    for path in ['/api/documents','/api/tasks','/api/notifications','/api/activity']:
        assert client.get(path).status_code==401
        assert client.post(path).status_code==401
    assert client.get('/workspace').status_code==302
    client=make_client(app)
    assert client.get('/workspace').status_code==200
    assert client.post('/api/tasks',json=task_payload(),headers={'X-CSRF-Token':'bad'}).status_code==403


def test_chat_suggestion_ownership(app):
    client,other=make_client(app),make_client(app)
    cid=client.post('/api/chats').json['id']
    assert other.post('/api/tasks/suggest',json={'conversation_id':cid}).status_code==404
    assert client.post('/api/tasks/suggest',json={'conversation_id':cid}).json['tasks']==[]


def test_pdf_page_numbers_and_scanned_pdf(app):
    # In-memory test fixture: a minimal text-based PDF, plus a blank scanned-like page.
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(612,792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 72 720 Td (Submit resume by September 30. Minimum CGPA is 7.0.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(content)
    buf=BytesIO();writer.write(buf)
    client=make_client(app);did=upload(client,buf.getvalue(),'notice.pdf').json['document']['id']
    process_document(app.extensions['workflow'])
    chunks=app.extensions['workflow'].retrieve(owner(app,client),[did],'CGPA')
    assert chunks[0]['page']==1 and '7.0' in chunks[0]['text']
    blank=PdfWriter();blank.add_blank_page(612,792);buf=BytesIO();blank.write(buf)
    did=upload(client,buf.getvalue(),'scan.pdf').json['document']['id'];process_document(app.extensions['workflow'])
    assert app.extensions['workflow'].document(owner(app,client),did)['status']=='failed'


def test_concurrent_workers_deliver_exactly_once(app):
    from concurrent.futures import ThreadPoolExecutor
    client=make_client(app);client.post('/api/tasks',json=task_payload())
    workflow=app.extensions['workflow'];future=time.time()+4000
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts=list(pool.map(lambda _:workflow.deliver_due(future),range(2)))
    assert sum(counts)==1
    assert len(client.get('/api/notifications').json['notifications'])==1


def test_edit_reschedules_and_normalizes_offset(app):
    client=make_client(app)
    tid=client.post('/api/tasks',json=task_payload()).json['ids'][0]
    revised=task_payload(remind_at=iso(5400))['tasks'][0]
    response=client.patch('/api/tasks/'+tid,json={**revised,'version':1})
    assert response.status_code==200
    task=client.get('/api/tasks').json['tasks'][0]
    assert [r['status'] for r in task['reminders']]==['scheduled','cancelled']
    assert app.extensions['workflow'].deliver_due(time.time()+4000)==0
    assert app.extensions['workflow'].deliver_due(time.time()+6000)==1
    from workflow_store import stamp
    assert stamp('2026-09-30T17:00:00+05:30','due')=='2026-09-30T11:30:00+00:00'


def test_full_document_to_action_flow(app):
    client=make_client(app);did=ready(app,client);workflow=app.extensions['workflow']
    chunk=workflow.retrieve(owner(app,client),[did],'resume')[0]
    model=workflow_ai.Suggestions(tasks=[dict(title='Submit resume and ID',notes='Check eligibility before applying.',
        deadline_hint='30 September 2026',citations=[dict(chunk_id=chunk['id'],quote='submit their resume and college ID')])])
    with patch('ai.workflow_ai.structured',return_value=model):
        suggested=client.post('/api/tasks/suggest',json={'document_ids':[did]}).json['tasks'][0]
    assert not client.get('/api/tasks').json['tasks']
    payload=task_payload(title=suggested['title'],notes=suggested['notes'])
    result=client.post('/api/tasks',json=payload)
    assert result.status_code==201
    assert workflow.deliver_due(time.time()+3700)==1
    assert client.get('/api/notifications').json['notifications'][0]['title']=='Submit resume and ID'
