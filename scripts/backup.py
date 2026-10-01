"""Consistent SQLite snapshot plus session key. Run as an authorized OS user."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import sqlite3


def backup(database, directory, secret=None):
    database, directory = Path(database).resolve(), Path(directory).resolve()
    if not database.is_file():
        raise FileNotFoundError('Database does not exist; refusing to create an empty backup.')
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ')
    snapshot.mkdir(mode=0o700)
    try:
        source = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        target = sqlite3.connect(snapshot / 'chats.sqlite3')
        try:
            source.backup(target)
            result = target.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise RuntimeError('Snapshot failed integrity check.')
        finally:
            target.close()
            source.close()
        (snapshot / 'chats.sqlite3').chmod(0o600)
        if secret and Path(secret).is_file():
            shutil.copyfile(secret, snapshot / 'session-secret')
            (snapshot / 'session-secret').chmod(0o600)
    except Exception:
        shutil.rmtree(snapshot)
        raise
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--secret')
    args = parser.parse_args()
    print(backup(args.database, args.destination, args.secret))
