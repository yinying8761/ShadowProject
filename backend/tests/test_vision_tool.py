"""`see_screen` 工具里的看图（ticket 05 / spec S4）：主模型优先，专家模型兜底。

Seam: `services.vision_tool.describe_screen(…)` —— 注入"主模型"（假 LLM）与"专家模型"
（假 vision_service），所以不碰网络也不碰真实屏幕。必须成立的事：

1. 主模型能看图 → **只用**主模型（专家模型一次都不碰）；
2. 主模型看不了（能力明确为否）→ 回落专家模型；
3. 主模型**该看却没看**（形状配错 / 空回复）→ **明确报错**，不换模型顶替；
4. 专家模型失败 / 没配置 → **明确报错**，绝不把错误文本当成屏幕描述；
5. 两者都没有 → 明确的不可用错误。
"""

import base64

import pytest

from services.vision_service import VisionUnavailable
from services.vision_tool import (
    EMPTY_REPLY_MESSAGE,
    NO_BACKEND_MESSAGE,
    SHAPE_PROBLEM_MESSAGE,
    ScreenDescription,
    describe_screen,
)

JPEG = b"\xff\xd8\xff\xe0fake-screen"
B64 = base64.b64encode(JPEG).decode("ascii")


class FakeRuntime:
    def __init__(self, *, can_see: bool, image_api: str | None = "openai"):
        self._can_see = can_see
        self._api = image_api

    def vision_should_attempt(self) -> bool:
        return self._can_see

    def image_api(self):
        return self._api


class FakeLLM:
    """假的"主模型"：记下收到的 messages，返回固定描述。

    `image_api=None` 模拟"不知道怎么发图"—— 与真 `LLMService.image_part` 同一个契约：
    形状未知就抛 `ValueError`。
    """

    def __init__(
        self,
        reply: str = "屏幕上是代码编辑器",
        *,
        error: Exception | None = None,
        image_api: str | None = "openai",
    ):
        self._reply = reply
        self._error = error
        self._image_api = image_api
        self.seen: list[list[dict]] = []

    def image_part(self, data: bytes, mime: str = "image/jpeg") -> dict:
        if self._image_api is None:
            raise ValueError("unknown image_api: None")
        from services.formatters import MessageFormatter

        return MessageFormatter.image_part(data, mime)

    async def chat_sync(self, messages, max_tokens=400, temperature=0.2):
        self.seen.append(messages)
        if self._error is not None:
            raise self._error
        return self._reply


class FakeSpecialist:
    def __init__(self, *, description: str = "专家模型：屏幕上是浏览器", error=None):
        self._description = description
        self._error = error
        self.calls: list[dict] = []

    async def describe_image(self, image_bytes, mime_type="image/jpeg", focus=None):
        self.calls.append({"bytes": image_bytes, "mime": mime_type, "focus": focus})
        if self._error is not None:
            raise self._error
        return self._description


class TestMainModelPath:
    @pytest.mark.asyncio
    async def test_the_main_model_describes_the_screen_and_the_specialist_is_never_called(self):
        llm = FakeLLM()
        specialist = FakeSpecialist()

        result = await describe_screen(
            image_bytes=JPEG,
            focus="屏幕上有报错吗",
            runtime=FakeRuntime(can_see=True),
            llm=llm,
            specialist=specialist,
        )

        assert result == ScreenDescription("屏幕上是代码编辑器", "main")
        assert specialist.calls == []          # 专家模型一次都没被碰
        # 图片真的发出去了：最后一个 content block 是图片块
        blocks = llm.seen[0][-1]["content"]
        assert blocks[-1] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{B64}"},
        }
        assert "屏幕上有报错吗" in blocks[0]["text"]   # focus 进了提问


class TestMainModelFailuresAreLoud:
    @pytest.mark.asyncio
    async def test_an_empty_main_model_answer_is_an_error_not_a_model_switch(self):
        """主模型该看却什么都没说 —— 换成专家模型就是另一个模型在替它回答。"""
        specialist = FakeSpecialist()

        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=True),
            llm=FakeLLM(reply="   "),
            specialist=specialist,
        )

        assert result.error == EMPTY_REPLY_MESSAGE
        assert specialist.calls == []

    @pytest.mark.asyncio
    async def test_an_unknown_shape_is_an_error_not_a_silent_fallback(self):
        """spec：不认识的 image_api 必须报错（静默降级会让配置错误永远藏着）。"""
        llm = FakeLLM(image_api=None)
        specialist = FakeSpecialist()

        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=True, image_api=None),
            llm=llm,
            specialist=specialist,
        )

        assert result.error.startswith("视觉不可用")
        assert "image_api" in result.error
        assert llm.seen == [] and specialist.calls == []

    @pytest.mark.asyncio
    async def test_a_shape_that_does_not_match_the_sdk_is_reported(self):
        llm = FakeLLM(image_api=None)       # image_part 抛 ValueError
        specialist = FakeSpecialist()

        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=True, image_api="anthropic"),
            llm=llm,
            specialist=specialist,
        )

        assert result.error.startswith("视觉不可用")
        assert specialist.calls == []

    @pytest.mark.asyncio
    async def test_a_main_model_runtime_error_still_raises(self):
        """限流/断网是故障：如实抛出（see_screen 会转成工具错误），不换模型顶替。"""
        specialist = FakeSpecialist()

        with pytest.raises(RuntimeError):
            await describe_screen(
                image_bytes=JPEG,
                runtime=FakeRuntime(can_see=True),
                llm=FakeLLM(error=RuntimeError("429")),
                specialist=specialist,
            )
        assert specialist.calls == []


class TestCapabilityFallback:
    @pytest.mark.asyncio
    async def test_a_model_that_cannot_see_uses_the_specialist(self):
        llm = FakeLLM()
        specialist = FakeSpecialist()

        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=False),
            llm=llm,
            specialist=specialist,
        )

        assert result == ScreenDescription("专家模型：屏幕上是浏览器", "specialist")
        assert llm.seen == []                   # 主模型一次都没被调
        assert specialist.calls[0]["mime"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_no_specialist_configured_is_an_explicit_error(self):
        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=False),
            llm=FakeLLM(),
            specialist=None,                    # 明确没有专家模型（不碰全局 settings）
        )

        assert result.error == NO_BACKEND_MESSAGE
        assert "不支持看图" in result.error
        assert result.description is None


class TestSpecialistFailures:
    """专家模型的失败**绝不能**变成一段"描述"（2026-10-20 的 429 复读事故）。"""

    @pytest.mark.asyncio
    async def test_a_vision_unavailable_error_is_reported_as_an_error(self):
        specialist = FakeSpecialist(error=VisionUnavailable("视觉模型调用失败（429）"))

        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=False),
            llm=FakeLLM(),
            specialist=specialist,
        )

        assert result.description is None
        assert "429" in result.error
        assert "没看成" in result.error

    @pytest.mark.asyncio
    async def test_an_unexpected_exception_is_reported_as_an_error(self):
        specialist = FakeSpecialist(error=RuntimeError("boom"))

        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=False),
            llm=FakeLLM(),
            specialist=specialist,
        )

        assert result.description is None
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_an_empty_specialist_answer_is_an_error(self):
        result = await describe_screen(
            image_bytes=JPEG,
            runtime=FakeRuntime(can_see=False),
            llm=FakeLLM(),
            specialist=FakeSpecialist(description=""),
        )

        assert result.error == NO_BACKEND_MESSAGE
