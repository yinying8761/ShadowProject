"""
Tests for LLM config — Workflow H (#29 runtime config + ConfigStore).

S1 — LLMService(runtime_config=...) injection: a config change takes effect on
     the next call, and base_url/key changes rebuild the cached SDK client.
S2 — ConfigStore round-trip via a temp dir: yaml round-trip, per-provider key
     resolution (fallback to LLM_API_KEY), and .env write via python-dotenv.

No real LLM API, no real .env / config.yaml.
"""

from types import SimpleNamespace

import pytest
import yaml
from dotenv import dotenv_values

from services.llm_config import ConfigStore, LLMRuntimeConfig


# ── S1: runtime-config injection + client rebuild ─────────────────────────


class RecordingClient:
    """Fake OpenAI client that records every chat.completions.create call."""

    def __init__(self):
        self.calls: list[dict] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )


class RecordingFactory:
    """Fake ``openai.AsyncOpenAI`` factory: records constructor kwargs and
    returns the same client so cached-client behaviour is observable."""

    def __init__(self):
        self.kwargs: list[dict] = []
        self.client = RecordingClient()

    def __call__(self, **kwargs):
        self.kwargs.append(kwargs)
        return self.client


class _EmptyStream:
    """Async iterator that yields no chunks (stream_chat model-only test)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _StreamingClient:
    """Fake OpenAI client that records the model on each streamed create()."""

    def __init__(self):
        self.models: list[str] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self.models.append(kwargs["model"])
        return _EmptyStream()


class TestRuntimeConfigInjection:
    @pytest.mark.asyncio
    async def test_model_change_and_client_rebuild(self, monkeypatch):
        """Changed model is used on the next call; base_url/key change +
        invalidate_clients() rebuilds the cached client (S1)."""
        from services.llm_service import LLMService

        cfg = LLMRuntimeConfig(
            provider="openai", model="m1", base_url="https://old/v1", api_key="sk-old"
        )
        factory = RecordingFactory()
        monkeypatch.setattr("openai.AsyncOpenAI", factory)

        llm = LLMService(runtime_config=cfg)

        await llm.chat_sync([{"role": "user", "content": "hi"}])
        assert factory.client.calls[-1]["model"] == "m1"
        assert factory.kwargs[-1]["base_url"] == "https://old/v1"
        assert factory.kwargs[-1]["api_key"] == "sk-old"
        assert len(factory.kwargs) == 1  # one client built

        # Model change → next call uses the new model, cached client is reused.
        cfg.model = "m2"
        await llm.chat_sync([{"role": "user", "content": "hi"}])
        assert factory.client.calls[-1]["model"] == "m2"
        assert len(factory.kwargs) == 1  # still cached

        # base_url/key change + invalidate → client rebuilt with new values.
        cfg.base_url = "https://new/v1"
        cfg.api_key = "sk-new"
        llm.invalidate_clients()
        await llm.chat_sync([{"role": "user", "content": "hi"}])
        assert len(factory.kwargs) == 2
        assert factory.kwargs[-1]["base_url"] == "https://new/v1"
        assert factory.kwargs[-1]["api_key"] == "sk-new"

    @pytest.mark.asyncio
    async def test_stream_chat_uses_runtime_model(self, monkeypatch):
        """stream_chat also reads the runtime model on each call (S1)."""
        from services.llm_service import LLMService

        cfg = LLMRuntimeConfig(provider="openai", model="m1")
        client = _StreamingClient()
        monkeypatch.setattr("openai.AsyncOpenAI", lambda **kw: client)

        llm = LLMService(runtime_config=cfg)

        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]
        assert events == []
        assert client.models == ["m1"]

        cfg.model = "m2"
        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]
        assert events == []
        assert client.models == ["m1", "m2"]

    def test_resolve_custom_provider_is_openai(self):
        """A custom provider resolves to sdk_type 'openai' + its base_url."""
        cfg = LLMRuntimeConfig(
            provider="opencode-go",
            model="m1",
            custom_providers=[{"id": "opencode-go", "name": "oc", "base_url": "https://x/v1"}],
        )
        assert cfg.resolve() == ("openai", "https://x/v1", "m1")

    def test_resolve_builtin_preset(self):
        """A built-in provider resolves through PROVIDER_PRESETS."""
        assert LLMRuntimeConfig(provider="deepseek", model="m1").resolve() == (
            "openai",
            "https://api.deepseek.com/v1",
            "m1",
        )
        assert LLMRuntimeConfig(provider="anthropic", model="claude-test").resolve() == (
            "anthropic",
            None,
            "claude-test",
        )


# ── S2: ConfigStore round-trip + key resolution ────────────────────────────


class TestConfigStore:
    def test_round_trip(self, tmp_path):
        """Save → load returns the same provider/model/base_url/custom list."""
        store = ConfigStore(data_dir=tmp_path, env_path=tmp_path / ".env")
        cfg = LLMRuntimeConfig(
            provider="opencode-go",
            model="deepseek-chat",
            base_url="https://x/v1",
            custom_providers=[{"id": "opencode-go", "name": "oc", "base_url": "https://x/v1"}],
        )
        store.save(cfg)

        loaded = store.load()
        assert loaded.provider == "opencode-go"
        assert loaded.model == "deepseek-chat"
        assert loaded.base_url == "https://x/v1"
        assert loaded.custom_providers == [
            {"id": "opencode-go", "name": "oc", "base_url": "https://x/v1"}
        ]

    def test_load_without_yaml_returns_defaults(self, tmp_path):
        """No config.yaml → provider defaults to 'custom' (no legacy migration)."""
        store = ConfigStore(data_dir=tmp_path, env_path=tmp_path / ".env")
        cfg = store.load()
        assert cfg.provider == "custom"
        assert cfg.model == ""
        assert cfg.base_url == ""
        assert cfg.custom_providers == []

    def test_get_provider_key_falls_back_to_llm_key(self, tmp_path):
        """<ID>_API_KEY hit wins; otherwise LLM_API_KEY is the fallback."""
        env = tmp_path / ".env"
        env.write_text("DEEPSEEK_API_KEY=sk-deep\nLLM_API_KEY=sk-fallback\n", encoding="utf-8")
        store = ConfigStore(data_dir=tmp_path, env_path=env)

        assert store.get_provider_key("deepseek") == "sk-deep"
        assert store.get_provider_key("qwen") == "sk-fallback"
        assert store.get_provider_key("opencode-go") == "sk-fallback"

    def test_save_writes_snake_cased_provider_key(self, tmp_path):
        """save() writes <ID>_API_KEY (custom id snake-cased) via python-dotenv."""
        env = tmp_path / ".env"
        store = ConfigStore(data_dir=tmp_path, env_path=env)
        store.save(LLMRuntimeConfig(provider="opencode-go", model="m1", api_key="sk-xyz"))

        values = dotenv_values(str(env))
        assert values.get("OPENCODE_GO_API_KEY") == "sk-xyz"

    def test_save_clears_empty_key(self, tmp_path):
        """api_key == '' removes the per-provider entry (clear semantics)."""
        env = tmp_path / ".env"
        env.write_text("DEEPSEEK_API_KEY=sk-old\n", encoding="utf-8")
        store = ConfigStore(data_dir=tmp_path, env_path=env)
        store.save(LLMRuntimeConfig(provider="deepseek", model="m1", api_key=""))

        values = dotenv_values(str(env))
        assert "DEEPSEEK_API_KEY" not in values

    def test_builtin_provider_base_url_not_persisted(self, tmp_path):
        """Saving a built-in provider with base_url='' must not freeze a
        base_url into config.yaml — the preset default stays in config.py
        (ADR-0001)."""
        store = ConfigStore(data_dir=tmp_path, env_path=tmp_path / ".env")

        # A prior custom save persisted a base_url…
        store.save(
            LLMRuntimeConfig(
                provider="opencode-go",
                model="m1",
                base_url="https://x/v1",
                custom_providers=[
                    {"id": "opencode-go", "name": "oc", "base_url": "https://x/v1"}
                ],
            )
        )
        yaml_path = tmp_path / "config.yaml"
        assert yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["model"][
            "base_url"
        ] == "https://x/v1"

        # …then switching to a built-in provider sends '' (not the preset URL).
        store.save(
            LLMRuntimeConfig(provider="deepseek", model="deepseek-chat", base_url="")
        )

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert "base_url" not in data["model"]

        loaded = store.load()
        assert loaded.base_url == ""
        assert loaded.get_base_url() == "https://api.deepseek.com/v1"
