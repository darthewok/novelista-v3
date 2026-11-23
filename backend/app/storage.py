from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors="replace")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def safe_child(base: Path, name: str) -> Path:
    """Resolve a single-segment filename safely inside `base`.

    Raises ValueError on any traversal, path separator, drive letter, or empty name.
    """
    if not name or not isinstance(name, str):
        raise ValueError("Empty filename")
    # Reject both POSIX and Windows separators explicitly before Path parsing
    if "/" in name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Invalid filename")
    if name in {".", ".."} or name.startswith("_") or name.startswith("."):
        raise ValueError("Forbidden filename")
    cleaned = Path(name).name
    if cleaned != name:
        raise ValueError("Path traversal rejected")
    resolved_base = base.resolve()
    candidate = (resolved_base / cleaned).resolve()
    if not candidate.is_relative_to(resolved_base) or candidate == resolved_base:
        raise ValueError("Path escapes target directory")
    return candidate


def backup_prev(path: Path) -> Path | None:
    """Copy `path` to `<stem>.prev<suffix>` if non-empty. Returns backup path."""
    if not path.exists():
        return None
    text = read_text(path)
    if not text.strip():
        return None
    prev = path.with_name(f"{path.stem}.prev{path.suffix}")
    atomic_write_text(prev, text)
    return prev


def restore_prev(path: Path) -> str | None:
    """Swap `<stem>.prev<suffix>` back into `path` (backing up current to `.prev` first)."""
    prev = path.with_name(f"{path.stem}.prev{path.suffix}")
    if not prev.exists():
        return None
    prev_text = read_text(prev)
    if not prev_text.strip():
        return None
    cur_text = read_text(path)
    atomic_write_text(path, prev_text)
    if cur_text.strip():
        atomic_write_text(prev, cur_text)
    return prev_text


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def safe_slug(text: str, fallback: str = "book") -> str:
    s = _SLUG_RE.sub("-", (text or "").strip()).strip("-").lower()
    return s[:64] or fallback
