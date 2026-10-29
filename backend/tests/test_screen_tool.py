"""屏幕工具（ticket 02 / 05）：截图闸门 + `see_screen` 的返回契约。

- `ScreenCaptureGate`：审批、失败、以及"用户发起就永不去重"
- `see_screen`：工具契约是字符串（JSON），主模型优先、描述带 `vision_backend`，
  失败/不可用返回 `error` 而不是一段编出来的"描述"

这里的 MCP 视觉适配已经删除（ticket 05）：`see_screen` 不再认识任何 MCP server 的
工具名/参数名 —— 看图的决定在 `services/vision_tool.py`。
"""

import json

import pytest


class TestScreenCaptureGate:
    """The gate is what turns a visual result into the chat context.

    2026-10-20: a 429 stack went through this seam as a "description", landed in
    a *user* message, and the character then recited it for hours. An error
    result must come back as None (→ a neutral note in the turn), not as text.
    """

    @pytest.mark.asyncio
    async def test_error_result_is_not_returned_as_a_description(self, monkeypatch):
        from services import screen_capture_gate as gate_mod

        sent: list[dict] = []

        async def send_json(payload):
            sent.append(payload)

        async def approval_callback(tool_name, arguments):
            return True

        async def get_user_config():
            return None

        async def fake_see_screen(focus=None, monitor=0):
            return json.dumps(
                {"error": "视觉服务暂时不可用（Error code: 429）"}, ensure_ascii=False
            )

        monkeypatch.setattr(gate_mod, "see_screen", fake_see_screen)
        monkeypatch.setattr(gate_mod, "_capture_dhash_sync", lambda: "hash-1")

        gate = gate_mod.ScreenCaptureGate(
            send_json=send_json,
            approval_callback=approval_callback,
            get_user_config=get_user_config,
            fingerprints=gate_mod.ScreenFingerprintStore(maxlen=3),
        )

        assert await gate.try_capture() is None

        results = [p for p in sent if p.get("type") == "tool_result"]
        assert results, "the tool call should still be reported to the UI"
        assert results[-1]["is_error"] is True

    @pytest.mark.asyncio
    async def test_success_result_is_returned(self, monkeypatch):
        from services import screen_capture_gate as gate_mod

        async def send_json(payload):
            return None

        async def approval_callback(tool_name, arguments):
            return True

        async def get_user_config():
            return None

        async def fake_see_screen(focus=None, monitor=0):
            return json.dumps({"description": "屏幕上是代码编辑器"}, ensure_ascii=False)

        monkeypatch.setattr(gate_mod, "see_screen", fake_see_screen)
        monkeypatch.setattr(gate_mod, "_capture_dhash_sync", lambda: "hash-2")

        gate = gate_mod.ScreenCaptureGate(
            send_json=send_json,
            approval_callback=approval_callback,
            get_user_config=get_user_config,
            fingerprints=gate_mod.ScreenFingerprintStore(maxlen=3),
        )

        assert await gate.try_capture() == "屏幕上是代码编辑器"


class TestCaptureImages:
    """ticket 02：把截图**原样**交出去给主模型看，不经过任何视觉模型。

    `capture_images` 与 `try_capture` 共用同一道审批与前端事件，但返回的是
    `(jpeg_bytes, mime)`；它**不去重**（用户主动说"看看"，同一屏也要真的再看一次）。
    """

    @staticmethod
    def _gate(*, approved=True, sent=None):
        from services import screen_capture_gate as gate_mod

        async def send_json(payload):
            if sent is not None:
                sent.append(payload)

        async def approval_callback(tool_name, arguments):
            return approved

        async def get_user_config():
            return None

        gate = gate_mod.ScreenCaptureGate(
            send_json=send_json,
            approval_callback=approval_callback,
            get_user_config=get_user_config,
            fingerprints=gate_mod.ScreenFingerprintStore(maxlen=3),
        )
        return gate_mod, gate

    @pytest.mark.asyncio
    async def test_returns_the_jpeg_bytes_and_never_calls_a_model(
        self, monkeypatch
    ):
        sent: list[dict] = []
        gate_mod, gate = self._gate(sent=sent)
        calls = {"capture": 0}

        def fake_capture(monitor_index=0):
            calls["capture"] += 1
            return b"fake-jpeg", 1280, 720

        async def boom(*_a, **_kw):
            raise AssertionError("这条路径不该调用任何视觉模型")

        monkeypatch.setattr(gate_mod, "_capture_sync", fake_capture)
        monkeypatch.setattr(gate_mod, "dhash_from_jpeg", lambda _bytes: 12345)
        monkeypatch.setattr(gate_mod, "see_screen", boom)

        assert await gate.capture_images() == (b"fake-jpeg", "image/jpeg")
        assert calls["capture"] == 1
        # 前端仍要看到一次工具调用（tool_use → tool_result），否则按钮会一直转
        kinds = [p.get("type") for p in sent]
        assert kinds == ["tool_use", "tool_result"]
        assert sent[-1]["is_error"] is False

    @pytest.mark.asyncio
    async def test_a_denied_capture_returns_none_and_takes_no_screenshot(
        self, monkeypatch
    ):
        sent: list[dict] = []
        gate_mod, gate = self._gate(approved=False, sent=sent)

        def never(monitor_index=0):
            raise AssertionError("用户拒绝了，不该截图")

        monkeypatch.setattr(gate_mod, "_capture_sync", never)

        assert await gate.capture_images() is None
        assert [p.get("type") for p in sent] == ["tool_result"]
        assert sent[0]["denied"] is True

    @pytest.mark.asyncio
    async def test_a_failed_capture_returns_none_with_an_error_result(
        self, monkeypatch
    ):
        sent: list[dict] = []
        gate_mod, gate = self._gate(sent=sent)

        def broken(monitor_index=0):
            raise RuntimeError("no display")

        monkeypatch.setattr(gate_mod, "_capture_sync", broken)

        assert await gate.capture_images() is None
        assert sent[-1]["type"] == "tool_result"
        assert sent[-1]["is_error"] is True

    @pytest.mark.asyncio
    async def test_the_same_screen_is_captured_again_without_asking(
        self, monkeypatch
    ):
        """用户发起的看屏幕**永不去重**：同一屏连说两次，两次都真的去拍。"""
        gate_mod, gate = self._gate()
        calls = {"capture": 0}

        def fake_capture(monitor_index=0):
            calls["capture"] += 1
            return b"same-screen", 800, 600

        monkeypatch.setattr(gate_mod, "_capture_sync", fake_capture)
        # 指纹从**已经拍到的那张图**算（不再为了指纹多截一次屏）
        monkeypatch.setattr(gate_mod, "dhash_from_jpeg", lambda _bytes: 999)

        assert await gate.capture_images() is not None
        assert await gate.capture_images() is not None
        assert calls["capture"] == 2


class TestSeeScreenContract:
    """工具契约：`see_screen` 永远返回字符串；描述带 `vision_backend`，失败带 `error`。"""

    @staticmethod
    def _patch(monkeypatch, *, result):
        from tools import screen_tools

        captured: list[int] = []

        def fake_capture(monitor_index=0):
            captured.append(monitor_index)
            return b"jpeg-bytes", 1280, 720

        async def fake_describe(**kwargs):
            return result

        monkeypatch.setattr(screen_tools, "_capture_sync", fake_capture)
        monkeypatch.setattr(screen_tools, "describe_screen", fake_describe)
        return screen_tools, captured

    @pytest.mark.asyncio
    async def test_a_main_model_description_is_reported_with_its_backend(
        self, monkeypatch
    ):
        from services.vision_tool import ScreenDescription

        screen_tools, captured = self._patch(
            monkeypatch,
            result=ScreenDescription("屏幕上是浏览器", "main"),
        )

        payload = json.loads(await screen_tools.see_screen(focus="有报错吗"))

        assert payload["description"] == "屏幕上是浏览器"
        assert payload["vision_backend"] == "main"
        assert payload["image_size"] == "1280x720"
        assert captured == [0]          # 真拍了一张

    @pytest.mark.asyncio
    async def test_the_specialist_backend_is_reported_too(self, monkeypatch):
        from services.vision_tool import ScreenDescription

        screen_tools, _ = self._patch(
            monkeypatch, result=ScreenDescription("专家：屏幕上是浏览器", "specialist")
        )

        payload = json.loads(await screen_tools.see_screen())

        assert payload["vision_backend"] == "specialist"

    @pytest.mark.asyncio
    async def test_no_backend_is_an_error_key_and_no_description(self, monkeypatch):
        from services.vision_tool import NO_BACKEND_MESSAGE, ScreenDescription

        screen_tools, _ = self._patch(
            monkeypatch, result=ScreenDescription(error=NO_BACKEND_MESSAGE)
        )

        payload = json.loads(await screen_tools.see_screen())

        assert "error" in payload
        assert "description" not in payload
        assert "不支持看图" in payload["error"]
        assert "。。" not in payload["error"]      # 句号不叠

    @pytest.mark.asyncio
    async def test_a_failed_capture_is_an_error_key(self, monkeypatch):
        from tools import screen_tools

        def broken(monitor_index=0):
            raise RuntimeError("no display")

        monkeypatch.setattr(screen_tools, "_capture_sync", broken)

        payload = json.loads(await screen_tools.see_screen())

        assert "error" in payload
        assert "Screen capture failed" in payload["error"]

    @pytest.mark.asyncio
    async def test_two_looks_take_two_screenshots(self, monkeypatch):
        """工具路径不去重：角色想再看一眼就再拍一次。"""
        from services.vision_tool import ScreenDescription

        screen_tools, captured = self._patch(
            monkeypatch, result=ScreenDescription("看看", "main")
        )

        await screen_tools.see_screen()
        await screen_tools.see_screen()

        assert captured == [0, 0]
