"""群聊补账（ticket #55 / spec: group-chat Phase 2 · 群记忆与 compact）。

打开群对话时在后台把"这几天该记的事"补上：

1. **先提取**（一次 LLM 调用）→ **每个成员各存一份**记忆（`character_id` = 成员）；
2. **后 compact**（复用现有摘要 + 裁剪）。顺序不能换：先裁剪就会把没提取的消息删掉。

补账按**天**分窗（`batch_windows`），记忆的 `created_at` 用那一天 —— 隔几天再打开
时不会把三天前的事记成今天。锚点是 `Conversation.last_extract_at`，只推进到确实
处理过的地方（提取失败就停在那里，下次再补）。

同一对话 **in-flight 去重**：重连/多窗口同时打开不会重复补账（`schedule_catch_up`）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.conversation_manager import ConversationManager
from core.transcript import ensure_utc, render_transcript_text
from models.conversation import Conversation
from models.group import Group
from models.message import Message
from services.memory_extractor import (
    PROMPT_SCOPE_GROUP_USER_FACTS,
    ExtractedMemory,
    MemoryExtractor,
)
from services.memory_store import MemoryStore

#: 一次补账最多几批（= 几次 LLM 提取调用）。离开更久时把最老的那些天并成一批，
#: 内容一条不丢，代价只是那几天的记忆时间统一标成其中最早的一天。
MAX_BATCHES = 7

#: compact 保留的最近消息条数（与 1:1 / 每日补账一致）。
KEEP_RECENT_MESSAGES = 12


@dataclass(frozen=True)
class DayWindow:
    """一批补账：覆盖 [since_date 00:00, before) 的消息，记忆时间标成 `created_at`。"""

    since_date: str          # ISO 日期（含），给提取器做时间下界
    before: datetime         # 上界（不含）
    created_at: datetime     # 这批记忆的 created_at（= 这批最早那天的 00:00 UTC）


def batch_windows(
    anchor: datetime | None, until: datetime, *, max_batches: int = MAX_BATCHES
) -> list[DayWindow]:
    """把补账区间 [anchor, until) 切成"尽量按天、最多 max_batches 批"的窗口。

    - 锚点为空（从没补过账）→ 从 until 往前数 max_batches 天开始（不必把陈年旧账全翻一遍）。
    - 天数超过 max_batches → 最老的那些天合并成一批。
    - 每批的 before 是"下一天 00:00"；最后一批就是 until 本身。
    """
    until = ensure_utc(until)
    until_day = until.date()

    if anchor is None:
        start_day = until_day - timedelta(days=max_batches - 1)
    else:
        start_day = ensure_utc(anchor).date()

    day_count = (until_day - start_day).days + 1
    if day_count <= 0:
        return []  # 锚点在未来（时钟回拨之类）：没什么可补的
    days = [start_day + timedelta(days=i) for i in range(day_count)]

    if len(days) > max_batches:
        merge_count = len(days) - max_batches + 1
        merged, rest = days[:merge_count], days[merge_count:]
        groups = [(merged[0], merged[-1])] + [(d, d) for d in rest]
    else:
        groups = [(d, d) for d in days]

    windows: list[DayWindow] = []
    for index, (first, last) in enumerate(groups):
        is_last = index == len(groups) - 1
        windows.append(
            DayWindow(
                since_date=first.isoformat(),
                before=until
                if is_last
                else datetime.combine(last + timedelta(days=1), time.min, tzinfo=timezone.utc),
                created_at=datetime.combine(first, time.min, tzinfo=timezone.utc),
            )
        )
    return windows


def _spans_more_than_a_day(window: DayWindow) -> bool:
    """这批窗口是不是"合并批"（跨了不止一天）。

    单日窗里 `created_at` 恰好就是那天，退回它没有代价；合并窗里退回它就会把
    近几天的事记成最老那天 —— `_warn_missing_occurred_on` 靠这个区分。
    """
    first = date.fromisoformat(window.since_date)
    return (ensure_utc(window.before).date() - first).days > 1


def _warn_missing_occurred_on(
    window: DayWindow, items: "list[ExtractedMemory]", conversation_id: str
) -> int:
    """合并窗里漏了 `occurred_on` 的条目会被标成批次最早一天 —— 让它可见。

    返回被警告的条目数（0 = 没有这个问题），由 `CatchUpResult.undated` 带出去，
    这样"缺口可见"既能被运维从日志看到、也能被测试直接断言，不必去解析日志文本。

    正常路径不会走到这里：群聊提示词要求模型从行首时间戳里取 `occurred_on`
    （`memory_extractor._GROUP_RULES`），日期因此是精确的。一旦模型漏字段，
    `_item_created_at` 会退回窗口的 `created_at`：单日窗无所谓（那天就是那天），
    **合并窗**（离开太久、最老那几天并成一批）会把近几天的事记成最早那天。
    （docs/analysis/group-chat-phase2-review-fixes.md §2.1 / P1。）
    """
    missing = [item for item in items if not item.occurred_on]
    if not missing or not _spans_more_than_a_day(window):
        return 0
    print(
        f"[GroupMemory] conv={conversation_id[:8]} window={window.since_date}.."
        f"{ensure_utc(window.before).date().isoformat()} 跨多日，但 {len(missing)}/"
        f"{len(items)} 条没有 occurred_on → 记为 {window.created_at.date().isoformat()}"
        "（该批最早一天）",
        flush=True,
    )
    return len(missing)


@dataclass(frozen=True)
class CatchUpResult:
    """一次补账干了什么（给日志与测试看）。"""

    #: 走过的日窗口数（含空窗）—— 内容多少看 `extracted` / `stored`
    windows: int = 0
    extracted: int = 0
    stored: int = 0
    compacted: int = 0
    #: 合并窗里没有 `occurred_on`、被标成该批最早一天的条目数（>0 值得看一眼）
    undated: int = 0
    completed: bool = True


class GroupMemoryCatchUp:
    """打开一条群对话时的后台补账：先提取（每人一份）→ 后 compact。"""

    def __init__(
        self,
        *,
        extractor: MemoryExtractor | None = None,
        conversation_manager: ConversationManager | None = None,
        llm_service=None,
        session_factory=None,
        now=None,
        max_batches: int = MAX_BATCHES,
        keep_recent: int = KEEP_RECENT_MESSAGES,
    ):
        self._extractor = extractor or MemoryExtractor(MemoryStore())
        self._conversations = conversation_manager or ConversationManager()
        self._llm_service = llm_service
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._max_batches = max_batches
        self._keep_recent = keep_recent

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory()
        from database import async_session

        return async_session()

    def _llm(self):
        if self._llm_service is None:
            from services.llm_service import LLMService

            self._llm_service = LLMService()
        return self._llm_service

    async def run(self, conversation_id: str) -> CatchUpResult:
        until = self._now()

        async with self._sessions() as session:
            conv = await session.get(Conversation, conversation_id)
            if conv is None or conv.group_id is None:
                return CatchUpResult()  # 不是群对话，不归这里管
            group = (await session.execute(
                select(Group)
                .options(selectinload(Group.members))
                .where(Group.id == conv.group_id)
            )).scalar_one_or_none()
            member_ids = [m.character_id for m in group.members] if group else []
            anchor = conv.last_extract_at
            if anchor is None:
                # 从没补过账：窗口 = 这条对话的**全部历史**。只往前数几天的话，
                # 更老的消息会被后面的 compact 直接删掉 —— 那正是"先提取后裁剪"要避免的。
                anchor = (await session.execute(
                    select(func.min(Message.created_at)).where(
                        Message.conversation_id == conversation_id
                    )
                )).scalar()

        if anchor is None:
            # 一条消息都没有：没什么可补的
            await self._advance(conversation_id, until)
            return CatchUpResult()

        if not member_ids:
            # 群里没人：这些记忆没有归属；锚点照样推进，免得每次打开都重扫一遍
            await self._advance(conversation_id, until)
            return CatchUpResult()

        windows_walked = extracted = stored = undated = 0
        processed_until: datetime | None = None
        failed = False

        for window in batch_windows(anchor, until, max_batches=self._max_batches):
            windows_walked += 1
            messages = await self._extractor.load_window(
                conversation_id, since_date=window.since_date, before=window.before
            )
            if messages:
                # 名字要查库，所以这里仍需一次 session（渲染器本身是纯函数，
                # 不替调用方解析说话人）。
                async with self._sessions() as session:
                    speaker_names = await self._conversations.resolve_speaker_names(
                        session, messages
                    )
                # 群记录走共享渲染器：保留"谁说了什么"，提示词才能只挑用户的事实
                transcript = render_transcript_text(messages, speaker_names=speaker_names)
                try:
                    items = await self._extractor.extract(
                        transcript,
                        self._llm(),
                        prompt_scope=PROMPT_SCOPE_GROUP_USER_FACTS,
                    )
                except Exception as e:
                    print(
                        f"[GroupMemory] extraction failed conv={conversation_id[:8]} "
                        f"day={window.since_date}: {e}",
                        flush=True,
                    )
                    failed = True
                    break  # 锚点停在上一批结尾：这一天下次再补
                extracted += len(items)
                # 合并窗漏 occurred_on 的条目会被标成该批最早一天：返回值让调用方
                # （与测试）能看见，不必去解析日志文本。
                undated += _warn_missing_occurred_on(window, items, conversation_id)
                if items:
                    # 提取一次 → 每个成员各存一份（同一个角色内才去重）
                    for member_id in member_ids:
                        stored += len(await self._extractor.store(
                            items,
                            conversation_id=conversation_id,
                            character_id=member_id,
                            created_at=window.created_at,
                            notify=False,  # 通知由这里汇总成一次
                        ))
            processed_until = window.before

        if processed_until is not None:
            await self._advance(conversation_id, min(processed_until, until))

        if stored:
            from services.memory_service import push_memory_notification

            push_memory_notification(conversation_id, stored)

        compacted = 0
        if not failed:
            # 顺序固定：**先提取 → 后 compact**。提取失败就绝不裁剪 ——
            # 那些消息还没变成记忆，先删掉等于丢内容。
            async with self._sessions() as session:
                result = await self._conversations.summarize_and_trim(
                    session,
                    conversation_id,
                    keep_count=self._keep_recent,
                    llm_service=self._llm(),
                )
            compacted = int(result.get("deleted", 0) or 0)

        print(
            f"[GroupMemory] catch-up conv={conversation_id[:8]} windows={windows_walked} "
            f"extracted={extracted} stored={stored} compacted={compacted} "
            f"undated={undated} completed={not failed}",
            flush=True,
        )
        return CatchUpResult(
            windows=windows_walked,
            extracted=extracted,
            stored=stored,
            compacted=compacted,
            undated=undated,
            completed=not failed,
        )

    async def _advance(self, conversation_id: str, when: datetime) -> None:
        """把补账锚点推进到"确实处理过"的位置（会话状态归 ConversationManager 管）。"""
        async with self._sessions() as session:
            await self._conversations.mark_extracted(session, conversation_id, when)


#: 同一对话的补账任务（in-flight 去重：重连/多窗口不会重复补账）。
_in_flight: dict[str, asyncio.Task] = {}


def schedule_catch_up(
    conversation_id: str, *, catch_up: GroupMemoryCatchUp | None = None
) -> asyncio.Task:
    """启动该群对话的补账任务；已经在途就复用同一个任务。

    后台任务不绑在这条 WS 连接上：用户走开也照样补完。
    """
    existing = _in_flight.get(conversation_id)
    if existing is not None and not existing.done():
        return existing

    runner = catch_up or GroupMemoryCatchUp()

    async def _guarded() -> CatchUpResult:
        try:
            return await runner.run(conversation_id)
        except Exception as e:  # 后台任务没人 await，别把异常丢在风里
            print(
                f"[GroupMemory] catch-up failed conv={conversation_id[:8]}: {e}",
                flush=True,
            )
            return CatchUpResult(completed=False)

    task = asyncio.create_task(_guarded())
    _in_flight[conversation_id] = task

    def _forget(finished: asyncio.Task) -> None:
        if _in_flight.get(conversation_id) is finished:
            _in_flight.pop(conversation_id, None)

    task.add_done_callback(_forget)
    return task

