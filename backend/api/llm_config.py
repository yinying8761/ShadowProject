"""
LLM config endpoints (Workflow H, #30).

- ``PUT /api/llm-config`` — update the effective config (provider/model/base_url/
  key/custom providers), persist to ``data/config.yaml`` + ``.env``, and
  invalidate cached SDK clients so the change takes effect without a restart.
- ``POST /api/llm/models`` / ``POST /api/llm/test`` — CORS-bypassing proxies
  that use the request's *unsaved* form values (not the stored config), so the
  UI can "fill key → list models → test → save" in one flow.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter
from pydantic import BaseModel

from services.llm_config import LLMRuntimeConfig, config_store, runtime_config
from services.llm_service import invalidate_llm_clients

router = APIRouter(prefix="/api", tags=["llm-config"])

# User-facing hints for common status codes; the proxy returns these so the UI
# can show a readable reason (401=key wrong, 404=model missing, …).
_STATUS_HINTS = {
    401: "API key 错误",
    404: "模型不存在",
    429: "请求限流",
}


class LlmConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    base_url: str | None = None
    api_key: str | None = None          # null=keep, ""=clear, else set
    custom_providers: list[dict] | None = None


class ModelsRequest(BaseModel):
    provider: str
    base_url: str | None = None
    api_key: str | None = None


class TestRequest(BaseModel):
    provider: str
    base_url: str | None = None
    api_key: str | None = None
    model: str


def _resolve(provider: str, base_url: str | None) -> tuple[str, str | None]:
    """Resolve ``(sdk_type, base_url)`` for a request's form values.

    Uses the request's explicit base_url when given, else the preset/custom
    provider's base_url (custom providers always use the OpenAI SDK).
    """
    cfg = LLMRuntimeConfig(
        provider=provider,
        base_url=base_url or "",
        custom_providers=runtime_config.custom_providers,
    )
    sdk_type, resolved_base_url, _ = cfg.resolve()
    return sdk_type, resolved_base_url


def _effective_key(provider: str, api_key: str | None) -> str:
    """Use the request's key when given, else the saved per-provider key
    (``<ID>_API_KEY`` → ``LLM_API_KEY`` fallback)."""
    return api_key or config_store.get_provider_key(provider)


def _build_client(sdk_type: str, base_url: str | None, api_key: str):
    """Build a fresh SDK client for the proxy request's unsaved form values."""
    if sdk_type == "anthropic":
        import anthropic
        return anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None)
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=api_key, base_url=base_url or None)


async def _list_models(sdk_type: str, base_url: str | None, api_key: str) -> list[str]:
    client = _build_client(sdk_type, base_url, api_key)
    page = await client.models.list()
    return [m.id for m in page.data]


async def _test_chat(sdk_type: str, base_url: str | None, api_key: str, model: str) -> None:
    client = _build_client(sdk_type, base_url, api_key)
    if sdk_type == "anthropic":
        await client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    else:
        await client.chat.completions.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )


def _describe_error(exc: Exception) -> str:
    """Turn an SDK/network/timeout error into a short user-facing message."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    status = getattr(exc, "status_code", None)
    if status is not None:
        hint = _STATUS_HINTS.get(status)
        if hint:
            return f"{status} {hint}"
        return f"{status} {str(exc).strip() or type(exc).__name__}"
    return str(exc).strip() or type(exc).__name__


@router.put("/llm-config")
async def update_llm_config(data: LlmConfigUpdate):
    """Persist the LLM effective config and hot-reload the runtime singleton."""
    if data.llm_provider is not None:
        runtime_config.provider = data.llm_provider
    if data.llm_model is not None:
        runtime_config.model = data.llm_model
    if data.base_url is not None:
        runtime_config.base_url = data.base_url
    if data.api_key is not None:
        runtime_config.api_key = data.api_key  # "" clears, else sets
    else:
        # api_key null = keep → re-resolve the (possibly switched) provider's own
        # key, rather than carrying over the previous provider's key (US13).
        # No-op when the provider didn't change.
        runtime_config.api_key = config_store.get_provider_key(runtime_config.provider)
    if data.custom_providers is not None:
        runtime_config.custom_providers = data.custom_providers

    config_store.save(runtime_config)
    invalidate_llm_clients()
    return {"status": "updated"}


@router.post("/llm/models")
async def list_llm_models(data: ModelsRequest):
    """Proxy the provider's model list (8s timeout). Returns ``models`` or
    ``error`` — never a 500 for provider failures."""
    sdk_type, base_url = _resolve(data.provider, data.base_url)
    key = _effective_key(data.provider, data.api_key)
    try:
        models = await asyncio.wait_for(_list_models(sdk_type, base_url, key), timeout=8)
        return {"models": models}
    except Exception as e:
        return {"error": _describe_error(e)}


@router.post("/llm/test")
async def test_llm_config(data: TestRequest):
    """Send a minimal chat (10s timeout) to verify key + endpoint + model."""
    sdk_type, base_url = _resolve(data.provider, data.base_url)
    key = _effective_key(data.provider, data.api_key)
    start = time.perf_counter()
    try:
        await asyncio.wait_for(
            _test_chat(sdk_type, base_url, key, data.model), timeout=10
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": True, "latency_ms": latency_ms}
    except Exception as e:
        return {"ok": False, "error": _describe_error(e)}
