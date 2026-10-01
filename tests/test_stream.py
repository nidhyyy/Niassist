from tests.support import make_client
import json
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest
import httpx
from app import create_app
from ai.gemini_client import AssistantError, stream_gemini


def decode(response):
    return [json.loads(line) for line in response.data.splitlines()]


def test_stream_is_incremental_and_has_timings(caplog):
    caplog.set_level('INFO')
    generated = []
    def source(_text):
        generated.append(1)
        yield 'Hello '
        generated.append(2)
        yield 'world'
    with patch('app.stream_gemini', source):
        response = make_client(create_app()).post('/command/stream', json={'text': 'hi'}, buffered=False)
        assert generated == []
        events = decode(response)
    assert [e['type'] for e in events] == ['start', 'delta', 'delta', 'done']
    assert ''.join(e.get('text', '') for e in events) == 'Hello world'
    assert events[-1]['total_ms'] >= events[-1]['first_text_ms'] >= 0
    assert response.headers['X-Accel-Buffering'] == 'no'
    assert 'outcome=completed' in caplog.text


@pytest.mark.parametrize('partial', [False, True])
def test_stream_failure_is_terminal(partial):
    def source(_text):
        if partial:
            yield 'Partial'
        raise AssistantError('AI_TIMEOUT', 'Try again.', 504)
    with patch('app.stream_gemini', source):
        events = decode(make_client(create_app()).post('/command/stream', json={'text': 'hi'}))
    assert events[-1]['type'] == 'error'
    assert events[-1]['code'] == 'AI_TIMEOUT'
    assert all(e['type'] != 'done' for e in events)


def test_stream_validation():
    with patch('app.stream_gemini') as mocked:
        response = make_client(create_app()).post('/command/stream', json={'text': ''})
    assert response.status_code == 422
    mocked.assert_not_called()


def test_disconnect_closes_provider():
    closed = []
    def source(_text):
        try:
            yield 'hello'
            yield 'world'
        finally:
            closed.append(True)
    with patch('app.stream_gemini', source):
        response = make_client(create_app()).post('/command/stream', json={'text': 'hi'}, buffered=False)
        iterator = iter(response.response)
        next(iterator)
        next(iterator)
        response.close()
    assert closed == [True]


@pytest.mark.parametrize('mode', ['ok', 'empty', 'timeout'])
def test_sdk_stream(monkeypatch, mode):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake')
    fake = MagicMock()
    def chunks():
        yield SimpleNamespace(text=None)
        if mode == 'timeout':
            raise httpx.ReadTimeout('private detail')
        if mode == 'ok':
            yield SimpleNamespace(text='Hello ')
            yield SimpleNamespace(text='world')
    fake.models.generate_content_stream.return_value = chunks()
    with patch('ai.gemini_client.genai.Client') as factory:
        factory.return_value.__enter__.return_value = fake
        if mode == 'ok':
            assert list(stream_gemini('hi')) == ['Hello ', 'world']
        else:
            with pytest.raises(AssistantError) as caught:
                list(stream_gemini('hi'))
            assert caught.value.code == ('AI_TIMEOUT' if mode == 'timeout' else 'AI_EMPTY_RESPONSE')
    factory.return_value.__exit__.assert_called_once()
