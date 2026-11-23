from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.routing import Match

from app import llm_client
from app.config import frontend_dist_dir
from app.routers import audit, books, chapters, foundation, outline, seed, settings, stream, uploads

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#8C4A27"/>'
    '<path d="M9 8h11a3 3 0 0 1 3 3v13H11a2 2 0 0 0-2 2V8z" fill="#FAF7F0"/>'
    '<path d="M11 22h12v2H11a2 2 0 0 1 0-4z" fill="#E6D7C3"/>'
    "</svg>"
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await llm_client.close_client()


app = FastAPI(title="Novelista v3", version="3.0.0", lifespan=lifespan)

for r in (
    settings.router,
    books.router,
    seed.router,
    uploads.router,
    outline.router,
    chapters.router,
    audit.router,
    foundation.router,
    stream.router,
):
    app.include_router(r)


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    dist = frontend_dist_dir()
    ico = dist / "favicon.ico"
    if ico.exists():
        return FileResponse(ico)
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


class _SpaFallbackRoute(APIRoute):
    def matches(self, scope: dict[str, Any]) -> tuple[Match, dict[str, Any]]:
        path = scope.get("path", "")
        if path == "/api" or path.startswith("/api/"):
            return Match.NONE, {}
        return super().matches(scope)


_spa_router = APIRouter(route_class=_SpaFallbackRoute)


@_spa_router.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> Response:
    dist = frontend_dist_dir()
    if not dist.exists():
        return Response(
            content="Novelista v3 frontend build not found. Run `npm run build` in frontend/.",
            media_type="text/plain",
            status_code=200,
        )
    candidate = (dist / full_path).resolve()
    if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
        return FileResponse(candidate)
    index = dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return Response(content="index.html missing from frontend/dist.", media_type="text/plain", status_code=200)


app.include_router(_spa_router)
