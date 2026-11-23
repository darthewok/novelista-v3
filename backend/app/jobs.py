from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app import events
from app.enums import JobStatus

log = logging.getLogger("novelista.jobs")

MAX_FINISHED = 250


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Job:
    id: str
    book_id: str
    kind: str
    status: JobStatus = JobStatus.queued
    created_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    error: str | None = None
    chapter: int | None = None
    batch_id: str | None = None
    _task: asyncio.Task[Any] | None = field(default=None, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "book_id": self.book_id,
            "kind": self.kind,
            "status": self.status.value,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "chapter": self.chapter,
            "batch_id": self.batch_id,
        }


@dataclass
class BatchState:
    id: str
    book_id: str
    chapters: list[int]
    job_ids: list[str]
    pause_requested: bool = False
    cancel_requested: bool = False
    stopped_reason: str | None = None


_JOBS: dict[str, Job] = {}
_BATCHES: dict[str, BatchState] = {}
_SEMAPHORE_BY_LOOP: dict[int, asyncio.Semaphore] = {}


def _get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    sem = _SEMAPHORE_BY_LOOP.get(lid)
    if sem is None:
        sem = asyncio.Semaphore(1)
        _SEMAPHORE_BY_LOOP[lid] = sem
    return sem


def _trim_finished() -> None:
    terminal = [
        j
        for j in _JOBS.values()
        if j.status in (JobStatus.done, JobStatus.error, JobStatus.cancelled)
    ]
    if len(terminal) <= MAX_FINISHED:
        return
    terminal.sort(key=lambda j: j.created_at)
    for j in terminal[: len(terminal) - MAX_FINISHED]:
        _JOBS.pop(j.id, None)


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def list_active_jobs(book_id: str | None = None) -> list[Job]:
    out = [
        j
        for j in _JOBS.values()
        if j.status in (JobStatus.queued, JobStatus.running, JobStatus.paused)
        and (book_id is None or j.book_id == book_id)
    ]
    out.sort(key=lambda j: j.created_at)
    return out


def has_active(book_id: str, chapter: int | None = None) -> bool:
    for j in _JOBS.values():
        if j.book_id != book_id:
            continue
        if j.status not in (JobStatus.queued, JobStatus.running):
            continue
        if chapter is None or j.chapter == chapter or j.chapter is None:
            return True
    return False


def create_batch(book_id: str, chapters: list[int]) -> BatchState:
    bid = str(uuid.uuid4())
    b = BatchState(id=bid, book_id=book_id, chapters=list(chapters), job_ids=[])
    _BATCHES[bid] = b
    return b


def get_batch(batch_id: str) -> BatchState | None:
    return _BATCHES.get(batch_id)


def pause_batch(batch_id: str) -> bool:
    b = _BATCHES.get(batch_id)
    if not b:
        return False
    b.pause_requested = True
    return True


def cancel_batch(batch_id: str) -> int:
    b = _BATCHES.get(batch_id)
    if not b:
        return 0
    b.cancel_requested = True
    b.stopped_reason = "Cancelled by user"
    count = 0
    for jid in b.job_ids:
        if cancel_job(jid):
            count += 1
    return count


def enqueue(
    *,
    book_id: str,
    kind: str,
    fn: Callable[[Job], Awaitable[Any]],
    chapter: int | None = None,
    batch_id: str | None = None,
) -> Job:
    jid = str(uuid.uuid4())
    job = Job(id=jid, book_id=book_id, kind=kind, chapter=chapter, batch_id=batch_id)
    _JOBS[jid] = job
    if batch_id and batch_id in _BATCHES:
        _BATCHES[batch_id].job_ids.append(jid)

    async def _runner() -> None:
        sem = _get_semaphore()
        try:
            async with sem:
                if job._cancel_requested:
                    job.status = JobStatus.cancelled
                    job.finished_at = _now_iso()
                    events.publish(jid, {"type": "cancelled"})
                    return
                if batch_id and batch_id in _BATCHES:
                    b = _BATCHES[batch_id]
                    if b.cancel_requested or b.pause_requested or b.stopped_reason:
                        job.status = JobStatus.cancelled
                        job.error = b.stopped_reason or "Batch paused"
                        job.finished_at = _now_iso()
                        events.publish(jid, {"type": "cancelled", "message": job.error})
                        return
                job.status = JobStatus.running
                events.publish(jid, {"type": "started", "kind": kind, "chapter": chapter})
                await fn(job)
                if job._cancel_requested:
                    job.status = JobStatus.cancelled
                    job.finished_at = _now_iso()
                    events.publish(jid, {"type": "cancelled"})
                else:
                    job.status = JobStatus.done
                    job.finished_at = _now_iso()
                    events.publish(jid, {"type": "done"})
        except asyncio.CancelledError:
            job.status = JobStatus.cancelled
            job.finished_at = _now_iso()
            events.publish(jid, {"type": "cancelled"})
        except Exception as exc:
            log.error("Job %s (%s) failed: %s\n%s", jid, kind, exc, traceback.format_exc())
            job.status = JobStatus.error
            job.error = str(exc) or exc.__class__.__name__
            job.finished_at = _now_iso()
            if batch_id and batch_id in _BATCHES and not _BATCHES[batch_id].stopped_reason:
                _BATCHES[batch_id].stopped_reason = f"Chapter {chapter} failed: {job.error}"
            events.publish(jid, {"type": "error", "message": job.error})
        finally:
            _trim_finished()

    job._task = asyncio.create_task(_runner(), name=f"job-{kind}-{jid[:8]}")
    return job


def cancel_job(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if not job:
        return False
    if job.status in (JobStatus.done, JobStatus.error, JobStatus.cancelled):
        return False
    job._cancel_requested = True
    if job.status == JobStatus.queued:
        job.status = JobStatus.cancelled
        job.finished_at = _now_iso()
        events.publish(job.id, {"type": "cancelled"})
    if job._task and not job._task.done():
        job._task.cancel()
    return True


async def cancel_book_jobs(book_id: str) -> int:
    cancelled = 0
    tasks: list[asyncio.Task[Any]] = []
    for j in list(_JOBS.values()):
        if j.book_id == book_id and j.status in (JobStatus.queued, JobStatus.running):
            if cancel_job(j.id):
                cancelled += 1
            if j._task and not j._task.done():
                tasks.append(j._task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return cancelled


def clear_all() -> None:
    for j in list(_JOBS.values()):
        if j._task and not j._task.done():
            j._task.cancel()
    _JOBS.clear()
    _BATCHES.clear()
