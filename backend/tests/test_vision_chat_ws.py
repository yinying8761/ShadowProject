"""看屏幕的入口分派（ticket 02 / spec S3）：挂图 / 转述 / 明确不可用。

Seam: `/ws/chat/{conversation_id}` 的 1:1 分支 —— 假 agent（记下它真正收到的
`images` / `vision_note`）+ 假 `ScreenCaptureGate`（不碰真实屏幕）。三种能力配置各走
一条路，而且**只有一条**被执行（另一条路被调用就直接失败）。
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation

SCREEN = (b"fake-jpeg-bytes", "image/jpeg")


class FakeCapabilities:
    """只回答 chat 层会问的两个问题（"能不能试" 与 "按哪种形状"）。"""

    def __init__(self, can_see: bool, image_api):
        self._can_see = can_see
        self._api = image_api

    def vision_should_attempt(self):
        # 与能力表一致：未知也试一次 → 调用方传 True
        return self._can_see

    def image_api(self):
        return self._api


class RecordingAgent:
    """记下 agent.run 的实参，回一个 done（这层只验分派，不验落库）。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        yield {"type": "done", "message_id": "m-a"}


def _client(monkeypatch, factory, *, agent, capture_images=None, try_capture=None):
    """最小 app + 替换掉 agent / 屏幕闸门 / 消息增强（不碰网络与真实屏幕）。"""
    monkeypatch.setattr("database.async_session", factory)

    async def _noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("services.proactive_session.ProactiveSession.start", _noop)
    monkeypatch.setattr("services.proactive_session.ProactiveSession.stop", _noop)
    monkeypatch.setattr("api.chat.agent", agent)

    from services import screen_capture_gate as gate_mod

    async def _never_capture(*_a, **_kw):
        raise AssertionError("这条路径不该截屏")

    monkeypatch.setattr(
        gate_mod.ScreenCaptureGate,
        "capture_images",
        capture_images or _never_capture,
    )
    monkeypatch.setattr(
        gate_mod.ScreenCaptureGate, "try_capture", try_capture or _never_capture
    )

    async def _passthrough(text):
        return text

    monkeypatch.setattr("services.message_augmenter.augment", _passthrough)

    from api.chat import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vision.db'}")
    asyncio.run(_create(engine))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    asyncio.run(_seed(factory))
    yield factory
    asyncio.run(engine.dispose())


async def _create(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed(factory):
    async with factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔"))
        s.add(Conversation(id="conv-v", character_id="c1"))
        await s.commit()


def _say(client, text="看看我屏幕"):
    with client.websocket_connect("/ws/chat/conv-v") as ws:
        ws.send_json({"type": "chat", "content": text, "character_id": "c1"})
        return ws.receive_json()


class TestAModelThatCanSeeMountsTheImage:
    def test_the_agent_receives_the_screenshot_and_a_short_note(
        self, env, monkeypatch
    ):
        monkeypatch.setattr(
            "services.llm_config.runtime_config", FakeCapabilities(True, "openai")
        )
        agent = RecordingAgent()
        captured: list[bool] = []

        async def capture_images(self, *args, **kwargs):
            captured.append(True)
            return SCREEN

        client = _client(monkeypatch, env, agent=agent, capture_images=capture_images)

        assert _say(client)["type"] == "done"
        assert captured == [True]
        assert agent.calls[0]["images"] == [SCREEN]
        assert agent.calls[0]["vision_note"]  # 极短说明（不落库，只给模型）
        assert "截图" in agent.calls[0]["vision_note"]

    def test_an_unknown_capability_is_still_tried(self, env, monkeypatch):
        """能力未知 → 试一试（`vision_should_attempt()` 已经把"未知"算成 True）。"""
        monkeypatch.setattr(
            "services.llm_config.runtime_config", FakeCapabilities(True, "openai")
        )
        agent = RecordingAgent()

        async def capture_images(self, *args, **kwargs):
            return SCREEN

        client = _client(monkeypatch, env, agent=agent, capture_images=capture_images)

        _say(client)
        assert agent.calls[0]["images"] == [SCREEN]


class TestAModelThatCannotSeeFallsBack:
    def test_the_specialist_description_rides_along_as_a_note(
        self, env, monkeypatch
    ):
        monkeypatch.setattr(
            "services.llm_config.runtime_config", FakeCapabilities(False, "openai")
        )
        agent = RecordingAgent()

        async def try_capture(self, *args, **kwargs):
            return "屏幕上是代码编辑器"

        client = _client(monkeypatch, env, agent=agent, try_capture=try_capture)

        _say(client)
        assert agent.calls[0]["images"] is None            # 不挂图
        assert "屏幕上是代码编辑器" in agent.calls[0]["vision_note"]  # 转述走 note（不落库）


class TestAnUnknownShapeIsLoud:
    def test_an_error_event_and_an_explicit_note(self, env, monkeypatch):
        """不知道怎么发图 → 不截屏、不静默发纯文本：先给用户一个明确的错误。"""
        monkeypatch.setattr(
            "services.llm_config.runtime_config", FakeCapabilities(True, None)
        )
        agent = RecordingAgent()
        client = _client(monkeypatch, env, agent=agent)

        first = _say(client)

        assert first["type"] == "error"
        assert "image_api" in first["message"]
        # 角色仍然回答一句（说明为什么没看成），但这一轮没有图
        assert agent.calls[0]["images"] is None
        assert "image_api" in agent.calls[0]["vision_note"]


class TestADeniedCapture:
    def test_the_turn_still_answers_without_images(self, env, monkeypatch):
        monkeypatch.setattr(
            "services.llm_config.runtime_config", FakeCapabilities(True, "openai")
        )
        agent = RecordingAgent()

        async def capture_images(self, *args, **kwargs):
            return None  # 用户拒绝 / 截图失败

        client = _client(monkeypatch, env, agent=agent, capture_images=capture_images)

        assert _say(client)["type"] == "done"
        assert agent.calls[0]["images"] is None
        assert "没看成" in agent.calls[0]["vision_note"]
