from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import data_dir
from app.enums import ProviderName
from app.storage import atomic_write_json, read_json

TASK_ROLES: tuple[str, ...] = (
    "seed",
    "foundation",
    "voice",
    "canon",
    "outline",
    "scenes",
    "draft",
    "revise",
    "editor",
    "summary",
    "audit",
    "kdp",
)

DEFAULT_URLS: dict[str, str] = {
    "anthropic": "",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openrouter": "https://openrouter.ai/api/v1",
    "lmstudio": "http://localhost:1234/v1",
    "ollama": "http://localhost:11434",
}

DEFAULT_MODELS_BY_PROVIDER: dict[str, dict[str, str]] = {
    "anthropic": {
        "seed": "claude-sonnet-4-5",
        "foundation": "claude-sonnet-4-5",
        "voice": "claude-sonnet-4-5",
        "canon": "claude-haiku-4-5",
        "outline": "claude-opus-4-1",
        "scenes": "claude-sonnet-4-5",
        "draft": "claude-opus-4-1",
        "revise": "claude-opus-4-1",
        "editor": "claude-opus-4-1",
        "summary": "claude-haiku-4-5",
        "audit": "claude-opus-4-1",
        "kdp": "claude-sonnet-4-5",
    },
    "openai": {r: ("gpt-4.1-mini" if r in {"canon", "summary"} else "gpt-4.1") for r in TASK_ROLES},
    "gemini": {r: ("gemini-2.5-flash" if r in {"canon", "summary", "seed"} else "gemini-2.5-pro") for r in TASK_ROLES},
    "openrouter": {r: "anthropic/claude-sonnet-4.5" for r in TASK_ROLES},
    "lmstudio": {r: "" for r in TASK_ROLES},
    "ollama": {r: "" for r in TASK_ROLES},
}


class ProviderConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    num_ctx: int = Field(default=32768, ge=2048, le=262144)
    repeat_penalty: float = Field(default=1.12, ge=1.0, le=1.6)
    repeat_last_n: int = Field(default=256, ge=-1, le=4096)
    top_p: float = Field(default=0.9, ge=0.1, le=1.0)
    min_p: float = Field(default=0.05, ge=0.0, le=0.5)
    frequency_penalty: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_sec: int = Field(default=180, ge=15, le=900)


class QualityConfig(BaseModel):
    auto_polish: bool = True
    max_polish_passes: int = Field(default=2, ge=1, le=5)
    tells_threshold: float = Field(default=4.0, ge=0.0, le=50.0)
    auto_scene_cards: bool = True
    auto_continue_truncated: bool = True


def _default_providers_dict() -> dict[str, ProviderConfig]:
    return {p.value: ProviderConfig(base_url=DEFAULT_URLS.get(p.value, "")) for p in ProviderName}


def _default_models_dict() -> dict[str, str]:
    return {role: "" for role in TASK_ROLES}


class Settings(BaseModel):
    provider: ProviderName = ProviderName.ollama
    providers: dict[str, ProviderConfig] = Field(default_factory=_default_providers_dict)
    models: dict[str, str] = Field(default_factory=_default_models_dict)
    quality: QualityConfig = Field(default_factory=QualityConfig)

    @field_validator("providers", mode="before")
    @classmethod
    def _fill_providers(cls, v: Any) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        raw = v if isinstance(v, dict) else {}
        for p in ProviderName:
            cur = dict(raw.get(p.value, {}) or {})
            if not cur.get("base_url") and DEFAULT_URLS.get(p.value):
                cur["base_url"] = DEFAULT_URLS[p.value]
            merged[p.value] = cur
        return merged

    @field_validator("models", mode="before")
    @classmethod
    def _fill_models(cls, v: Any) -> dict[str, str]:
        raw = v if isinstance(v, dict) else {}
        return {role: str(raw.get(role) or "") for role in TASK_ROLES}


class ProviderPatch(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    num_ctx: int | None = Field(default=None, ge=2048, le=262144)
    repeat_penalty: float | None = Field(default=None, ge=1.0, le=1.6)
    repeat_last_n: int | None = Field(default=None, ge=-1, le=4096)
    top_p: float | None = Field(default=None, ge=0.1, le=1.0)
    min_p: float | None = Field(default=None, ge=0.0, le=0.5)
    frequency_penalty: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_sec: int | None = Field(default=None, ge=15, le=900)


class QualityPatch(BaseModel):
    auto_polish: bool | None = None
    max_polish_passes: int | None = Field(default=None, ge=1, le=5)
    tells_threshold: float | None = Field(default=None, ge=0.0, le=50.0)
    auto_scene_cards: bool | None = None
    auto_continue_truncated: bool | None = None


class SettingsPatch(BaseModel):
    provider: ProviderName | None = None
    providers: dict[ProviderName, ProviderPatch] | None = None
    models: dict[str, str] | None = None
    quality: QualityPatch | None = None


def settings_path() -> Path:
    return data_dir() / "settings.json"


_CACHE: Settings | None = None


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


def mask_key(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 10:
        return "•" * min(8, len(k))
    return f"{k[:6]}…{k[-4:]}"


def _is_masked(value: str) -> bool:
    return "…" in value or ("•" in value and set(value) <= {"•"})


def default_settings() -> Settings:
    s = Settings(provider=ProviderName.ollama)
    return s


def load_settings() -> Settings:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    p = settings_path()
    raw = read_json(p)
    if isinstance(raw, dict):
        _CACHE = Settings.model_validate(raw)
    else:
        _CACHE = default_settings()
    return _CACHE


def save_settings(s: Settings) -> Settings:
    global _CACHE
    atomic_write_json(settings_path(), s.model_dump(mode="json"))
    _CACHE = s
    return s


def masked_settings_dict(s: Settings | None = None) -> dict[str, Any]:
    s = s or load_settings()
    d = s.model_dump(mode="json")
    for pdata in d["providers"].values():
        raw_k = pdata.get("api_key", "")
        pdata["has_key"] = bool(raw_k)
        pdata["api_key"] = mask_key(raw_k)
    return d


def apply_settings_patch(patch: SettingsPatch) -> Settings:
    cur = copy.deepcopy(load_settings())
    if patch.provider is not None:
        cur.provider = patch.provider
    if patch.providers is not None:
        for pname, ppatch in patch.providers.items():
            cfg = cur.providers.get(pname.value) or ProviderConfig(base_url=DEFAULT_URLS.get(pname.value, ""))
            pdata = ppatch.model_dump(exclude_unset=True)
            if "api_key" in pdata:
                new_k = pdata.pop("api_key")
                if new_k is None:
                    cfg.api_key = ""  # explicit clear
                elif isinstance(new_k, str) and new_k.strip() and not _is_masked(new_k):
                    cfg.api_key = new_k.strip()
            for k, v in pdata.items():
                if v is not None:
                    setattr(cfg, k, v)
            cur.providers[pname.value] = cfg
    if patch.models is not None:
        for role, model in patch.models.items():
            if role in TASK_ROLES:
                cur.models[role] = (model or "").strip()
    if patch.quality is not None:
        qdata = patch.quality.model_dump(exclude_unset=True)
        for k, v in qdata.items():
            if v is not None:
                setattr(cur.quality, k, v)
    return save_settings(cur)


def active_provider_config(s: Settings | None = None) -> tuple[ProviderName, ProviderConfig]:
    s = s or load_settings()
    cfg = s.providers.get(s.provider.value) or ProviderConfig(base_url=DEFAULT_URLS.get(s.provider.value, ""))
    return s.provider, cfg


def model_for_task(task: str, s: Settings | None = None) -> str:
    s = s or load_settings()
    m = (s.models.get(task) or "").strip()
    if m:
        return m
    # Fallback to any non-empty configured model, then provider default
    for role in ("draft", "outline", "foundation", *TASK_ROLES):
        cand = (s.models.get(role) or "").strip()
        if cand:
            return cand
    return DEFAULT_MODELS_BY_PROVIDER.get(s.provider.value, {}).get(task, "")
