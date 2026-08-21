"""
Tests for LLM config HTTP endpoints — Workflow H (#30 API).

S3 — GET /api/config (api_key_hint, never the full key), PUT /api/llm-config
(three-state api_key), GET /api/providers (built-in + custom merge), and the
POST /api/llm/models + POST /api/llm/test proxies (success / error, no 500).

Uses ASGITransport + a temp ConfigStore + an in-memory DB; SDK clients are
faked by patching openai.AsyncOpenAI / anthropic.AsyncAnthropic.
"""

from types import SimpleNamespace

import pytest


class FakeHTTPError(Exception):
    """Stand-in for an SDK APIStatusError carrying a status_code."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class FakeOpenAI:
    """Fake for openai.AsyncOpenAI: models.list() + chat.completions.create()."""

    def __init__(self, models=None, chat_result=None, exc=None):
        self._models = models or ["m1", "m2"]
        self._chat_result = chat_result or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
        self._exc = exc
        self.models = self
        self.chat = SimpleNamespace(completions=self)

    async def list(self):
        if self._exc:
            raise self._exc
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self._models])

    async def create(self, **kwargs):
        if self._exc:
            raise self._exc
        return self._chat_result


@pytest.fixture
async def llm_config_client(tmp_path, monkeypatch):
    """ASGITransport client wired to a temp ConfigStore + runtime config + in-memory DB."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from main import app  # noqa: F401 — imports models onto Base.metadata
    from database import Base, get_session
    from services.llm_config import ConfigStore, LLMRuntimeConfig

    cfg = LLMRuntimeConfig()
    store = ConfigStore(data_dir=tmp_path, env_path=tmp_path / ".env")

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    monkeypatch.setattr("api.config.runtime_config", cfg)
    monkeypatch.setattr("api.llm_config.runtime_config", cfg)
    monkeypatch.setattr("api.llm_config.config_store", store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, cfg, store

    app.dependency_overrides.clear()
    await engine.dispose()


# ── GET /api/config ────────────────────────────────────────────────────────


class TestGetConfig:
    @pytest.mark.asyncio
    async def test_returns_key_hint_never_full_key(self, llm_config_client):
        client, cfg, _ = llm_config_client
        cfg.provider = "deepseek"
        cfg.model = "deepseek-chat"
        cfg.api_key = "sk-1234abcd"

        resp = await client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()

        assert body["llm_provider"] == "deepseek"
        assert body["llm_model"] == "deepseek-chat"
        assert body["has_api_key"] is True
        assert body["api_key_hint"] == "abcd"
        assert "1234abcd" not in resp.text  # full key never leaks

    @pytest.mark.asyncio
    async def test_no_key_hint_when_empty(self, llm_config_client):
        client, cfg, _ = llm_config_client
        cfg.api_key = ""

        body = (await client.get("/api/config")).json()
        assert body["has_api_key"] is False
        assert body["api_key_hint"] is None


# ── PUT /api/llm-config (three-state api_key) ──────────────────────────────


class TestPutLlmConfig:
    @pytest.mark.asyncio
    async def test_api_key_three_state(self, llm_config_client):
        client, cfg, store = llm_config_client

        # Establish a persisted key for deepseek through the public save path
        # (so "keep" below has a real key to re-resolve from .env).
        await client.put(
            "/api/llm-config",
            json={"llm_provider": "deepseek", "llm_model": "m0", "api_key": "sk-old"},
        )
        assert cfg.api_key == "sk-old"

        # null → keep (re-resolves deepseek's persisted key; unchanged)
        resp = await client.put(
            "/api/llm-config", json={"llm_model": "m1", "api_key": None}
        )
        assert resp.status_code == 200
        assert cfg.api_key == "sk-old"
        assert cfg.model == "m1"

        # non-empty → set (persisted to .env, read back via the public API)
        await client.put("/api/llm-config", json={"api_key": "sk-new"})
        assert cfg.api_key == "sk-new"
        assert store.get_provider_key("deepseek") == "sk-new"

        # "" → clear
        await client.put("/api/llm-config", json={"api_key": ""})
        assert cfg.api_key == ""

    @pytest.mark.asyncio
    async def test_switch_provider_does_not_copy_old_key(self, llm_config_client):
        """Switching provider with api_key=null must NOT write the previous
        provider's key into the new provider's <PROVIDER>_API_KEY (US13)."""
        client, cfg, store = llm_config_client

        # Save deepseek's key.
        await client.put(
            "/api/llm-config", json={"llm_provider": "deepseek", "api_key": "sk-deep"}
        )
        assert store.get_provider_key("deepseek") == "sk-deep"

        # Switch to openai, leave key empty (null = keep). The old deepseek key
        # must not leak into OPENAI_API_KEY; the runtime key re-resolves to
        # openai's own (empty here).
        await client.put(
            "/api/llm-config", json={"llm_provider": "openai", "api_key": None}
        )
        assert store.get_provider_key("openai") == ""
        assert cfg.api_key == ""
        assert store.get_provider_key("deepseek") == "sk-deep"


# ── GET /api/providers ─────────────────────────────────────────────────────


class TestProviders:
    @pytest.mark.asyncio
    async def test_merges_custom_providers(self, llm_config_client):
        client, cfg, _ = llm_config_client
        cfg.custom_providers = [
            {"id": "opencode-go", "name": "oc", "base_url": "https://x/v1"}
        ]

        body = (await client.get("/api/providers")).json()
        ids = [p["id"] for p in body["providers"]]

        assert "deepseek" in ids  # built-in preset
        assert "opencode-go" in ids  # custom provider

        custom = next(p for p in body["providers"] if p["id"] == "opencode-go")
        assert custom["is_custom"] is True
        assert custom["base_url"] == "https://x/v1"
        assert custom["sdk_type"] == "openai"


# ── POST /api/llm/models (proxy) ───────────────────────────────────────────


class TestModelsProxy:
    @pytest.mark.asyncio
    async def test_returns_model_ids(self, llm_config_client, monkeypatch):
        client, _, _ = llm_config_client
        monkeypatch.setattr("openai.AsyncOpenAI", lambda **kw: FakeOpenAI(models=["m1", "m2"]))

        resp = await client.post(
            "/api/llm/models",
            json={"provider": "custom", "base_url": "https://x/v1", "api_key": "sk-x"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"models": ["m1", "m2"]}

    @pytest.mark.asyncio
    async def test_error_does_not_500(self, llm_config_client, monkeypatch):
        client, _, _ = llm_config_client
        monkeypatch.setattr(
            "openai.AsyncOpenAI", lambda **kw: FakeOpenAI(exc=RuntimeError("boom"))
        )

        resp = await client.post(
            "/api/llm/models",
            json={"provider": "custom", "base_url": "https://x/v1", "api_key": "sk-x"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert "boom" in body["error"]

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, llm_config_client, monkeypatch):
        """A timeout (asyncio.TimeoutError) maps to {error: 'timeout'}, not a 500."""
        import asyncio

        client, _, _ = llm_config_client
        monkeypatch.setattr(
            "openai.AsyncOpenAI", lambda **kw: FakeOpenAI(exc=asyncio.TimeoutError())
        )

        resp = await client.post(
            "/api/llm/models",
            json={"provider": "custom", "base_url": "https://x/v1", "api_key": "sk-x"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": "timeout"}


# ── POST /api/llm/test (proxy) ─────────────────────────────────────────────


class TestConnectionProxy:
    @pytest.mark.asyncio
    async def test_ok_with_latency(self, llm_config_client, monkeypatch):
        client, _, _ = llm_config_client
        monkeypatch.setattr("openai.AsyncOpenAI", lambda **kw: FakeOpenAI())

        resp = await client.post(
            "/api/llm/test",
            json={
                "provider": "custom",
                "base_url": "https://x/v1",
                "api_key": "sk-x",
                "model": "m1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_401_reports_key_error(self, llm_config_client, monkeypatch):
        client, _, _ = llm_config_client
        monkeypatch.setattr(
            "openai.AsyncOpenAI", lambda **kw: FakeOpenAI(exc=FakeHTTPError(401))
        )

        body = (
            await client.post(
                "/api/llm/test",
                json={
                    "provider": "custom",
                    "base_url": "https://x/v1",
                    "api_key": "sk-x",
                    "model": "m1",
                },
            )
        ).json()
        assert body["ok"] is False
        assert "401" in body["error"]

    @pytest.mark.asyncio
    async def test_404_reports_model_missing(self, llm_config_client, monkeypatch):
        client, _, _ = llm_config_client
        monkeypatch.setattr(
            "openai.AsyncOpenAI", lambda **kw: FakeOpenAI(exc=FakeHTTPError(404))
        )

        body = (
            await client.post(
                "/api/llm/test",
                json={
                    "provider": "custom",
                    "base_url": "https://x/v1",
                    "api_key": "sk-x",
                    "model": "m1",
                },
            )
        ).json()
        assert body["ok"] is False
        assert "模型不存在" in body["error"]
