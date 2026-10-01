"""Shared web/worker storage settings; local defaults keep existing installs working."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(os.getenv('NIASSIST_ENV_FILE', str(ROOT / '.env')))


def data_directory():
    return Path(os.getenv('NIASSIST_DATA_DIR', str(ROOT / 'instance'))).expanduser().resolve()


def database_path():
    return Path(os.getenv('NIASSIST_DATABASE', str(data_directory() / 'chats.sqlite3'))).expanduser().resolve()
