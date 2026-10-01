"""
tests/conftest.py
─────────────────
Global PyTest fixtures for Kamal Express test suite.
Enforces strict test database isolation so tests NEVER mutate or touch the
development / production SQLite database (`data/kamal_express.db`).
"""
import sys
import tempfile
from pathlib import Path
import pytest

# Ensure root directory is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import agents.appointments.db as db_module


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database():
    """
    Globally isolate the database for all pytest executions by redirecting
    `db_module.DB_PATH` to a dedicated temporary SQLite database.
    """
    temp_dir = tempfile.TemporaryDirectory()
    temp_db_path = Path(temp_dir.name) / "test_kamal_express.db"

    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = temp_db_path
    db_module.init_db(temp_db_path)

    yield temp_db_path

    # Restore original DB_PATH and cleanup temp directory
    db_module.DB_PATH = original_db_path
    try:
        temp_dir.cleanup()
    except Exception:
        pass
