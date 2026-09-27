"""群聊补账（ticket #55）：打开群对话时"先提取 → 后 compact"。

两层：
- `batch_windows`（纯函数）：把 [锚点, 现在) 切成"尽量按天、最多 N 批"的窗口 ——
  跨多日补账要按天补，记忆时间才不会把三天前记成今天。
- `GroupMemoryCatchUp`（编排）：取窗口 → 提取一次 → 每个成员各存一份 → 再 compact；
  锚点只推进到确实处理过的地方。

Seam: `services/group_memory.py`。测试注入假 LLM + 内存库（ticket 要求）。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.group_memory import DayWindow, batch_windows
from services.memory_extractor import ExtractedMemory


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestBatchWindows:
    def test_one_day_when_the_anchor_is_today(self):
        until = _utc(2026, 9, 20, 10, 30)

        windows = batch_windows(_utc(2026, 9, 20, 9, 0), until)

        assert windows == [
            DayWindow(
                since_date="2026-09-20",
                before=until,
                created_at=_utc(2026, 9, 20, 0, 0),
            )
        ]

    def test_one_window_per_day_inside_the_window(self):
        until = _utc(2026, 9, 20, 10, 0)

        windows = batch_windows(_utc(2026, 9, 18, 9, 0), until)

        assert [w.since_date for w in windows] == ["2026-09-18", "2026-09-19", "2026-09-20"]
        assert [w.created_at for w in windows] == [
            _utc(2026, 9, 18, 0, 0),
            _utc(2026, 9, 19, 0, 0),
            _utc(2026, 9, 20, 0, 0),
        ]
        # 窗口首尾相接，最后一批到"现在"为止
        assert windows[0].before == _utc(2026, 9, 19, 0, 0)
        assert windows[1].before == _utc(2026, 9, 20, 0, 0)
        assert windows[2].before == until

    def test_no_anchor_starts_max_batches_days_back(self):
        until = _utc(2026, 9, 20, 10, 0)

        windows = batch_windows(None, until, max_batches=7)

        assert len(windows) == 7
        assert windows[0].since_date == "2026-09-14"
        assert windows[-1].since_date == "2026-09-20"

    def test_long_absence_merges_the_oldest_days_into_one_batch(self):
        """离开太久：最老的那些天并成一批（内容一条不丢，日期也不会说成今天）。"""
        until = _utc(2026, 9, 20, 10, 0)

        windows = batch_windows(_utc(2026, 9, 10, 9, 0), until, max_batches=3)

        assert [(w.since_date, w.created_at) for w in windows] == [
            ("2026-09-10", _utc(2026, 9, 10, 0, 0)),
            ("2026-09-19", _utc(2026, 9, 19, 0, 0)),
            ("2026-09-20", _utc(2026, 9, 20, 0, 0)),
        ]
        # 合并批覆盖 09-10..09-18，到 09-19 00:00 为止
        assert windows[0].before == _utc(2026, 9, 19, 0, 0)
        assert windows[1].before == _utc(2026, 9, 20, 0, 0)

    def test_naive_anchor_from_the_db_is_treated_as_utc(self):
        """库里读出来的锚点是 naive 的（全库惯例 = UTC），不该炸。"""
        until = _utc(2026, 9, 20, 10, 0)

        windows = batch_windows(datetime(2026, 9, 19, 9, 0), until)

        assert [w.since_date for w in windows] == ["2026-09-19", "2026-09-20"]

    def test_anchor_in_the_future_yields_nothing(self):
        assert batch_windows(_utc(2026, 9, 21, 0, 0), _utc(2026, 9, 20, 10, 0)) == []


# ── 编排：GroupMemoryCatchUp ─────────────────────────────────────────────


def _msg(role: str, content: str, speaker_id: str | None = None):
    """够用的消息替身：渲染器只读 role/content/created_at/speaker_id/tool_calls。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        role=role, content=content, speaker_id=speaker_id,
        created_at=datetime(2026, 9, 20, tzinfo=timezone.utc), tool_calls=None,
    )


class SpyExtractor:
    """假提取器：记住每次调用，按 `script` 回答，可指定哪一批失败。"""

    def __init__(self, *, script=None, items=None, fail_on=(), log=None):
        self.script = script or {}
        self.items = items or {"any": []}  # 提取出来的条目（`list[ExtractedMemory]`，与真提取器同型）
        self.fail_on = set(fail_on)
        self.log = log if log is not None else []
        self.load_calls: list[str | None] = []
        self.extract_calls: list[dict] = []
        self.store_calls: list[dict] = []

    async def load_window(self, conversation_id, *, since_date=None, before=None):
        self.load_calls.append(since_date)
        return self.script.get(since_date, [])

    async def extract(self, transcript, llm_service, *, prompt_scope=None, user_name_hint=""):
        self.extract_calls.append({"prompt_scope": prompt_scope, "transcript": transcript})
        since = self.load_calls[-1]
        if since in self.fail_on:
            raise RuntimeError("LLM 炸了")
        self.log.append("extract")
        return self.items.get("any", [])

    async def store(self, items, *, conversation_id, character_id=None, created_at=None, notify=True):
        self.store_calls.append({
            "character_id": character_id, "created_at": created_at,
            "conversation_id": conversation_id, "count": len(items), "notify": notify,
        })
        self.log.append(f"store:{character_id}")
        return list(items)


class SpyConversations:
    """假 compact：只记录"什么时候被调用"，用来断言提取在前、裁剪在后。"""

    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.calls: list[str] = []

    async def resolve_speaker_names(self, session, messages):
        """真实实现按 speaker_id 查角色名；补账测试只关心编排，用 id 顶上。"""
        return {
            m.speaker_id: m.speaker_id
            for m in messages
            if getattr(m, "speaker_id", None)
        }

    async def mark_extracted(self, session, conversation_id, when):
        """真实实现把锚点写回会话；补账测试要能看见锚点真的动了。"""
        from models.conversation import Conversation

        conv = await session.get(Conversation, conversation_id)
        if conv is not None:
            conv.last_extract_at = when
            await session.commit()

    async def summarize_and_trim(self, session, conversation_id, **kwargs):
        self.calls.append(conversation_id)
        self.log.append("compact")
        return {"deleted": 0, "summary": ""}


@pytest.fixture
async def factory():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from database import Base

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed_group(factory, *, members=("c1", "c2"), anchor=None):
    from models.character import CharacterProfile
    from models.conversation import Conversation
    from models.group import Group, GroupMember

    async with factory() as s:
        for index, character_id in enumerate(members):
            s.add(CharacterProfile(id=character_id, name=f"角色{index + 1}"))
            s.add(GroupMember(group_id="g1", character_id=character_id, position=index))
        s.add(Group(id="g1", name="深夜食堂"))
        s.add(Conversation(id="conv-g", character_id=None, group_id="g1", last_extract_at=anchor))
        await s.commit()


async def _anchor(factory) -> datetime | None:
    from models.conversation import Conversation

    async with factory() as s:
        conv = await s.get(Conversation, "conv-g")
        return conv.last_extract_at


NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _catch_up(factory, extractor, conversations, **kwargs):
    from services.group_memory import GroupMemoryCatchUp

    return GroupMemoryCatchUp(
        extractor=extractor,
        conversation_manager=conversations,
        llm_service=object(),  # 假提取器不真的用 LLM
        session_factory=factory,
        now=lambda: NOW,
        **kwargs,
    )


class TestCatchUp:
    @pytest.mark.asyncio
    async def test_one_extraction_per_window_and_one_copy_per_member(self, factory):
        await _seed_group(factory, anchor=datetime(2026, 9, 20, 9, 0))
        log: list[str] = []
        extractor = SpyExtractor(script={"2026-09-20": [_msg("user", "我最近在学 Rust")]}, log=log)
        extractor.items = {"any": [ExtractedMemory("记忆A"), ExtractedMemory("记忆B")]}
        conversations = SpyConversations(log)

        result = await _catch_up(factory, extractor, conversations).run("conv-g")

        assert len(extractor.extract_calls) == 1  # 一次提取
        assert extractor.extract_calls[0]["prompt_scope"] == "group_user_facts"
        assert [c["character_id"] for c in extractor.store_calls] == ["c1", "c2"]  # 每人一份
        assert all(c["created_at"] == datetime(2026, 9, 20, tzinfo=timezone.utc) for c in extractor.store_calls)
        assert all(c["notify"] is False for c in extractor.store_calls)  # 通知由补账汇总
        # 今天的窗口里只有一天有内容 → 一次提取、每人一份
        assert (result.windows, result.extracted, result.stored) == (1, 2, 4)
        assert result.completed is True

    @pytest.mark.asyncio
    async def test_each_days_memories_are_dated_to_that_day(self, factory):
        await _seed_group(factory, members=("c1",), anchor=datetime(2026, 9, 18, 9, 0))
        extractor = SpyExtractor(script={
            "2026-09-18": [_msg("user", "前天那句")],
            "2026-09-20": [_msg("user", "今天这句")],
        })
        extractor.items = {"any": [ExtractedMemory("一条记忆")]}

        await _catch_up(factory, extractor, SpyConversations()).run("conv-g")

        assert [c["created_at"].date().isoformat() for c in extractor.store_calls] == [
            "2026-09-18",
            "2026-09-20",
        ]

    @pytest.mark.asyncio
    async def test_compact_runs_after_extraction(self, factory):
        await _seed_group(factory, anchor=datetime(2026, 9, 20, 9, 0))
        log: list[str] = []
        extractor = SpyExtractor(script={"2026-09-20": [_msg("user", "一句话")]}, log=log)
        extractor.items = {"any": [ExtractedMemory("一条记忆")]}
        conversations = SpyConversations(log)

        await _catch_up(factory, extractor, conversations).run("conv-g")

        assert conversations.calls == ["conv-g"]
        assert log == ["extract", "store:c1", "store:c2", "compact"]  # 先提取 → 后裁剪

    @pytest.mark.asyncio
    async def test_the_anchor_advances_to_the_snapshot(self, factory):
        await _seed_group(factory, anchor=datetime(2026, 9, 18, 9, 0))
        extractor = SpyExtractor(script={"2026-09-18": [_msg("user", "前天")], "2026-09-19": [], "2026-09-20": []})

        result = await _catch_up(factory, extractor, SpyConversations()).run("conv-g")

        assert result.windows == 3
        assert (await _anchor(factory)) == NOW.replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_a_failed_extraction_stops_the_anchor_and_skips_compact(self, factory):
        """提取失败就别裁剪 —— 裁掉的消息还没变成记忆，等于丢内容。"""
        await _seed_group(factory, members=("c1",), anchor=datetime(2026, 9, 18, 9, 0))
        log: list[str] = []
        extractor = SpyExtractor(
            script={"2026-09-18": [_msg("user", "前天")], "2026-09-19": [_msg("user", "昨天")]},
            fail_on=("2026-09-19",),
            log=log,
        )
        extractor.items = {"any": [ExtractedMemory("一条记忆")]}
        conversations = SpyConversations(log)

        result = await _catch_up(factory, extractor, conversations).run("conv-g")

        assert result.completed is False
        assert conversations.calls == []          # 没裁剪
        assert "compact" not in log
        # 锚点只走到"确实处理过"的地方：09-19 00:00
        assert (await _anchor(factory)) == datetime(2026, 9, 19)

    @pytest.mark.asyncio
    async def test_a_group_without_members_backs_off_without_doing_work(self, factory):
        await _seed_group(factory, members=(), anchor=datetime(2026, 9, 20, 9, 0))
        extractor = SpyExtractor(script={"2026-09-20": [_msg("user", "没人听")]})
        conversations = SpyConversations()

        result = await _catch_up(factory, extractor, conversations).run("conv-g")

        assert extractor.extract_calls == [] and extractor.store_calls == []
        assert conversations.calls == []
        assert result.windows == 0
        assert (await _anchor(factory)) == NOW.replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_a_conversation_never_backfilled_covers_its_whole_history(self, factory):
        """从没补过账（last_extract_at NULL）：窗口 = 全部历史。

        只往前数几天的话，更老的消息会被后面的 compact 直接删掉 —— 那正是
        "先提取后裁剪"要避免的。
        """
        await _seed_group(factory, members=("c1",))
        async with factory() as s:
            from models.message import Message

            s.add(Message(
                conversation_id="conv-g", role="user", content="很久以前那句",
                created_at=datetime(2026, 9, 8, 6, 0),
            ))
            await s.commit()
        extractor = SpyExtractor(script={"2026-09-08": [_msg("user", "很久以前那句")]})

        await _catch_up(factory, extractor, SpyConversations()).run("conv-g")

        # 最老的那天在窗口里（合并批的起点），并且真的被提取了
        assert extractor.load_calls[0] == "2026-09-08"
        assert any(call["transcript"] for call in extractor.extract_calls)

    @pytest.mark.asyncio
    async def test_an_empty_window_still_moves_the_anchor(self, factory):
        await _seed_group(factory, members=("c1",), anchor=datetime(2026, 9, 19, 9, 0))
        extractor = SpyExtractor(script={})  # 没有任何消息

        result = await _catch_up(factory, extractor, SpyConversations()).run("conv-g")

        assert extractor.extract_calls == []
        assert result.completed is True
        assert (await _anchor(factory)) == NOW.replace(tzinfo=None)


class TestCatchUpWithTheRealExtractor:
    """真提取器 + 假 LLM + 内存库：把"提取一次 → 每人一份 → 再 compact"整条链跑通。"""

    @pytest.mark.asyncio
    async def test_end_to_end(self, factory, monkeypatch):
        import json

        from sqlalchemy import select

        from core.conversation_manager import ConversationManager
        from models.character import CharacterProfile
        from models.conversation import Conversation
        from models.group import Group, GroupMember
        from models.memory import Memory
        from models.message import Message
        from services.group_memory import GroupMemoryCatchUp
        from services.memory_extractor import MemoryExtractor
        from services.memory_store import MemoryStore

        import services.memory_extractor as me

        monkeypatch.setattr(me, "async_session", factory)

        day = datetime(2026, 9, 18, 6, 0, tzinfo=timezone.utc)
        async with factory() as s:
            s.add(CharacterProfile(id="c1", name="小柔"))
            s.add(CharacterProfile(id="c2", name="阿B"))
            s.add(Group(id="g1", name="深夜食堂"))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            s.add(GroupMember(group_id="g1", character_id="c2", position=1))
            s.add(Conversation(id="conv-g", character_id=None, group_id="g1", last_extract_at=day))
            for i in range(14):
                s.add(Message(conversation_id="conv-g", role="user", content=f"用户第{i}句", created_at=day))
                s.add(Message(
                    conversation_id="conv-g", role="assistant", content=f"角色第{i}句",
                    speaker_id="c1" if i % 2 == 0 else "c2", created_at=day,
                ))
            await s.commit()

        prompts: list[str] = []

        class FakeLLM:
            async def chat_sync(self, messages, max_tokens=1024, temperature=0.3):
                prompt = messages[-1]["content"]
                prompts.append(prompt)
                if "记忆记录员" in prompt:
                    return json.dumps([
                        {"content": "用户最近在学 Rust", "memory_type": "user_fact", "importance": 6}
                    ])
                return "那几天聊了他学 Rust"

        # 通知队列是模块级共享状态：先清掉同文件其他测试留下的计数，再断言"这次"的通知
        from services.memory_service import pop_memory_notifications

        pop_memory_notifications("conv-g")

        result = await GroupMemoryCatchUp(
            extractor=MemoryExtractor(MemoryStore()),
            conversation_manager=ConversationManager(),
            llm_service=FakeLLM(),
            session_factory=factory,
            now=lambda: NOW,
        ).run("conv-g")

        async with factory() as s:
            rows = (await s.execute(select(Memory).order_by(Memory.character_id))).scalars().all()
            conv = await s.get(Conversation, "conv-g")

        # 一次提取 → 每人一份，且都是那天的记忆
        assert [(m.character_id, m.content) for m in rows] == [
            ("c1", "用户最近在学 Rust"),
            ("c2", "用户最近在学 Rust"),
        ]
        assert {m.created_at.date().isoformat() for m in rows} == {"2026-09-18"}
        assert (result.extracted, result.stored) == (1, 2)

        # 顺序：先提取（群聊口径的提示词）→ 后 compact（摘要落到会话上）
        assert any("只提取**关于用户的持久事实**" in p for p in prompts)
        assert conv.summary == "那几天聊了他学 Rust"
        assert conv.last_extract_at == NOW.replace(tzinfo=None)

        # 通知一次性汇总：2 行记忆 = 一次通知（`notify=False` 真的把 MemoryStore.add
        # 的逐条通知也关掉了，否则这里会是 2 行 × N 次）
        assert pop_memory_notifications("conv-g") == 2

    @pytest.mark.asyncio
    async def test_a_merged_window_without_occurred_on_is_dated_to_its_first_day(
        self, factory, monkeypatch
    ):
        """P1：合并窗 + 模型漏 `occurred_on` → 退回该批最早一天，但**必须看得见**。

        这不是"应该这样"，而是把已知行为锁住：单日窗退回它无所谓（那天就是那天），
        合并窗会把近几天的事记成最老那天。精确日期靠提示词拿到 `occurred_on`；
        见 docs/analysis/group-chat-phase2-review-fixes.md §2.1。
        """
        import json

        from sqlalchemy import select

        from core.conversation_manager import ConversationManager
        from models.character import CharacterProfile
        from models.conversation import Conversation
        from models.group import Group, GroupMember
        from models.memory import Memory
        from models.message import Message
        from services.group_memory import GroupMemoryCatchUp
        from services.memory_extractor import MemoryExtractor
        from services.memory_store import MemoryStore

        import services.memory_extractor as me

        monkeypatch.setattr(me, "async_session", factory)

        # 锚点比 NOW 早 19 天 → 超过 MAX_BATCHES=7：最老的 14 天并成一批（09-01..09-14）
        anchor = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        in_merged_window = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)
        async with factory() as s:
            s.add(CharacterProfile(id="c1", name="小柔"))
            s.add(Group(id="g1", name="深夜食堂"))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            s.add(Conversation(
                id="conv-g", character_id=None, group_id="g1", last_extract_at=anchor,
            ))
            s.add(Message(
                conversation_id="conv-g", role="user",
                content="我这几天在赶项目", created_at=in_merged_window,
            ))
            await s.commit()

        class FakeLLM:
            async def chat_sync(self, messages, max_tokens=1024, temperature=0.3):
                return json.dumps([
                    {"content": "用户最近在赶项目", "memory_type": "important_event", "importance": 6}
                ])  # 刻意不给 occurred_on

        result = await GroupMemoryCatchUp(
            extractor=MemoryExtractor(MemoryStore()),
            conversation_manager=ConversationManager(),
            llm_service=FakeLLM(),
            session_factory=factory,
            now=lambda: NOW,
        ).run("conv-g")

        async with factory() as s:
            rows = (await s.execute(select(Memory))).scalars().all()

        assert result.completed is True
        # 已知回退：整批标成 09-01，而不是消息真实的 09-14
        assert {m.created_at.date().isoformat() for m in rows} == {"2026-09-01"}

        # 但绝不静默：`[GroupMemory] catch-up …` 汇总行看不出这件事，所以单独警告一行，
        # 并把条数从 `CatchUpResult.undated` 带出来（断言结构化结果，不解析日志文本）
        assert result.undated == 1

        # 模块级通知队列：别把残留留给别的测试
        from services.memory_service import pop_memory_notifications

        pop_memory_notifications("conv-g")


class TestInFlightDedup:
    @pytest.mark.asyncio
    async def test_a_second_schedule_reuses_the_running_task(self):
        import services.group_memory as gm

        started = asyncio.Event()
        release = asyncio.Event()

        class SlowCatchUp:
            async def run(self, conversation_id):
                started.set()
                await release.wait()
                return "done"

        first = gm.schedule_catch_up("conv-dedup", catch_up=SlowCatchUp())
        await started.wait()
        second = gm.schedule_catch_up("conv-dedup", catch_up=SlowCatchUp())

        assert first is second  # 同一对话在途去重
        release.set()
        assert await first == "done"

    @pytest.mark.asyncio
    async def test_a_finished_catch_up_can_run_again(self):
        """跑完就解除登记：重连、隔天再打开都要能再补一次。"""
        import services.group_memory as gm

        class FastCatchUp:
            async def run(self, conversation_id):
                return "done"

        first = gm.schedule_catch_up("conv-dedup-2", catch_up=FastCatchUp())
        assert await first == "done"
        await asyncio.sleep(0)  # 让完成回调清掉登记

        second = gm.schedule_catch_up("conv-dedup-2", catch_up=FastCatchUp())
        assert second is not first
        assert await second == "done"

