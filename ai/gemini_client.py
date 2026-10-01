import os

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()


class AssistantError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def ask_gemini(prompt):
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key or api_key == 'your_api_key_here':
        raise AssistantError('AI_NOT_CONFIGURED', 'The assistant is not configured yet.', 503)
    try:
        # Lazy construction permits health checks and tests without a live key.
        # One attempt keeps request duration predictable; the user can retry.
        with genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)
        )) as client:
            response = client.models.generate_content(
                model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=2048),
            )
            text = response.text
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise AssistantError('AI_TIMEOUT', 'The assistant took too long. Please try again.', 504) from exc
    except errors.APIError as exc:
        if exc.code == 429:
            raise AssistantError('AI_BUSY', 'The assistant is busy. Please try again later.', 503) from exc
        raise AssistantError('AI_UNAVAILABLE', 'The AI service is temporarily unavailable.', 502) from exc
    except httpx.RequestError as exc:
        raise AssistantError('AI_UNAVAILABLE', 'Could not reach the AI service. Please try again.', 502) from exc
    if not text or not text.strip():
        raise AssistantError('AI_EMPTY_RESPONSE', 'No text answer was returned. Try rephrasing your request.', 502)
    return text.strip()


def stream_gemini(prompt, history=None):
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key or api_key == 'your_api_key_here':
        raise AssistantError('AI_NOT_CONFIGURED', 'The assistant is not configured yet.', 503)
    try:
        # Lazy construction permits health checks and tests without a live key.
        # One attempt keeps request duration predictable; the user can retry.
        with genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)
        )) as client:
            chunks = client.models.generate_content_stream(
                model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
                contents=conversation_contents(prompt, history),
                config=types.GenerateContentConfig(max_output_tokens=2048),
            )
            has_text = False
            try:
                for chunk in chunks:
                    text = chunk.text
                    if text:
                        has_text = has_text or bool(text.strip())
                        yield text
            finally:
                close = getattr(chunks, 'close', None)
                if close:
                    close()
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise AssistantError('AI_TIMEOUT', 'The assistant took too long. Please try again.', 504) from exc
    except errors.APIError as exc:
        if exc.code == 429:
            raise AssistantError('AI_BUSY', 'The assistant is busy. Please try again later.', 503) from exc
        raise AssistantError('AI_UNAVAILABLE', 'The AI service is temporarily unavailable.', 502) from exc
    except httpx.RequestError as exc:
        raise AssistantError('AI_UNAVAILABLE', 'Could not reach the AI service. Please try again.', 502) from exc
    if not has_text:
        raise AssistantError('AI_EMPTY_RESPONSE', 'No text answer was returned. Try rephrasing your request.', 502)


def conversation_contents(prompt, history=None):
    contents = []
    for turn in history or []:
        contents.append(types.Content(role='user', parts=[types.Part(text=turn['question'])]))
        contents.append(types.Content(role='model', parts=[types.Part(text=turn['answer'])]))
    contents.append(types.Content(role='user', parts=[types.Part(text=prompt)]))
    return contents
