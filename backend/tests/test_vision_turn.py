"""一次「看屏幕」的落地（ticket 02）：拍图 / 转述 / 报错，三选一。

Seam: `services.vision_turn.prepare_vision_turn(gate=…, can_see=…, image_api=…)` ——
注入假闸门，所以不碰真实屏幕；这里锁的是"每条路产出什么"：
挂图带 `(bytes, mime)` + 极短说明；转述把描述放进说明（不落库）；形状未知则
**有一条明确的错误**（用户看得到），而不是悄悄发个纯文本请求。
"""

import pytest

from services import vision_turn
from services.vision_turn import (
    CAPTURE_FAILED_NOTE,
    DESCRIPTION_NOTE,
    MOUNT_NOTE,
    UNAVAILABLE_MESSAGE,
    UNAVAILABLE_NOTE,
    prepare_vision_turn,
)

SCREEN = (b"fake-jpeg", "image/jpeg")


class FakeGate:
    """记下被调了哪条路。"""

    def __init__(self, *, images=SCREEN, description="屏幕上是代码编辑器"):
        self._images = images
        self._description = description
        self.captured = 0
        self.described = 0

    async def capture_images(self):
        self.captured += 1
        return self._images

    async def try_capture(self):
        self.described += 1
        return self._description


class TestMount:
    @pytest.mark.asyncio
    async def test_the_screenshot_and_the_short_note_are_returned(self):
        gate = FakeGate()

        turn = await prepare_vision_turn(gate=gate, can_see=True, image_api="openai")

        assert turn.images == (SCREEN,)
        assert turn.note == MOUNT_NOTE
        assert turn.error is None
        assert (gate.captured, gate.described) == (1, 0)   # 只有一条路被走

    @pytest.mark.asyncio
    async def test_a_denied_capture_still_answers_honestly(self):
        gate = FakeGate(images=None)

        turn = await prepare_vision_turn(gate=gate, can_see=True, image_api="openai")

        assert turn.images == ()
        assert turn.note == CAPTURE_FAILED_NOTE
        assert "没看成" in turn.note
        assert turn.error is None


class TestFallback:
    @pytest.mark.asyncio
    async def test_the_specialist_description_rides_as_a_note(self):
        gate = FakeGate()

        turn = await prepare_vision_turn(gate=gate, can_see=False, image_api="openai")

        assert turn.images == ()
        assert turn.note == DESCRIPTION_NOTE.format(description="屏幕上是代码编辑器")
        assert (gate.captured, gate.described) == (0, 1)

    @pytest.mark.asyncio
    async def test_a_failed_description_does_not_invent_a_screen(self):
        gate = FakeGate(description=None)

        turn = await prepare_vision_turn(gate=gate, can_see=False, image_api="openai")

        assert turn.note == CAPTURE_FAILED_NOTE


class TestUnavailable:
    @pytest.mark.asyncio
    async def test_an_unknown_shape_reports_an_error_and_takes_no_screenshot(self):
        gate = FakeGate()

        turn = await prepare_vision_turn(gate=gate, can_see=True, image_api=None)

        assert turn.error == UNAVAILABLE_MESSAGE
        assert "image_api" in turn.error
        assert turn.note == UNAVAILABLE_NOTE
        assert (gate.captured, gate.described) == (0, 0)   # 什么都没拍

    def test_the_note_and_the_error_say_the_same_thing(self):
        """一条给用户看（error 气泡），一条给模型看（解释为什么没图），口径必须一致。"""
        assert "image_api" in vision_turn.UNAVAILABLE_NOTE
        assert "image_api" in vision_turn.UNAVAILABLE_MESSAGE
