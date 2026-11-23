from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # novelista-v3/
BACKEND_DIR = Path(__file__).resolve().parent.parent       # novelista-v3/backend/


def data_dir() -> Path:
    env = os.environ.get("NOVELISTA_DATA_DIR")
    p = Path(env).expanduser() if env else (BASE_DIR / "data")
    p.mkdir(parents=True, exist_ok=True)
    return p


def frontend_dist_dir() -> Path:
    env = os.environ.get("NOVELISTA_FRONTEND_DIST")
    return Path(env).expanduser() if env else (BASE_DIR / "frontend" / "dist")


DEFAULT_HOST = os.environ.get("NOVELISTA_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("NOVELISTA_PORT", "8000"))
