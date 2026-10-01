import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from app import create_app
from scripts.backup import backup
from settings import data_directory, database_path


def test_configured_shared_storage_and_backup(tmp_path, monkeypatch):
    monkeypatch.setenv('NIASSIST_DATA_DIR', str(tmp_path / 'data'))
    assert database_path() == data_directory() / 'chats.sqlite3'
    app = create_app({'SECRET_KEY': 'test-key'})
    assert Path(app.extensions['conversations'].path) == database_path()
    user = app.extensions['accounts'].register('backup@example.test', 'Long-password-123')
    cid = app.extensions['conversations'].create('user:' + user['id'])
    secret = tmp_path / 'session-secret'
    secret.write_text('persistent-test-key')
    snapshot = backup(database_path(), tmp_path / 'backups', secret)
    with sqlite3.connect(snapshot / 'chats.sqlite3') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT id FROM chats').fetchone()[0] == cid
    assert (snapshot / 'session-secret').read_text() == 'persistent-test-key'


def test_missing_database_does_not_create_empty_backup(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / 'missing.db', tmp_path / 'backups')
    assert not (tmp_path / 'missing.db').exists()


def test_ready_tracks_worker_and_config_without_ai_request(tmp_path, monkeypatch):
    app = create_app({'DATABASE': tmp_path / 'test.db', 'SECRET_KEY': 'test'})
    client = app.test_client()
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    assert client.get('/health').status_code == 200
    assert client.get('/ready').status_code == 503
    monkeypatch.setenv('GEMINI_API_KEY', 'test-only-key')
    with patch.object(app.extensions['workflow'], 'worker_status', return_value={'online': True}), patch('app.ask_gemini') as model:
        result = client.get('/ready')
    assert result.status_code == 200
    model.assert_not_called()


def test_production_fails_closed_and_enforces_host(tmp_path, monkeypatch):
    monkeypatch.setenv('NIASSIST_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('NIASSIST_ENV', 'production')
    monkeypatch.delenv('COOKIE_SECURE', raising=False)
    with pytest.raises(RuntimeError):
        create_app()
    monkeypatch.setenv('COOKIE_SECURE', '1')
    monkeypatch.setenv('NIASSIST_TRUSTED_HOSTS', 'example.test')
    with pytest.raises(RuntimeError):
        create_app()  # No persistent key.
    monkeypatch.setenv('SECRET_KEY', 'test-secret-' * 4)
    app = create_app()
    assert app.config['SESSION_COOKIE_SECURE'] is True
    assert app.test_client().get('/health', base_url='https://evil.test').status_code == 400
    assert app.test_client().get('/health', base_url='https://example.test').status_code == 200


def test_proxy_hop_applies_only_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv('NIASSIST_PROXY_HOPS', '1')
    app = create_app({'SECRET_KEY':'test','DATABASE':tmp_path/'db'})
    @app.get('/test-proxy')
    def proxy_echo():
        from flask import request
        return {'ip':request.remote_addr,'scheme':request.scheme}
    result = app.test_client().get('/test-proxy', headers={'X-Forwarded-For':'203.0.113.9','X-Forwarded-Proto':'https'})
    assert result.json == {'ip':'203.0.113.9','scheme':'https'}
