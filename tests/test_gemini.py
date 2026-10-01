from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from google.genai import errors

from ai.gemini_client import AssistantError, ask_gemini


def test_missing_key(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(AssistantError) as caught:
        ask_gemini('hello')
    assert caught.value.code == 'AI_NOT_CONFIGURED'


@pytest.mark.parametrize('failure,code', [
    (httpx.ReadTimeout('private detail'), 'AI_TIMEOUT'),
    (httpx.ConnectError('private detail'), 'AI_UNAVAILABLE'),
    (errors.APIError(429, {'error': {'message': 'quota details'}}), 'AI_BUSY'),
    (errors.APIError(403, {'error': {'message': 'key details'}}), 'AI_UNAVAILABLE'),
])
def test_provider_failure_translation(monkeypatch, failure, code):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    fake = MagicMock()
    fake.models.generate_content.side_effect = failure
    with patch('ai.gemini_client.genai.Client') as factory:
        factory.return_value.__enter__.return_value = fake
        with pytest.raises(AssistantError) as caught:
            ask_gemini('hello')
    assert caught.value.code == code


@pytest.mark.parametrize('text', [None, '', '  ', ' Answer '])
def test_response_handling(monkeypatch, text):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    fake = MagicMock()
    fake.models.generate_content.return_value = SimpleNamespace(text=text)
    with patch('ai.gemini_client.genai.Client') as factory:
        factory.return_value.__enter__.return_value = fake
        if text and text.strip():
            assert ask_gemini('hello') == 'Answer'
        else:
            with pytest.raises(AssistantError) as caught:
                ask_gemini('hello')
            assert caught.value.code == 'AI_EMPTY_RESPONSE'
        options = factory.call_args.kwargs['http_options']
        assert options.timeout == 20000
        assert options.retry_options.attempts == 1
