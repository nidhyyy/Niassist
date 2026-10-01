from flask import Blueprint, current_app, g, jsonify, request
from ai import workflow_ai
from conversations import ChatError
from workflow_store import MAX_UPLOAD

bp=Blueprint('workflow',__name__)


def store(): return current_app.extensions['workflow']
def owner(): return 'user:'+g.user['id']


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict): raise ChatError('Send a JSON object.',422)
    return data


def question(data,default=None):
    text=data.get('question',default)
    if not isinstance(text,str) or not 1<=len(text.strip())<=2000:
        raise ChatError('Enter a question of 1–2000 characters.',422)
    return text.strip()


@bp.get('/api/documents')
def documents(): return jsonify(documents=store().documents(owner()),worker=store().worker_status())


@bp.post('/api/documents')
def upload():
    file=request.files.get('file')
    if file is None: raise ChatError('Choose a PDF or TXT file.',422)
    raw=file.stream.read(MAX_UPLOAD+1)
    did=store().upload(owner(),file.filename,raw)
    return jsonify(document=store().document(owner(),did)),202


@bp.delete('/api/documents/<did>')
def delete_document(did):
    store().delete_document(owner(),did)
    return jsonify(deleted=True)


@bp.post('/api/documents/<did>/retry')
def retry_document(did):
    store().retry_document(owner(),did)
    return jsonify(queued=True)


@bp.get('/api/documents/sources/<chunk_id>')
def source(chunk_id): return jsonify(source=store().excerpt(owner(),chunk_id))


@bp.post('/api/documents/ask')
def ask():
    data=body(); text=question(data)
    chunks=store().retrieve(owner(),data.get('document_ids'),text,
        overview=any(w in text.lower() for w in ('summar','overview')))
    store().limit_ai(owner())
    return jsonify(**workflow_ai.answer_question(text,chunks),retrieved_chunks=len(chunks))


@bp.post('/api/tasks/suggest')
def suggest():
    data=body(); text=question(data,'Create a practical checklist of required actions and deadlines.')
    if data.get('conversation_id'):
        cid=data['conversation_id']
        if not isinstance(cid,str): raise ChatError('Invalid conversation.',422)
        turns=current_app.extensions['conversations'].read(owner(),cid)[-6:]
        chunks=[dict(id='chat-'+str(i),name='Conversation exchange '+str(i+1),page=None,
                     text=('User: '+t['question']+'\nAssistant: '+t['answer'])[:3000]) for i,t in enumerate(turns)]
    else:
        chunks=store().retrieve(owner(),data.get('document_ids'),text,overview=True)
    store().limit_ai(owner())
    result=workflow_ai.suggest_tasks(text,chunks)
    return jsonify(**result,sampled_excerpts=len(chunks))


@bp.get('/api/tasks')
def tasks(): return jsonify(tasks=store().tasks(owner()),worker=store().worker_status())


@bp.post('/api/tasks')
def create_tasks(): return jsonify(ids=store().create_tasks(owner(),body())),201


@bp.patch('/api/tasks/<tid>')
def update_task(tid):
    store().update_task(owner(),tid,body())
    return jsonify(updated=True)


@bp.delete('/api/tasks/<tid>')
def delete_task(tid):
    store().delete_task(owner(),tid)
    return jsonify(deleted=True)


@bp.get('/api/notifications')
def inbox(): return jsonify(notifications=store().inbox(owner()),worker=store().worker_status())


@bp.post('/api/notifications/<nid>/read')
def read_notification(nid):
    store().read_notification(owner(),nid)
    return jsonify(read=True)


@bp.get('/api/activity')
def activity(): return jsonify(events=store().events(owner()))
