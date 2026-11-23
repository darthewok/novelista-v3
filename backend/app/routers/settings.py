from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import llm_client
from app.settings import (
    DEFAULT_MODELS_BY_PROVIDER,
    TASK_ROLES,
    SettingsPatch,
    apply_settings_patch,
    masked_settings_dict,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict[str, Any]:
    return masked_settings_dict()


@router.put("")
def put_settings(body: SettingsPatch) -> dict[str, Any]:
    updated = apply_settings_patch(body)
    return masked_settings_dict(updated)


@router.get("/presets")
def get_presets() -> dict[str, Any]:
    return {"task_roles": list(TASK_ROLES), "defaults_by_provider": DEFAULT_MODELS_BY_PROVIDER}


@router.post("/test")
async def test_settings() -> dict[str, Any]:
    return await llm_client.test_connection()
