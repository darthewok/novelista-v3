from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from app.enums import ProviderName
from app.settings import (
    DEFAULT_URLS,
    ProviderConfig,
    Settings,
    active_provider_config,
    load_settings,
    model_for_task,
)

log = logging.getLogger("novelista.llm")

StreamCB = Callable[[str], Awaitable[None]]

_TEMP_BY_CREATIVITY = {
    "low": 0.25,
    "medium": 0.75,
    "high": 0.92,
}

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    stop_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


_HTTP_CLIENT: httpx.AsyncClient | None = None


def _http_client(timeout_sec: int = 180) -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=float(timeout_sec), write=30.0, pool=15.0),
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
        )
    return _HTTP_CLIENT


async def close_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()
    _HTTP_CLIENT = None


def _is_reasoning_openai_model(model: str) -> bool:
    m = model.lower().split("/")[-1]
    return bool(re.match(r"^(o[134](-|$)|gpt-5)", m))


def _norm_stop(reason: str | None) -> str:
    if not reason:
        return "stop"
    r = reason.lower()
    if r in {"length", "max_tokens", "max_output_tokens", "max_token"}:
        return "length"
    if r in {"end_turn", "stop", "eos"}:
        return "stop"
    return r


def _parse_param_billions(param_size: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*([bm])", (param_size or "").lower())
    if not m:
        return None
    val = float(m.group(1))
    return val if m.group(2) == "b" else val / 1000.0


async def _complete_anthropic(
    *,
    cfg: ProviderConfig,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temp: float,
    cache_prefix: bool,
    stream_cb: StreamCB | None,
) -> LLMResult:
    key = cfg.api_key.strip()
    if not key:
        raise LLMError("Anthropic API key is missing. Add it in Settings.")
    import anthropic

    kwargs: dict[str, Any] = {"api_key": key, "timeout": float(cfg.timeout_sec)}
    if cfg.base_url.strip():
        kwargs["base_url"] = cfg.base_url.strip().rstrip("/")
    client = anthropic.AsyncAnthropic(**kwargs)
    try:
        sys_payload: Any = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if (cache_prefix and system.strip())
            else system
        )
        chunks: list[str] = []
        stop = "stop"
        in_tok = 0
        out_tok = 0
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temp,
            system=sys_payload,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for delta in stream.text_stream:
                if delta:
                    chunks.append(delta)
                    if stream_cb:
                        await stream_cb(delta)
            final = await stream.get_final_message()
            stop = _norm_stop(getattr(final, "stop_reason", None))
            usage = getattr(final, "usage", None)
            if usage:
                in_tok = int(getattr(usage, "input_tokens", 0) or 0)
                out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResult(
            text="".join(chunks),
            stop_reason=stop,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=model,
        )
    except anthropic.APIStatusError as exc:
        retryable = exc.status_code in _RETRYABLE_STATUS
        raise LLMError(f"Anthropic error ({exc.status_code}): {exc.message}", status_code=exc.status_code, retryable=retryable) from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError(f"Anthropic connection error: {exc}", retryable=True) from exc
    finally:
        await client.close()


async def _complete_ollama(
    *,
    cfg: ProviderConfig,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temp: float,
    json_mode: bool,
    overrides: dict[str, Any] | None,
    stream_cb: StreamCB | None,
) -> LLMResult:
    base = (cfg.base_url or DEFAULT_URLS["ollama"]).strip().rstrip("/")
    url = f"{base}/api/chat"
    ov = overrides or {}
    options: dict[str, Any] = {
        "num_ctx": int(ov.get("num_ctx", cfg.num_ctx)),
        "temperature": float(ov.get("temperature", temp)),
        "repeat_penalty": float(ov.get("repeat_penalty", cfg.repeat_penalty)),
        "repeat_last_n": int(ov.get("repeat_last_n", cfg.repeat_last_n)),
        "top_p": float(ov.get("top_p", cfg.top_p)),
        "min_p": float(ov.get("min_p", cfg.min_p)),
        "num_predict": max_tokens,
    }
    payload: dict[str, Any] = {
        "model": model,
        "stream": True,
        "think": False,
        "keep_alive": "15m",
        "options": options,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        payload["format"] = "json"
    client = _http_client(cfg.timeout_sec)
    chunks: list[str] = []
    stop = "stop"
    in_tok = 0
    out_tok = 0
    timeout = httpx.Timeout(connect=15.0, read=float(cfg.timeout_sec), write=30.0, pool=15.0)
    try:
        async with client.stream("POST", url, json=payload, timeout=timeout) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                raise LLMError(
                    f"Ollama HTTP {resp.status_code}: {body}",
                    status_code=resp.status_code,
                    retryable=resp.status_code in _RETRYABLE_STATUS,
                )
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in data and data["error"]:
                    err_msg = str(data["error"])
                    low_err = err_msg.lower()
                    is_retryable = any(
                        k in low_err for k in ("repeat limit", "prediction aborted", "overloaded", "busy", "timeout")
                    )
                    raise LLMError(f"Ollama error: {err_msg}", retryable=is_retryable)
                msg = data.get("message") or {}
                delta = msg.get("content") or ""
                if delta:
                    chunks.append(delta)
                    if stream_cb:
                        await stream_cb(delta)
                if data.get("done"):
                    stop = _norm_stop(data.get("done_reason"))
                    in_tok = int(data.get("prompt_eval_count") or 0)
                    out_tok = int(data.get("eval_count") or 0)
    except httpx.TimeoutException as exc:
        raise LLMError(f"Ollama request timed out after {cfg.timeout_sec}s: {exc}", retryable=not chunks) from exc
    except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
        raise LLMError(f"Ollama stream interrupted: {exc}", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"Ollama network error: {exc}", retryable=not chunks) from exc
    return LLMResult(text="".join(chunks), stop_reason=stop, input_tokens=in_tok, output_tokens=out_tok, model=model)


async def _complete_gemini(
    *,
    cfg: ProviderConfig,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temp: float,
    json_mode: bool,
    stream_cb: StreamCB | None,
) -> LLMResult:
    key = cfg.api_key.strip()
    if not key:
        raise LLMError("Google Gemini API key is missing. Add it in Settings.")
    base = (cfg.base_url or DEFAULT_URLS["gemini"]).strip().rstrip("/")
    clean_model = model.removeprefix("models/")
    url = f"{base}/models/{clean_model}:streamGenerateContent?alt=sse"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    gen_cfg: dict[str, Any] = {
        "temperature": temp,
        "maxOutputTokens": max_tokens,
        "topP": cfg.top_p,
    }
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": gen_cfg,
    }
    if system.strip():
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    client = _http_client(cfg.timeout_sec)
    timeout = httpx.Timeout(connect=15.0, read=float(cfg.timeout_sec), write=30.0, pool=15.0)
    chunks: list[str] = []
    stop = "stop"
    in_tok = 0
    out_tok = 0
    try:
        async with client.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                raise LLMError(
                    f"Gemini HTTP {resp.status_code}: {body}",
                    status_code=resp.status_code,
                    retryable=resp.status_code in _RETRYABLE_STATUS,
                )
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                raw_json = line[5:].strip()
                if not raw_json or raw_json == "[DONE]":
                    continue
                try:
                    data = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue
                if "error" in data:
                    err = data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise LLMError(f"Gemini stream error: {msg}")
                for cand in data.get("candidates") or []:
                    content = cand.get("content") or {}
                    for part in content.get("parts") or []:
                        txt = part.get("text") or ""
                        if txt:
                            chunks.append(txt)
                            if stream_cb:
                                await stream_cb(txt)
                    fr = cand.get("finishReason")
                    if fr:
                        stop = _norm_stop("length" if fr == "MAX_TOKENS" else fr)
                um = data.get("usageMetadata") or {}
                if um:
                    in_tok = int(um.get("promptTokenCount") or in_tok)
                    out_tok = int(um.get("candidatesTokenCount") or out_tok)
    except httpx.TimeoutException as exc:
        raise LLMError(f"Gemini request timed out: {exc}", retryable=not chunks) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"Gemini network error: {exc}", retryable=not chunks) from exc
    return LLMResult(text="".join(chunks), stop_reason=stop, input_tokens=in_tok, output_tokens=out_tok, model=clean_model)


async def _complete_openai_compat(
    *,
    provider: ProviderName,
    cfg: ProviderConfig,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temp: float,
    json_mode: bool = False,
    overrides: dict[str, Any] | None,
    stream_cb: StreamCB | None,
) -> LLMResult:
    if provider in (ProviderName.openai, ProviderName.openrouter) and not cfg.api_key.strip():
        raise LLMError(f"{provider.value.title()} API key is missing. Add it in Settings.")
    base = (cfg.base_url or DEFAULT_URLS[provider.value]).strip().rstrip("/")
    url = f"{base}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key.strip():
        headers["Authorization"] = f"Bearer {cfg.api_key.strip()}"

    ov = overrides or {}
    payload: dict[str, Any] = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode and provider in (ProviderName.openai, ProviderName.openrouter):
        payload["response_format"] = {"type": "json_object"}
    if provider == ProviderName.openai and _is_reasoning_openai_model(model):
        payload["max_completion_tokens"] = max_tokens
    else:
        if provider == ProviderName.openai:
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
        payload["temperature"] = float(ov.get("temperature", temp))
        fp = float(ov.get("frequency_penalty", cfg.frequency_penalty))
        if fp > 0.0:
            payload["frequency_penalty"] = fp

    client = _http_client(cfg.timeout_sec)
    timeout = httpx.Timeout(connect=15.0, read=float(cfg.timeout_sec), write=30.0, pool=15.0)
    chunks: list[str] = []
    stop = "stop"
    in_tok = 0
    out_tok = 0
    try:
        async with client.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                raise LLMError(
                    f"{provider.value} HTTP {resp.status_code}: {body}",
                    status_code=resp.status_code,
                    retryable=resp.status_code in _RETRYABLE_STATUS,
                )
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                raw_json = line[5:].strip()
                if not raw_json or raw_json == "[DONE]":
                    continue
                try:
                    data = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue
                if "error" in data and data["error"]:
                    err = data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise LLMError(f"{provider.value} error: {msg}", retryable=not chunks)
                choices = data.get("choices") or []
                if choices:
                    ch0 = choices[0]
                    delta = (ch0.get("delta") or {}).get("content") or ""
                    if delta:
                        chunks.append(delta)
                        if stream_cb:
                            await stream_cb(delta)
                    fr = ch0.get("finish_reason")
                    if fr:
                        stop = _norm_stop(fr)
                usage = data.get("usage") or {}
                if usage:
                    in_tok = int(usage.get("prompt_tokens") or in_tok)
                    out_tok = int(usage.get("completion_tokens") or out_tok)
    except httpx.TimeoutException as exc:
        raise LLMError(f"{provider.value} timed out after {cfg.timeout_sec}s: {exc}", retryable=not chunks) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"{provider.value} network error: {exc}", retryable=not chunks) from exc
    return LLMResult(text="".join(chunks), stop_reason=stop, input_tokens=in_tok, output_tokens=out_tok, model=model)


async def complete(
    *,
    system: str,
    user: str,
    task: str,
    max_tokens: int = 8000,
    stream_cb: StreamCB | None = None,
    creativity: str = "medium",
    cache_prefix: bool = True,
    json_mode: bool = False,
    overrides: dict[str, Any] | None = None,
    settings_override: Settings | None = None,
) -> LLMResult:
    s = settings_override or load_settings()
    provider, cfg = active_provider_config(s)
    model = model_for_task(task, s)
    if not model:
        raise LLMError(
            f"No model configured for task '{task}' on provider '{provider.value}'. Open Settings and assign a model."
        )
    temp = _TEMP_BY_CREATIVITY.get(creativity, 0.75)
    if overrides and "temperature" in overrides:
        temp = float(overrides["temperature"])

    cur_json_mode = json_mode
    cur_overrides = dict(overrides or {})

    async def _attempt() -> LLMResult:
        eff_ov = cur_overrides or None
        if provider == ProviderName.anthropic:
            return await _complete_anthropic(
                cfg=cfg,
                model=model,
                system=system,
                user=user,
                max_tokens=max_tokens,
                temp=temp,
                cache_prefix=cache_prefix,
                stream_cb=stream_cb,
            )
        if provider == ProviderName.ollama:
            return await _complete_ollama(
                cfg=cfg,
                model=model,
                system=system,
                user=user,
                max_tokens=max_tokens,
                temp=temp,
                json_mode=cur_json_mode,
                overrides=eff_ov,
                stream_cb=stream_cb,
            )
        if provider == ProviderName.gemini:
            return await _complete_gemini(
                cfg=cfg,
                model=model,
                system=system,
                user=user,
                max_tokens=max_tokens,
                temp=temp,
                json_mode=cur_json_mode,
                stream_cb=stream_cb,
            )
        return await _complete_openai_compat(
            provider=provider,
            cfg=cfg,
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temp=temp,
            json_mode=cur_json_mode,
            overrides=eff_ov,
            stream_cb=stream_cb,
        )

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return await _attempt()
        except LLMError as exc:
            last_exc = exc
            if not exc.retryable or attempt == 2:
                raise
            if provider == ProviderName.ollama and any(
                k in str(exc).lower() for k in ("repeat limit", "prediction aborted")
            ):
                cur_json_mode = False
                cur_overrides["temperature"] = max(0.55, temp)
                cur_overrides["repeat_penalty"] = max(
                    1.18, float(cur_overrides.get("repeat_penalty", cfg.repeat_penalty))
                )
            elif provider == ProviderName.ollama and "stream interrupted" in str(exc).lower():
                await close_client()
            delay = 1.5 * (2**attempt)
            log.warning("Retrying LLM call (%s/%s) in %.1fs: %s", attempt + 1, 3, delay, exc)
            await asyncio.sleep(delay)
    raise last_exc or LLMError("Unknown LLM error")


async def test_connection(settings_override: Settings | None = None) -> dict[str, Any]:
    s = settings_override or load_settings()
    provider, cfg = active_provider_config(s)
    url = (cfg.base_url or DEFAULT_URLS.get(provider.value, "")).strip().rstrip("/")
    available: list[str] = []
    model_meta: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    err: str | None = None
    ok = False

    client = _http_client(30)
    try:
        if provider == ProviderName.ollama:
            resp = await client.get(f"{url}/api/tags", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("models") or []:
                name = m.get("name") or ""
                if not name:
                    continue
                available.append(name)
                details = m.get("details") or {}
                psize = str(details.get("parameter_size") or "")
                pb = _parse_param_billions(psize)
                model_meta[name] = {
                    "parameter_size": psize,
                    "parameter_billions": pb,
                    "family": details.get("family", ""),
                    "quantization": details.get("quantization_level", ""),
                }
            ok = True
        elif provider == ProviderName.gemini:
            if not cfg.api_key.strip():
                err = "Google Gemini API key is empty."
            else:
                resp = await client.get(
                    f"{url}/models",
                    headers={"x-goog-api-key": cfg.api_key.strip()},
                    timeout=12.0,
                )
                resp.raise_for_status()
                for m in resp.json().get("models") or []:
                    methods = m.get("supportedGenerationMethods") or []
                    if "generateContent" in methods:
                        name = str(m.get("name") or "").removeprefix("models/")
                        if name:
                            available.append(name)
                ok = True
        elif provider == ProviderName.anthropic:
            if not cfg.api_key.strip():
                err = "Anthropic API key is empty."
            else:
                import anthropic

                akw: dict[str, Any] = {"api_key": cfg.api_key.strip(), "timeout": 15.0}
                if url:
                    akw["base_url"] = url
                ac = anthropic.AsyncAnthropic(**akw)
                try:
                    page = await ac.models.list(limit=25)
                    available = [m.id for m in page.data]
                    ok = True
                finally:
                    await ac.close()
        else:
            if provider in (ProviderName.openai, ProviderName.openrouter) and not cfg.api_key.strip():
                err = f"{provider.value.title()} API key is empty."
            else:
                headers = {}
                if cfg.api_key.strip():
                    headers["Authorization"] = f"Bearer {cfg.api_key.strip()}"
                resp = await client.get(f"{url}/models", headers=headers, timeout=12.0)
                resp.raise_for_status()
                for m in resp.json().get("data") or []:
                    mid = m.get("id")
                    if mid:
                        available.append(mid)
                ok = True
    except Exception as exc:
        err = str(exc) or exc.__class__.__name__

    configured_models = {k: v for k, v in s.models.items() if v}
    models_present = True
    if ok and available and configured_models:
        missing = sorted({v for v in configured_models.values() if v not in available})
        if missing:
            models_present = False
            warnings.append(f"Configured model(s) not returned by provider: {', '.join(missing)}")
    for role in ("draft", "revise", "outline", "editor", "voice"):
        mname = s.models.get(role, "")
        meta = model_meta.get(mname)
        if meta and meta.get("parameter_billions") is not None and meta["parameter_billions"] < 7.0:
            warnings.append(
                f"Model '{mname}' ({meta['parameter_size']}) on '{role}' is under 7B; expect weaker continuity and repetition compared to 8B-14B+ or cloud models."
            )
            break

    return {
        "provider": provider.value,
        "url": url,
        "ok": ok,
        "models_present": models_present,
        "available_models": available,
        "model_meta": model_meta,
        "warnings": warnings,
        "error": err,
    }
