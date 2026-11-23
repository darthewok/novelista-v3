from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import events, jobs, settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NOVELISTA_DATA_DIR", str(data_root))
    settings.reset_cache()
    events.clear_all()
    jobs.clear_all()
    yield data_root
    jobs.clear_all()
    events.clear_all()
    settings.reset_cache()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
