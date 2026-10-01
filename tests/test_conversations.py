from tests.support import make_client
import json
from unittest.mock import patch

import pytest

from app import create_app
from ai.gemini_client import AssistantError, conversation_contents


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': tmp_path / 'chats.db'})


def send(client, cid, question):
    return client.post('/command/stream', json={'text': question, 'conversation_id': cid})


def test_followup_context_persistence_and_separate_chat(app):
    client = make_client(app)
    cid = client.post('/api/chats').json['id']
    seen = []
    def answer(text, history):
        seen.append(history)
        yield 'REST answer' if text == 'REST?' else 'Followup answer'
    with patch('app.stream_gemini', answer):
        assert b'"done"' in send(client, cid, 'REST?').data
        assert b'"done"' in send(client, cid, 'Example?').data
        other = client.post('/api/chats').json['id']
        assert b'"done"' in send(client, other, 'Unrelated').data
    assert seen == [[], [{'question': 'REST?', 'answer': 'REST answer'}], []]
    assert len(client.get('/api/chats/' + cid).json['turns']) == 2
    # Reopen the same database, as a new application process would.
    from conversations import Conversations
    store = Conversations(app.extensions['conversations'].path)
    with client.session_transaction() as session:
        owner = 'user:' + app.extensions['accounts'].user(session['login'])['id']
    assert len(store.read(owner, cid)) == 2


def test_ownership_and_delete(app):
    a, b = make_client(app), make_client(app)
    cid = a.post('/api/chats').json['id']
    assert b.get('/api/chats').json['chats'] == []
    assert b.get('/api/chats/' + cid).status_code == 404
    assert b.delete('/api/chats/' + cid).status_code == 404
    assert send(b, cid, 'hello').status_code == 404
    assert a.delete('/api/chats/' + cid).status_code == 200
    assert a.get('/api/chats/' + cid).status_code == 404


def test_failure_not_saved_and_lease_released(app):
    client = make_client(app)
    cid = client.post('/api/chats').json['id']
    def failure(text, history):
        yield 'Partial answer'
        raise AssistantError('AI_TIMEOUT', 'retry', 504)
    with patch('app.stream_gemini', failure):
        assert b'"error"' in send(client, cid, 'hello').data
    assert client.get('/api/chats/' + cid).json['turns'] == []
    assert client.delete('/api/chats/' + cid).status_code == 200


def test_simultaneous_requests_are_rejected_and_disconnect_unlocks(app):
    client = make_client(app)
    cid = client.post('/api/chats').json['id']
    def answer(text, history):
        yield 'part'
        yield 'two'
    with patch('app.stream_gemini', answer):
        response = send(client, cid, 'first')
        assert send(client, cid, 'second').status_code == 409
        assert client.delete('/api/chats/' + cid).status_code == 409
        response.close()
    assert client.delete('/api/chats/' + cid).status_code == 200


def test_history_bounds_and_roles(app):
    store = app.extensions['conversations']
    cid = store.create('owner')
    for index in range(12):
        token, _ = store.acquire('owner', cid)
        store.finish('owner', cid, token, str(index), 'answer')
    token, history = store.acquire('owner', cid)
    assert len(history) == 10
    assert history[0]['question'] == '2'
    contents = conversation_contents('next', history)
    assert [c.role for c in contents] == ['user', 'model'] * 10 + ['user']
    assert contents[-1].parts[0].text == 'next'
    store.release(cid, token)
    token, _ = store.acquire('owner', cid)
    store.finish('owner', cid, token, 'long', 'x' * 25000)
    token, history = store.acquire('owner', cid)
    assert history == []
    store.release(cid, token)


def test_cross_origin_mutation_rejected(app):
    result = make_client(app).post('/api/chats', headers={'Origin': 'https://evil.example'})
    assert result.status_code == 403
