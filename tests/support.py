"""Real authenticated client for existing API regression tests."""
from uuid import uuid4


def make_client(app):
    client = app.test_client()
    accounts = app.extensions['accounts']
    user = accounts.register(uuid4().hex + '@example.test', 'test-password-123')
    with client.session_transaction() as session:
        session['login'] = accounts.new_session(user['id'])
        session['csrf'] = 'test-csrf'
    original = client.open
    def authenticated_open(*args, **kwargs):
        kwargs['headers'] = {'X-CSRF-Token': 'test-csrf', **kwargs.get('headers', {})}
        return original(*args, **kwargs)
    client.open = authenticated_open
    return client
