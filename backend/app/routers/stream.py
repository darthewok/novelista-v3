from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app import events, jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/active")
def get_active_jobs(book_id: str | None = None) -> list[dict[str, Any]]:
    return [j.to_dict() for j in jobs.list_active_jobs(book_id)]


@router.get("/{job_id}")
def get_job_status(job_id: str) -> dict[str, Any]:
    j = jobs.get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    return j.to_dict()


@router.post("/{job_id}/cancel")
def cancel_job_endpoint(job_id: str) -> dict[str, bool]:
    if not jobs.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"cancelled": jobs.cancel_job(job_id)}


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str) -> EventSourceResponse:
    j = jobs.get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")

    async def _gen():
        async for ev in events.subscribe(job_id):
            yield {"data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(_gen())
