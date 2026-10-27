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
from services.capabilities import (
    CapabilityValue,
    apply_overrides,
    resolve_capabilities,
    resolve_api_shape,
    should_attempt_native_search,
    should_attempt_vision,
)


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
        capability_overrides: dict | None = None,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.custom_providers = custom_providers or []
        # 能力覆盖（ADR-0011 / ticket 01）：`{ "provider:model": {能力名: 值} }`。
        # **按模型**存 —— 同一家厂商里 gpt-4o 能看、老模型不能，给 A 模型打开的开关
        # 不该悄悄作用到 B 模型上。落盘在 data/config.yaml 的 `capabilities:`。
        self.capability_overrides: dict[str, dict] = {
            str(key): dict(value or {})
            for key, value in (capability_overrides or {}).items()
        }

    def _preset(self) -> dict | None:
        """Return the effective preset: a custom-provider entry when the active
        provider is custom, else the built-in ``PROVIDER_PRESETS`` entry."""
        for cp in self.custom_providers:
            if cp.get("id") == self.provider:
                # 用户自建的端点 = 「custom」形状：能力默认沿用 custom 预设
                # （形状 openai、视觉未知），再叠上它自己的 base_url。
                return {
                    **PROVIDER_PRESETS.get("custom", {}),
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

    # ── Capabilities (ADR-0011 / ticket 01) ────────────────────────────
    # 业务代码**只能**通过这些访问器问「这个模型能做什么」，不许按厂商名分支。
    # 值保留三态（True / False / None=未知）；"未知怎么办"由两个 decision 访问器
    # 按 ADR-0011 的不对称规则回答。

    def capability_key(self) -> str:
        """能力覆盖的作用域：**按模型**（`provider:model`）。"""
        return f"{self.provider or ''}:{self.get_model() or ''}"

    def current_capability_overrides(self) -> dict:
        """当前模型自己的覆盖项（没有就是空字典）。"""
        return dict(self.capability_overrides.get(self.capability_key()) or {})

    def patch_capabilities(self, patch: dict) -> dict:
        """把一次能力覆盖补丁合并到**当前模型**上（`None` = 清掉那一项）。

        合并而不是整批替换：面板只发用户改到的项，别的项不受影响。合并后为空就把
        这个模型对应的键整个删掉，yaml 里不留空壳。
        """
        merged = apply_overrides(self.current_capability_overrides(), patch)
        key = self.capability_key()
        if merged:
            self.capability_overrides[key] = merged
        else:
            self.capability_overrides.pop(key, None)
        return merged

    def capabilities(self) -> dict:
        """每项能力的生效值 + 来源（`user` / `preset` / `unknown`），给 UI 用。"""
        return resolve_capabilities(self._preset(), self.current_capability_overrides())

    def _capability(self, name: str) -> CapabilityValue:
        return self.capabilities()[name]

    def supports_vision(self) -> bool | None:
        """主模型能不能看图（三态：None = 未知）。"""
        return self._capability("supports_vision").value

    def image_api(self) -> str | None:
        """图片载荷形状（`openai` / `anthropic` / `gemini` / None=未知）。"""
        return resolve_api_shape("image_api", self._capability("image_api").value)

    def native_search(self) -> bool | None:
        return self._capability("native_search").value

    def native_fetch(self) -> bool | None:
        return self._capability("native_fetch").value

    def search_api(self) -> str | None:
        return resolve_api_shape("search_api", self._capability("search_api").value)

    def vision_should_attempt(self) -> bool:
        """未知也试（视觉是必需能力）。"""
        return should_attempt_vision(self._capability("supports_vision"))

    def native_search_should_attempt(self) -> bool:
        """只有明确支持才试（搜索是可选增强）。"""
        return should_attempt_native_search(self._capability("native_search"))

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
        self.capability_overrides = {
            key: dict(value) for key, value in other.capability_overrides.items()
        }


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
                # 能力覆盖按 `provider:model` 分键：切模型时各用各的（ADR-0011）。
                capability_overrides=data.get("capabilities") or {},
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
        # 只写用户显式覆盖过的能力项；空字典就不写这个键（保持 yaml 干净）。
        if cfg.capability_overrides:
            data["capabilities"] = dict(cfg.capability_overrides)
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
