from tests.support import make_client
from unittest.mock import patch

import pytest

from app import create_app
from ai.gemini_client import AssistantError


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    return make_client(app)


def test_home_and_health(client):
    assert client.get('/').status_code == 200
    assert client.get('/health').json == {'status': 'ok'}


@pytest.mark.parametrize('payload', [None, [], 'hello', {}, {'text': 5}, {'text': False},
                                    {'text': ''}, {'text': '   '}, {'text': 'a' * 4001}])
def test_invalid_input_never_calls_ai(client, payload):
    import json
    with patch('app.ask_gemini') as ask:
        result = client.post('/command', data=json.dumps(payload), content_type='application/json')
    assert result.status_code == 422
    ask.assert_not_called()


def test_malformed_and_non_json(client):
    assert client.post('/command', data='{', content_type='application/json').status_code == 400
    assert client.post('/command', data='hello').status_code == 415


def test_large_body(client):
    result = client.post('/command', json={'text': 'a' * 33000})
    assert result.status_code == 413
    assert result.json['error']['code'] == 'REQUEST_TOO_LARGE'


def test_success_trims_input_and_identifies_request(client):
    with patch('app.ask_gemini', return_value='Hello') as ask:
        result = client.post('/command', json={'text': ' Hi '})
    assert result.status_code == 200
    assert result.json['response'] == 'Hello'
    assert result.headers['X-Request-ID'] == result.json['request_id']
    ask.assert_called_once_with('Hi')


@pytest.mark.parametrize('status', [502, 503, 504])
def test_provider_errors(client, status):
    with patch('app.ask_gemini', side_effect=AssistantError('AI_ERROR', 'Try again.', status)):
        result = client.post('/command', json={'text': 'Hi'})
    assert result.status_code == status
    assert result.json['error']['message'] == 'Try again.'


def test_unexpected_errors_do_not_leak_secrets(client, caplog):
    with patch('app.ask_gemini', side_effect=RuntimeError('secret-key-value')):
        result = client.post('/command', json={'text': 'private prompt'})
    assert result.status_code == 500
    assert 'secret-key-value' not in result.text + caplog.text
    assert 'private prompt' not in caplog.text

