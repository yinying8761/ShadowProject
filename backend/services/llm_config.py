"""
LLM runtime config — mutable, no-restart edits (Workflow H, ADR-0001).

Hermes-style split: `.env` holds only secrets (one ``<PROVIDER>_API_KEY`` per
provider, ``LLM_API_KEY`` as fallback); ``data/config.yaml`` holds the effective
selection (active provider + model + custom provider list). ``LLMRuntimeConfig``
is the mutable in-memory object the UI writes back to; ``ConfigStore`` owns the
yaml + ``.env`` read/write.

``LLMService`` reads the shared ``runtime_config`` singleton (injected via
``runtime_config=`` in tests), so edits take effect on the next request without a
restart.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from dotenv import dotenv_values, set_key, unset_key

from config import PROVIDER_PRESETS


def _provider_env_var(provider_id: str) -> str:
    """Snake-case a provider id into its per-provider key env var name.

    ``opencode-go`` → ``OPENCODE_GO_API_KEY``.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", provider_id).upper() + "_API_KEY"


def _resolve_env_path() -> Path:
    """Return the ``.env`` file ``Settings`` reads (backend/ then project root).

    Matches ``config.Settings``'s load order so keys are written to the file
    that is actually consulted.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    for candidate in (backend_dir / ".env", backend_dir.parent / ".env"):
        if candidate.exists():
            return candidate
    return backend_dir / ".env"


class LLMRuntimeConfig:
    """Mutable LLM runtime config. Seeds from .env; UI writes yaml+.env and
    refreshes this object so changes take effect without a restart."""

    def __init__(
        self,
        provider: str = "custom",
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        custom_providers: list[dict] | None = None,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.custom_providers = custom_providers or []

    def _preset(self) -> dict | None:
        """Return the effective preset: a custom-provider entry when the active
        provider is custom, else the built-in ``PROVIDER_PRESETS`` entry."""
        for cp in self.custom_providers:
            if cp.get("id") == self.provider:
                return {
                    "base_url": cp.get("base_url"),
                    "default_model": "",
                    "sdk_type": "openai",
                }
        return PROVIDER_PRESETS.get(self.provider, PROVIDER_PRESETS.get("custom"))

    def get_sdk_type(self) -> str:
        preset = self._preset()
        return preset.get("sdk_type", "openai") if preset else "openai"

    def get_base_url(self) -> str | None:
        if self.base_url:
            return self.base_url
        preset = self._preset()
        return preset.get("base_url") if preset else None

    def get_model(self) -> str:
        if self.model:
            return self.model
        preset = self._preset()
        return preset.get("default_model", "") if preset else ""

    def resolve(self) -> tuple[str, str | None, str]:
        """Return ``(sdk_type, base_url, model)`` resolved through
        ``PROVIDER_PRESETS`` + ``custom_providers``. A ``custom`` provider has
        ``sdk_type == 'openai'``."""
        return self.get_sdk_type(), self.get_base_url(), self.get_model()

    def copy_from(self, other: "LLMRuntimeConfig") -> None:
        """Overwrite this config's fields from *other*.

        Used to refresh the shared singleton without rebinding the module
        reference (so ``LLMService`` instances already holding it stay current).
        """
        self.provider = other.provider
        self.model = other.model
        self.base_url = other.base_url
        self.api_key = other.api_key
        self.custom_providers = list(other.custom_providers)


def _seed_from_settings() -> LLMRuntimeConfig:
    """Build the initial runtime config seeded only with the fallback key.

    Per ADR-0001, legacy ``LLM_PROVIDER`` / ``LLM_MODEL`` / ``LLM_BASE_URL`` are
    deprecated and NOT auto-migrated (the user re-selects once in the UI);
    ``LLM_API_KEY`` remains as the fallback key. Provider/model/base_url come
    from ``data/config.yaml`` once it exists, or their defaults.
    """
    from config import settings

    return LLMRuntimeConfig(api_key=settings.llm_api_key)


# Module-level mutable singleton. Every ``LLMService`` defaults to THIS object,
# so mutating it (UI save) takes effect on the next request without a restart.
# ``main.py`` overlays ``data/config.yaml`` + per-provider keys at startup.
runtime_config: LLMRuntimeConfig = _seed_from_settings()


def default_runtime() -> LLMRuntimeConfig:
    """Return the shared runtime-config singleton (``LLMService``'s default)."""
    return runtime_config


class ConfigStore:
    """Read/write ``data/config.yaml`` + per-provider keys in ``.env``.

    ``data_dir`` / ``env_path`` / ``config_path`` are injectable so tests can
    point the store at a temp directory instead of the real files.
    """

    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        env_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ):
        from config import settings

        self._data_dir = (
            Path(data_dir) if data_dir is not None else settings.resolve_data_dir()
        )
        self._config_path = (
            Path(config_path) if config_path is not None else self._data_dir / "config.yaml"
        )
        self._env_path = Path(env_path) if env_path is not None else _resolve_env_path()

    def load(self) -> LLMRuntimeConfig:
        """Return the effective runtime config.

        ``data/config.yaml``, when present, is authoritative: its ``model``
        section (provider/model/base_url) + ``providers`` define the effective
        config. When the yaml is absent the config defaults to ``custom``/
        empty (legacy ``LLM_*`` settings are deprecated, per ADR-0001). The
        active provider's key is resolved per-provider with ``LLM_API_KEY`` as
        fallback.
        """
        if self._config_path.exists():
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            model = data.get("model") or {}
            cfg = LLMRuntimeConfig(
                provider=model.get("provider") or "custom",
                model=model.get("model") or "",
                base_url=model.get("base_url") or "",
                custom_providers=data.get("providers") or [],
            )
        else:
            cfg = _seed_from_settings()

        cfg.api_key = self.get_provider_key(cfg.provider)
        return cfg

    def save(self, cfg: LLMRuntimeConfig) -> None:
        """Write yaml (effective selection + custom providers) and the active
        provider's key to ``.env``."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        model_data = {"provider": cfg.provider, "model": cfg.model}
        if cfg.base_url:
            model_data["base_url"] = cfg.base_url
        data = {"model": model_data, "providers": cfg.custom_providers}
        self._config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        env_key = _provider_env_var(cfg.provider)
        if cfg.api_key:
            set_key(str(self._env_path), env_key, cfg.api_key)
        else:
            # Empty key → remove any stale per-provider entry ("clear").
            try:
                unset_key(str(self._env_path), env_key)
            except Exception:
                pass

    def get_provider_key(self, provider_id: str) -> str:
        """Return ``<ID>_API_KEY`` from ``.env``, falling back to ``LLM_API_KEY``."""
        values = dotenv_values(str(self._env_path))
        key = values.get(_provider_env_var(provider_id))
        if key:
            return key
        return values.get("LLM_API_KEY") or ""


# Module-level ConfigStore singleton (production data/config.yaml + .env).
# The API layer uses this; tests monkeypatch it to a temp-dir store.
config_store = ConfigStore()
