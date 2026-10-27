"""模型能力表（ticket 01 / ADR-0011）：预设默认 → 用户覆盖 → 未知。

Seam: `services.capabilities.resolve_capabilities`（纯函数，spec S2）+ `config`
里的那张表 + `LLMRuntimeConfig` 的访问器。业务代码只应该通过访问器问能力，
所以这里只测「表 / 解析 / 访问器 / 落盘」，不测任何调用点。

配套的 HTTP 断言在 `tests/test_llm_config_api.py`（GET /api/config 的生效值，
PUT /api/llm-config 的覆盖落盘）。
"""

import pytest

from services.capabilities import (
    CAPABILITY_NAMES,
    IMAGE_APIS,
    SEARCH_APIS,
    resolve_capabilities,
    should_attempt_native_search,
    should_attempt_vision,
)


class TestResolution:
    """解析顺序：用户覆盖 → 预设默认 → 未知；来源必须可分辨（UI 要用）。"""

    def test_preset_defaults_are_used_without_an_override(self):
        caps = resolve_capabilities(
            {"supports_vision": True, "image_api": "openai"}, None
        )

        assert caps["supports_vision"].value is True
        assert caps["supports_vision"].source == "preset"
        assert caps["image_api"].value == "openai"
        assert caps["image_api"].source == "preset"

    def test_a_user_override_wins_over_the_preset(self):
        caps = resolve_capabilities(
            {"supports_vision": True, "image_api": "openai", "native_search": True},
            {"supports_vision": False, "image_api": "anthropic"},
        )

        assert caps["supports_vision"].value is False
        assert caps["supports_vision"].source == "user"
        assert caps["image_api"].value == "anthropic"
        assert caps["image_api"].source == "user"
        # 没被覆盖的那项仍然来自预设
        assert caps["native_search"].value is True
        assert caps["native_search"].source == "preset"

    def test_a_user_override_can_enable_what_the_table_does_not_know(self):
        """预设不认识的自定义模型：用户手动打开始终说了算。"""
        caps = resolve_capabilities({"supports_vision": None}, {"supports_vision": True})

        assert caps["supports_vision"].value is True
        assert caps["supports_vision"].source == "user"

    def test_missing_or_none_means_unknown(self):
        caps = resolve_capabilities({"supports_vision": None}, None)

        assert caps["supports_vision"].value is None
        assert caps["supports_vision"].source == "unknown"
        for name in ("image_api", "native_search", "native_fetch", "search_api"):
            assert caps[name].value is None, name
            assert caps[name].source == "unknown", name

    def test_every_capability_is_always_present(self):
        caps = resolve_capabilities(None, None)

        assert set(caps) == set(CAPABILITY_NAMES)
        assert all(cap.source == "unknown" for cap in caps.values())

    def test_unknown_keys_in_an_override_are_ignored(self):
        """yaml 里残留的旧键不该让解析崩掉（宽进严出：读宽容，写校验）。"""
        caps = resolve_capabilities({"supports_vision": True}, {"nonsense": True})

        assert caps["supports_vision"].value is True
        assert "nonsense" not in caps

    def test_a_garbage_override_value_is_ignored_not_trusted(self):
        """手改 yaml 写成字符串 `"true"` / 不认识的形状 → 当作没覆盖（回落到预设）。"""
        caps = resolve_capabilities(
            {"supports_vision": False, "image_api": "openai"},
            {"supports_vision": "true", "image_api": "midjourney"},
        )

        assert caps["supports_vision"].value is False  # 预设说了算，不是字符串真值
        assert caps["supports_vision"].source == "preset"
        assert caps["image_api"].value == "openai"
        assert caps["image_api"].source == "preset"

    def test_an_explicit_none_shape_survives(self):
        """`search_api="none"` 是表里的合法值（明确不尝试原生），不是"未知"。"""
        caps = resolve_capabilities({"native_search": False, "search_api": "none"}, None)

        assert caps["search_api"].value == "none"
        assert caps["search_api"].source == "preset"


class TestOverridePatch:
    """写入口语义：`None` = 清掉这一项，缺项 = 保持不变（PATCH，不是整批替换）。"""

    def test_a_patch_updates_only_the_named_capabilities(self):
        from services.capabilities import apply_overrides

        merged = apply_overrides(
            {"supports_vision": True, "native_search": False},
            {"image_api": "anthropic"},
        )

        assert merged == {
            "supports_vision": True,
            "native_search": False,
            "image_api": "anthropic",
        }

    def test_none_in_a_patch_clears_that_one_capability(self):
        from services.capabilities import apply_overrides

        merged = apply_overrides(
            {"supports_vision": True, "native_search": False},
            {"supports_vision": None},
        )

        assert merged == {"native_search": False}

    def test_an_empty_patch_changes_nothing(self):
        from services.capabilities import apply_overrides

        current = {"supports_vision": True}
        assert apply_overrides(current, {}) == current

    def test_a_patch_does_not_mutate_the_current_mapping(self):
        from services.capabilities import apply_overrides

        current = {"supports_vision": True}
        apply_overrides(current, {"supports_vision": None})

        assert current == {"supports_vision": True}


class TestNormalizeOverrides:
    """写入口校验（API 的 400 就靠它）：未知能力 / 类型不对 / 未知形状一律拒绝。"""

    def test_none_is_kept_as_a_clear_marker(self):
        from services.capabilities import normalize_overrides

        assert normalize_overrides({"supports_vision": None}) == {"supports_vision": None}

    @pytest.mark.parametrize(
        "bad",
        [
            {"nonsense": True},
            {"supports_vision": "yes"},
            {"image_api": "midjourney"},
            {"search_api": "google"},
        ],
    )
    def test_bad_input_raises(self, bad):
        from services.capabilities import normalize_overrides

        with pytest.raises(ValueError):
            normalize_overrides(bad)

    def test_good_input_passes_through(self):
        from services.capabilities import normalize_overrides

        assert normalize_overrides(
            {"supports_vision": True, "search_api": "deepseek", "native_fetch": False}
        ) == {"supports_vision": True, "search_api": "deepseek", "native_fetch": False}


class TestUnknownIsAsymmetric:
    """ADR-0011：视觉未知 → 试一次；搜索未知 → 不走原生。这条不对称是决定，不是疏忽。"""

    def test_unknown_vision_is_still_attempted(self):
        caps = resolve_capabilities(None, None)
        assert should_attempt_vision(caps["supports_vision"]) is True

    def test_unknown_native_search_is_not_attempted(self):
        caps = resolve_capabilities(None, None)
        assert should_attempt_native_search(caps["native_search"]) is False

    def test_explicit_false_stops_both(self):
        caps = resolve_capabilities(
            {"supports_vision": False, "native_search": False}, None
        )
        assert should_attempt_vision(caps["supports_vision"]) is False
        assert should_attempt_native_search(caps["native_search"]) is False

    def test_explicit_true_starts_both(self):
        caps = resolve_capabilities(
            {"supports_vision": True, "native_search": True}, None
        )
        assert should_attempt_vision(caps["supports_vision"]) is True
        assert should_attempt_native_search(caps["native_search"]) is True


class TestPresetTable:
    """票面要求：DeepSeek / OpenAI / Anthropic / Gemini 默认可用，未列出的保持未知。"""

    @pytest.mark.parametrize(
        "provider,image_api,search_api",
        [
            ("deepseek", "openai", "deepseek"),
            ("openai", "openai", "openai"),
            ("anthropic", "anthropic", "anthropic"),
            ("gemini", "openai", "openai"),  # 走 OpenAI 兼容端点（ADR-0011）
        ],
    )
    def test_named_providers_are_vision_and_search_capable_by_default(
        self, provider, image_api, search_api
    ):
        from config import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        assert preset["supports_vision"] is True
        assert preset["image_api"] == image_api
        assert preset["native_search"] is True
        assert preset["native_fetch"] is True
        assert preset["search_api"] == search_api

    def test_providers_without_a_default_stay_unknown(self):
        """同一家厂商里也有不支持视觉的模型 —— 没把握就不许替用户猜。"""
        from config import PROVIDER_PRESETS

        for provider in ("qwen", "zhipu", "custom"):
            assert PROVIDER_PRESETS[provider]["supports_vision"] is None, provider
        # moonshot 明确不支持（spec 的能力表）
        assert PROVIDER_PRESETS["moonshot"]["supports_vision"] is False
        # 这几家没被点名支持原生搜索 → 保持未知（消费者按"未知=不用"处理）
        for provider in ("qwen", "zhipu", "moonshot", "custom"):
            assert PROVIDER_PRESETS[provider].get("native_search") is None, provider

    def test_every_preset_uses_known_api_shapes(self):
        """能力表只能写已知形状 / 合法布尔：写歪了就会在 formatter / parser 那层炸得莫名其妙。"""
        from config import PROVIDER_PRESETS

        for provider, preset in PROVIDER_PRESETS.items():
            image_api = preset.get("image_api")
            search_api = preset.get("search_api")
            assert image_api is None or image_api in IMAGE_APIS, provider
            assert search_api is None or search_api in SEARCH_APIS, provider
            for name in ("supports_vision", "native_search", "native_fetch"):
                value = preset.get(name)
                assert value is None or isinstance(value, bool), f"{provider}.{name}"

    def test_every_preset_row_names_all_capabilities(self):
        """一行就是一张表：缺项容易让人以为"不支持"，所以显式写 None（=未知）。"""
        from config import PROVIDER_PRESETS

        for provider, preset in PROVIDER_PRESETS.items():
            for name in CAPABILITY_NAMES:
                assert name in preset, f"{provider} 缺 {name}"

    def test_every_provider_can_resolve_its_own_key(self, tmp_path):
        """新增的 gemini 不需要额外代码：密钥走通用的 `<ID>_API_KEY`。"""
        from services.llm_config import ConfigStore

        env = tmp_path / ".env"
        env.write_text("GEMINI_API_KEY=sk-gemini\n", encoding="utf-8")
        store = ConfigStore(data_dir=tmp_path, env_path=env)

        assert store.get_provider_key("gemini") == "sk-gemini"


class TestRuntimeAccessor:
    """业务代码只能通过 `runtime_config` 的访问器问能力（不再按厂商名 if）。"""

    def test_accessors_resolve_through_the_preset(self):
        from services.llm_config import LLMRuntimeConfig

        cfg = LLMRuntimeConfig(provider="anthropic")

        assert cfg.supports_vision() is True
        assert cfg.image_api() == "anthropic"
        assert cfg.native_search() is True
        assert cfg.search_api() == "anthropic"
        assert cfg.vision_should_attempt() is True
        assert cfg.native_search_should_attempt() is True

    def test_overrides_are_persisted_per_model_in_config_yaml(self, tmp_path):
        """覆盖按 `provider:model` 落进 data/config.yaml，重新加载后仍然生效。"""
        from services.llm_config import ConfigStore, LLMRuntimeConfig

        store = ConfigStore(data_dir=tmp_path, env_path=tmp_path / ".env")
        cfg = LLMRuntimeConfig(provider="custom", model="my-model")
        cfg.patch_capabilities({"supports_vision": True, "image_api": "openai"})
        store.save(cfg)

        reloaded = store.load()
        assert reloaded.current_capability_overrides() == {
            "supports_vision": True,
            "image_api": "openai",
        }
        assert reloaded.supports_vision() is True
        assert reloaded.image_api() == "openai"
        assert reloaded.capabilities()["supports_vision"].source == "user"

    def test_overrides_belong_to_the_model_they_were_set_on(self):
        """给 A 模型开的开关不该作用到 B 模型（ADR-0011：按模型解析）。"""
        from services.llm_config import LLMRuntimeConfig

        cfg = LLMRuntimeConfig(provider="custom", model="model-a")
        cfg.patch_capabilities({"supports_vision": True})
        assert cfg.supports_vision() is True

        cfg.model = "model-b"
        assert cfg.supports_vision() is None          # B 模型没有覆盖，回到未知
        assert cfg.current_capability_overrides() == {}

        cfg.model = "model-a"                          # 切回来仍在
        assert cfg.current_capability_overrides() == {"supports_vision": True}

    def test_a_patch_only_touches_the_named_capabilities(self):
        """补丁语义：只改说到的项，别的项（以及别的模型）不受影响。"""
        from services.llm_config import LLMRuntimeConfig

        cfg = LLMRuntimeConfig(provider="openai", model="gpt-4o")
        cfg.patch_capabilities({"supports_vision": True, "native_search": False})
        cfg.patch_capabilities({"native_search": None})   # 只清掉这一项

        assert cfg.current_capability_overrides() == {"supports_vision": True}

    def test_the_effective_capabilities_are_reported_with_their_source(self):
        from services.llm_config import LLMRuntimeConfig

        cfg = LLMRuntimeConfig(provider="deepseek")
        cfg.patch_capabilities({"native_search": False})

        caps = cfg.capabilities()
        assert caps["supports_vision"].value is True
        assert caps["supports_vision"].source == "preset"
        assert caps["native_search"].value is False
        assert caps["native_search"].source == "user"

    def test_copy_from_carries_the_overrides(self):
        """热重载用一个对象覆盖另一个，覆盖项不能丢（也不能共用同一个 dict）。"""
        from services.llm_config import LLMRuntimeConfig

        source = LLMRuntimeConfig(provider="openai")
        source.patch_capabilities({"supports_vision": False})
        target = LLMRuntimeConfig(provider="custom")

        target.copy_from(source)

        assert target.current_capability_overrides() == {"supports_vision": False}
        target.patch_capabilities({"supports_vision": True})
        assert source.current_capability_overrides() == {"supports_vision": False}

    def test_a_user_added_endpoint_inherits_the_custom_row(self):
        from services.llm_config import LLMRuntimeConfig

        cfg = LLMRuntimeConfig(provider="my-endpoint")
        cfg.custom_providers = [{"id": "my-endpoint", "name": "Mine", "base_url": "https://x/v1"}]

        # custom 预设：视觉未知（用户可手动开启）、形状是 openai
        assert cfg.supports_vision() is None
        assert cfg.image_api() == "openai"
        assert cfg.vision_should_attempt() is True  # 未知也试一次
        assert cfg.native_search_should_attempt() is False  # 未知不走原生
