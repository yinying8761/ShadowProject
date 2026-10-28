"""图片内容块（ticket 02 / ADR-0011）：形状**住在 formatter 缝上**，按 `image_api` 取。

Seam: `MessageFormatter.image_part()`（基类 = OpenAI 形状）+ `get_image_formatter()`
（能力表的 `image_api` → 提供该形状的 formatter 类）+ `images.image_part()` 分派器。
业务代码只调这些；未知形状**显式报错**，绝不静默丢图。
"""

import base64

import pytest

from services.capabilities import IMAGE_APIS
from services.formatters import (
    AnthropicFormatter,
    GeminiFormatter,
    MessageFormatter,
    get_image_formatter,
)
from services.formatters.images import image_part, text_part

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
B64 = base64.b64encode(JPEG).decode("ascii")


class TestShapes:
    def test_the_base_formatter_is_the_openai_compatible_shape(self):
        """基类默认就是 OpenAI 兼容形状（大多数厂商 + 所有兼容端点）。"""
        assert MessageFormatter.image_part(JPEG, "image/jpeg") == {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{B64}"},
        }

    def test_anthropic_overrides_with_a_base64_source_block(self):
        assert AnthropicFormatter.image_part(JPEG, "image/png") == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",   # mime 原样透传，不被改写
                "data": B64,
            },
        }

    def test_gemini_provides_the_native_inline_data_shape(self):
        assert GeminiFormatter.image_part(JPEG, "image/jpeg") == {
            "inline_data": {"mime_type": "image/jpeg", "data": B64}
        }

    def test_text_part_is_the_same_shape_everywhere(self):
        assert text_part("看看这个") == {"type": "text", "text": "看看这个"}


class TestDispatch:
    @pytest.mark.parametrize(
        "api,formatter",
        [
            ("openai", MessageFormatter),
            ("anthropic", AnthropicFormatter),
            ("gemini", GeminiFormatter),
        ],
    )
    def test_the_registry_maps_the_capability_to_the_formatter(self, api, formatter):
        assert get_image_formatter(api) is formatter
        assert image_part(api, JPEG, "image/jpeg") == formatter.image_part(JPEG, "image/jpeg")

    @pytest.mark.parametrize("api", [None, "", "midjourney", "google"])
    def test_an_unknown_shape_raises_instead_of_sending_text_only(self, api):
        """用户明确要求看屏幕 —— 静默退化成纯文本请求等于骗人（ADR-0011 的不对称）。"""
        assert get_image_formatter(api) is None
        with pytest.raises(ValueError):
            image_part(api, JPEG, "image/jpeg")

    def test_the_registry_covers_exactly_the_declared_image_apis(self):
        """能力表说有哪些形状，就必须有对应的 formatter（防漂移）。"""
        for api in IMAGE_APIS:
            assert get_image_formatter(api) is not None, api


class TestLLMServiceShape:
    """`LLMService.image_part` 按当前模型的能力分派，并且拒绝"形状与 SDK 不匹配"。"""

    def _llm(self, **kwargs):
        from services.llm_config import LLMRuntimeConfig
        from services.llm_service import LLMService

        return LLMService(runtime_config=LLMRuntimeConfig(**kwargs))

    def test_anthropic_model_gets_the_anthropic_shape(self):
        assert self._llm(provider="anthropic").image_part(JPEG)["type"] == "image"

    def test_openai_compatible_model_gets_the_openai_shape(self):
        assert self._llm(provider="deepseek").image_part(JPEG)["type"] == "image_url"

    def test_an_unknown_shape_names_the_model_in_the_error(self):
        from services.llm_config import LLMRuntimeConfig
        from services.llm_service import LLMService

        class NoShapeRuntime(LLMRuntimeConfig):
            def image_api(self):
                return None

        llm = LLMService(runtime_config=NoShapeRuntime(provider="custom", model="mystery"))

        with pytest.raises(ValueError) as excinfo:
            llm.image_part(JPEG)
        assert "mystery" in str(excinfo.value)

    def test_a_shape_that_does_not_match_the_sdk_is_refused(self):
        """openai SDK 只认 image_url、Anthropic SDK 只认 source —— 配错就当场说清楚。"""
        cfg_overrides = {"custom:": {"supports_vision": True, "image_api": "anthropic"}}
        llm = self._llm(
            provider="custom", model="", capability_overrides=cfg_overrides
        )

        with pytest.raises(ValueError) as excinfo:
            llm.image_part(JPEG)
        assert "不匹配" in str(excinfo.value)
