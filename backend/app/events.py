from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

_TERMINAL_TYPES = {"done", "error", "cancelled"}
_TTL_SECONDS = 600.0  # retain terminal replay buffer for 10 min, then evict


@dataclass
class JobChannel:
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    meta_events: list[dict[str, Any]] = field(default_factory=list)
    token_chunks: list[str] = field(default_factory=list)
    terminal: bool = False
    finished_mono: float | None = None


_CHANNELS: dict[str, JobChannel] = {}


def _prune_expired() -> None:
    now = time.monotonic()
    stale = [
        jid
        for jid, ch in _CHANNELS.items()
        if ch.terminal and not ch.subscribers and ch.finished_mono is not None and (now - ch.finished_mono) > _TTL_SECONDS
    ]
    for jid in stale:
        _CHANNELS.pop(jid, None)


def _channel(job_id: str) -> JobChannel:
    _prune_expired()
    ch = _CHANNELS.get(job_id)
    if ch is None:
        ch = JobChannel()
        _CHANNELS[job_id] = ch
    return ch


def publish(job_id: str, event: dict[str, Any]) -> None:
    ch = _channel(job_id)
    etype = event.get("type", "")
    if etype == "token":
        ch.token_chunks.append(str(event.get("text", "")))
        # Cap token buffer at ~250k chars to bound memory on huge jobs
        if sum(len(t) for t in ch.token_chunks) > 250_000:
            joined = "".join(ch.token_chunks)[-120_000:]
            ch.token_chunks = [joined]
    else:
        if len(ch.meta_events) < 200:
            ch.meta_events.append(event)
    if etype in _TERMINAL_TYPES:
        ch.terminal = True
        ch.finished_mono = time.monotonic()

    for q in list(ch.subscribers):
        q.put_nowait(event)
        if etype in _TERMINAL_TYPES:
            q.put_nowait(None)


async def subscribe(job_id: str) -> AsyncIterator[dict[str, Any]]:
    ch = _channel(job_id)
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    # Replay past non-token lifecycle events & accumulated text so late/second tabs catch up seamlessly
    for ev in ch.meta_events:
        if ev.get("type") not in _TERMINAL_TYPES:
            q.put_nowait(ev)
    if ch.token_chunks:
        q.put_nowait({"type": "token", "text": "".join(ch.token_chunks)})
    for ev in ch.meta_events:
        if ev.get("type") in _TERMINAL_TYPES:
            q.put_nowait(ev)
    if ch.terminal:
        q.put_nowait(None)

    ch.subscribers.append(q)
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            yield item
    finally:
        if q in ch.subscribers:
            ch.subscribers.remove(q)
        _prune_expired()


def clear_all() -> None:
    _CHANNELS.clear()
