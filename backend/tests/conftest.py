from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure backend/ is importable as app.*
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import Database, reset_db_instance
from app.main import create_app


@pytest.fixture()
def temp_db(tmp_path: Path) -> Database:
    db = Database(db_path=tmp_path / "test_scheduler.db")
    db.init_schema()
    reset_db_instance(db)
    yield db
    reset_db_instance(None)


@pytest.fixture()
def client(temp_db: Database) -> TestClient:
    app = create_app()
    with TestClient(app) as tc:
        yield tc
