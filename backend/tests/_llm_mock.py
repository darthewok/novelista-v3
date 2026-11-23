from __future__ import annotations

from typing import Any, Callable

from app import llm_client


def install_mock_llm(monkeypatch, handler: Callable[[str, str, str], str]):
    calls: list[dict[str, Any]] = []

    async def _fake_complete(
        *,
        system: str,
        user: str,
        task: str,
        max_tokens: int = 8000,
        stream_cb=None,
        creativity: str = "medium",
        cache_prefix: bool = True,
        json_mode: bool = False,
        overrides=None,
        settings_override=None,
    ) -> llm_client.LLMResult:
        calls.append({"task": task, "system": system, "user": user, "overrides": overrides})
        out = handler(task, system, user)
        if stream_cb and out:
            await stream_cb(out[:60])
            if len(out) > 60:
                await stream_cb(out[60:])
        return llm_client.LLMResult(text=out, stop_reason="stop", model="mock-model")

    monkeypatch.setattr(llm_client, "complete", _fake_complete)
    return calls
