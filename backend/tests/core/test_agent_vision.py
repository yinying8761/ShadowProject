"""视觉：截图随当轮请求走，不落库（ticket 02，spec S3/S5）。

Seam: `Agent.run(images=[(bytes, mime)], vision_note=...)` + 一个假 LLM（记下真正发出去的
messages）+ 内存 SQLite（记下真正落库的东西）。两件事必须同时成立：

1. **当轮请求**里最后一条 user 消息带着图片块（形状由 `image_api` 决定，见 test_image_parts）；
2. **库里**只有用户原话和角色回复 —— 没有 base64、没有"已附上截图"这类注记，
   历史长度与不带图时完全一致（否则下一次请求会把两小时前的截图描述再喂一遍）。
"""

import base64

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.agent import Agent
from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime
from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message
from services.formatters import MessageFormatter
from services.formatters.images import text_part

JPEG = b"\xff\xd8\xff\xe0fake-screen-jpeg"
B64 = base64.b64encode(JPEG).decode("ascii")


class FakeLLMService:
    """记下每次请求的 messages，按脚本吐 token。`image_api` 模拟当前模型的能力。"""

    def __init__(self, replies: list[str] | None = None, image_api: str | None = "openai"):
        self.replies = list(replies or ["我看到了"])
        self.seen: list[list[dict]] = []
        self.image_api = image_api

    def image_part(self, data: bytes, mime: str = "image/jpeg") -> dict:
        from services.formatters.images import image_part as build

        return build(self.image_api, data, mime)

    async def estimate_prompt_tokens(self, messages):
        return 1

    async def stream_chat(self, messages, tools=None, on_retry=None):
        self.seen.append(messages)
        for ch in self.replies.pop(0) if self.replies else "":
            yield {"type": "token", "content": ch}


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory):
    async with factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔", personality="温柔", role="companion", archetype="friend"))
        s.add(Conversation(id="conv-v", character_id="c1"))
        await s.commit()


def _agent(llm) -> Agent:
    return Agent(
        llm_service=llm,
        tool_registry=ToolRuntime(ToolRegistry(), enable_tracing=False, enable_sandbox=False),
    )


async def _run(llm, session, *, images=None, vision_note=None, user_message="看看我的屏幕"):
    events = []
    async for event in _agent(llm).run(
        session,
        user_message=user_message,
        conversation_id="conv-v",
        character_id="c1",
        images=images,
        vision_note=vision_note,
    ):
        events.append(event)
    return events


def _last_user_message(messages: list[dict]) -> dict:
    return [m for m in messages if m["role"] == "user"][-1]


class TestImageRidesTheCurrentTurn:
    async def test_the_image_block_lands_on_the_current_user_message(self, factory):
        await _seed(factory)
        llm = FakeLLMService()

        async with factory() as s:
            await _run(llm, s, images=[(JPEG, "image/jpeg")], vision_note="（系统：截图已附上）")

        sent = _last_user_message(llm.seen[0])
        assert isinstance(sent["content"], list)
        # 文本在前的那个块 = 共享渲染器给出的对话记录行（`时间 [用户]: 内容`），
        # 说明图片挂的是**当轮那条**用户消息，而不是另起一条
        assert sent["content"][0]["type"] == "text"
        assert sent["content"][0]["text"].endswith("看看我的屏幕")
        assert sent["content"][1] == text_part("（系统：截图已附上）")   # 极短说明
        assert sent["content"][2] == MessageFormatter.image_part(JPEG, "image/jpeg")  # 图片在文本之后

    async def test_a_text_only_turn_keeps_its_plain_string_content(self, factory):
        """回归：不带图时请求体完全不变（纯文本对话必须是纯增量）。"""
        await _seed(factory)
        llm = FakeLLMService()

        async with factory() as s:
            await _run(llm, s)

        sent = _last_user_message(llm.seen[0])
        assert isinstance(sent["content"], str)
        assert sent["content"].endswith("看看我的屏幕")

    async def test_the_shape_comes_from_the_model_capability(self, factory):
        """换一种 image_api → 换个形状；业务代码不按厂商名分支。"""
        await _seed(factory)
        llm = FakeLLMService(image_api="anthropic")

        async with factory() as s:
            await _run(llm, s, images=[(JPEG, "image/png")])

        blocks = _last_user_message(llm.seen[0])["content"]
        assert blocks[-1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": B64},
        }


    async def test_a_note_without_an_image_is_two_text_blocks(self, factory):
        """"这次没看成"之类的说明也走同一条路（只进请求）：合法载荷 = 两个文本块。"""
        await _seed(factory)
        llm = FakeLLMService()

        async with factory() as s:
            await _run(llm, s, vision_note="（系统：这次没看成，如实告诉用户）")

        blocks = _last_user_message(llm.seen[0])["content"]
        assert isinstance(blocks, list) and len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1] == text_part("（系统：这次没看成，如实告诉用户）")


class TestAttachingIsScopedToThisTurn:
    async def test_nothing_is_attached_when_the_last_message_is_not_a_user_message(
        self, factory
    ):
        """没有"当轮用户消息"（例如主动陪伴）时：不挂上去，并且如实报错。"""
        await _seed(factory)
        async with factory() as s:
            s.add(Message(
                id="a-old", conversation_id="conv-v", role="assistant",
                content="我先说了一句", speaker_id=None,
            ))
            await s.commit()

        llm = FakeLLMService()
        async with factory() as s:
            events = []
            async for e in _agent(llm).run(
                s,
                user_message=None,
                conversation_id="conv-v",
                character_id="c1",
                images=[(JPEG, "image/jpeg")],
                vision_note="（系统：截图已附上）",
            ):
                events.append(e)

        # 一个请求都没发出去（那正是被禁止的"静默纯文本请求"），并且如实报错
        assert llm.seen == []
        assert events[-1]["type"] == "error"
        assert "截图没有发出去" in events[-1]["message"]


class TestNothingIsPersisted:
    async def test_the_database_holds_only_the_user_text_and_the_reply(self, factory):
        await _seed(factory)
        llm = FakeLLMService(["屏幕上是个编辑器"])

        async with factory() as s:
            await _run(llm, s, images=[(JPEG, "image/jpeg")], vision_note="（系统：截图已附上）")

        async with factory() as s:
            rows = (await s.execute(select(Message).order_by(Message.seq))).scalars().all()

        assert [(m.role, m.content) for m in rows] == [
            ("user", "看看我的屏幕"),
            ("assistant", "屏幕上是个编辑器"),
        ]
        joined = "\n".join(m.content for m in rows)
        assert B64 not in joined
        assert "系统：截图已附上" not in joined

    async def test_the_history_length_matches_a_text_only_turn(self, factory):
        """带图不该多出任何一条消息（图片只是一次请求的载荷，不是对话内容）。"""
        await _seed(factory)
        async with factory() as s:
            s.add(Conversation(id="conv-v2", character_id="c1"))
            await s.commit()

        async with factory() as s:      # conv-v：纯文本
            await _run(FakeLLMService(), s)
        async with factory() as s:      # conv-v2：带图
            async for _event in _agent(FakeLLMService()).run(
                s,
                user_message="看看我的屏幕",
                conversation_id="conv-v2",
                character_id="c1",
                images=[(JPEG, "image/jpeg")],
            ):
                pass

        async with factory() as s:
            counts = {
                conv_id: len((await s.execute(
                    select(Message).where(Message.conversation_id == conv_id)
                )).scalars().all())
                for conv_id in ("conv-v", "conv-v2")
            }

        assert counts == {"conv-v": 2, "conv-v2": 2}


class TestUnknownShapeIsLoud:
    async def test_an_unknown_image_api_surfaces_an_error_event(self, factory):
        """不知道按哪种形状发 → 明确报错，绝不静默降级成纯文本请求。"""
        await _seed(factory)
        llm = FakeLLMService(image_api=None)

        async with factory() as s:
            events = await _run(llm, s, images=[(JPEG, "image/jpeg")])

        assert [e["type"] for e in events] == ["error"]
        assert "image_api" in events[0]["message"]
        assert llm.seen == []  # 一个请求都没发出去

    def test_llm_service_refuses_when_the_model_has_no_shape(self):
        from services.llm_config import LLMRuntimeConfig
        from services.llm_service import LLMService

        class NoShapeRuntime(LLMRuntimeConfig):
            def image_api(self):
                return None

        llm = LLMService(runtime_config=NoShapeRuntime(provider="custom", model="mystery"))

        with pytest.raises(ValueError) as excinfo:
            llm.image_part(JPEG, "image/jpeg")
        assert "mystery" in str(excinfo.value)  # 错误里点名是哪个模型

    def test_llm_service_builds_the_shape_for_the_configured_model(self):
        from services.llm_config import LLMRuntimeConfig
        from services.llm_service import LLMService

        llm = LLMService(runtime_config=LLMRuntimeConfig(provider="anthropic"))

        assert llm.image_part(JPEG, "image/jpeg")["type"] == "image"
