"""
MemoryExtractor — LLM-driven memory extraction from conversations.

三件事分开，好让群聊复用同一套（ticket #55）：

1. `load_window` —— 取某时间窗口内的消息（DB）
2. `extract`     —— 一段 transcript → 候选条目（**一次** LLM 调用，不落库）
3. `store`       —— 条目 → 记忆（按角色去重 / 向量 / 落库）

`extract_and_store` 仍是 1:1 与每日补账的入口（= 三者串起来，行为不变）。
群聊走 `extract` 一次 + 对每个成员各 `store` 一份（"提取一次 → 存储 N 份"）。

去重按**角色**隔离：同一句话不会因为 A 已经记过，就不再进 B 的记忆
（记忆本来就是按角色一份 —— 跨角色去重等于记忆污染）。
"""

import json
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, time, timezone

from core.transcript import render_role_labels

from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.memory import Memory, SOURCE_AI_SUMMARIZED
from models.message import Message
from services.memory_store import MemoryStore

#: 提取口径。1:1 / 每日补账 = 第一人称日记；群聊 = 只记"关于用户的持久事实"。
PROMPT_SCOPE_DIARY = "diary"
PROMPT_SCOPE_GROUP_USER_FACTS = "group_user_facts"

#: 1:1 的日记口径（**原样保留**：本 ticket 只拆分提取/存储，不改 1:1 的提示词）。
_DIARY_RULES = (
    "你是一个记忆记录员。看了下面的对话后，用写日记的方式记录下你（AI 角色）"
    "在这次对话中了解到的事情。输出格式为严格的 JSON 数组。\n\n"
    "每条记录包含：\n"
    '- "content": 一小段日记（1-3句话），用第一人称叙述。例如：'
    '"今天和用户聊了他最近在学的 Rust，他似乎遇到了一些所有权概念的困惑，我帮他梳理了一下。" '
    '或 "主人今天心情不太好，说工作压力很大，我陪他聊了一会儿。"\n'
    '- "memory_type": "user_fact" / "user_preference" / "important_event"\n'
    '- "importance": 1-10（10=非常个人化、情感上重要、以后很可能需要回忆起）\n\n'
    "规则：\n"
    "- 用 AI 角色（你自己）的视角写，称自己为「我」。\n"
    "- 对用户的称呼根据对话氛围自然选择——用户、主人、他/她、对方的名字等都行。\n"
    "- 重点记录你了解到的关于用户的事情、发生了什么、用户的情绪状态。\n"
    "- 不要记录琐碎的问候和闲聊。\n"
    "- 如果没有值得记录的内容，输出空数组 []。\n"
)

#: 群聊口径：**只**关于用户的持久事实；角色的观点是当场的，不是记忆。
_GROUP_RULES = (
    "你是一个记忆记录员。下面是一段**群聊**记录（多人发言，每行带时间和说话人）。"
    "只提取**关于用户的持久事实**，输出格式为严格的 JSON 数组。\n\n"
    "每条记录包含：\n"
    '- "content": 一句话（1-3 句），第三人称写用户（不要写「我」）\n'
    '- "memory_type": "user_fact" / "user_preference" / "important_event"\n'
    '- "importance": 1-10（10=非常个人化、情感上重要、以后很可能需要回忆起）\n'
    '- "occurred_on": 这条事实发生在哪一天，**从那几行的时间戳里取**，格式 YYYY-MM-DD\n\n'
    "硬性限制：\n"
    "- **不要**提取任何角色的观点、态度、建议或人设（那是当场的对话，不是记忆）。\n"
    "- **不要**记录群里的对话过程（谁附和了谁、谁在吐槽、聊到哪儿了）。\n"
    "- 只写用户说过/做过/流露过的东西；没把握就不要写。\n"
    "- 如果没有值得记录的关于用户的事实，输出空数组 []。\n"
)

#: 系统提示词按口径分开：日记要第一人称，群聊恰恰**不许**第一人称。
_SYSTEM_BY_SCOPE = {
    PROMPT_SCOPE_DIARY: "你是一个记忆记录员。用第一人称日记体记录对话中的重要信息。只输出有效的 JSON 数组。",
    PROMPT_SCOPE_GROUP_USER_FACTS: "你是一个记忆记录员。只记录关于用户的持久事实，只输出有效的 JSON 数组。",
}





_RULES_BY_SCOPE = {
    PROMPT_SCOPE_DIARY: _DIARY_RULES,
    PROMPT_SCOPE_GROUP_USER_FACTS: _GROUP_RULES,
}


@dataclass(frozen=True)
class ExtractedMemory:
    """一条候选记忆（提取出来、还没落库）。"""

    content: str
    memory_type: str = "user_fact"
    importance: int = 5
    #: 这条事实发生在哪一天（群聊口径要求模型从记录的时间戳里取）；拿不到就退回窗口那天
    occurred_on: str | None = None


def _day_start(value: str) -> datetime:
    """ISO 日期 → 当天 00:00（UTC，与库里 naive-UTC 惯例一致）。"""
    return datetime.combine(_date.fromisoformat(value), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )


class MemoryExtractor:
    """LLM-driven memory extraction, dedup, embed, and store."""

    def __init__(self, store: MemoryStore):
        self._store = store

    # ---- 1. 取窗口 ----------------------------------------------------

    async def load_window(
        self,
        conversation_id: str,
        *,
        since_date: str | None = None,
        before: datetime | None = None,
    ) -> list[Message]:
        """取窗口内的消息（时间正序）。

        `since_date`（ISO 日期）给了就按时间下界取窗口内全部消息；没给则取最近
        `EXTRACTION_MESSAGE_COUNT` 条。`before`（带时区 datetime）是上界。
        """
        from services.memory_service import EXTRACTION_MESSAGE_COUNT

        async with async_session() as session:
            if since_date:
                stmt = (
                    _select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.created_at >= _date.fromisoformat(since_date),
                    )
                    .order_by(Message.created_at.desc())
                )
            else:
                stmt = (
                    _select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(EXTRACTION_MESSAGE_COUNT)
                )
            if before is not None:
                stmt = stmt.where(Message.created_at < before)
            result = await session.execute(stmt)
            return list(reversed(result.scalars().all()))

    @staticmethod
    def render_diary_transcript(messages) -> str:
        """1:1 用的行格式（"谁说的"由角色标签给出，不需要名字）。

        实现在 `core.transcript.render_role_labels` —— 对话摘要也用同一份。
        """
        return render_role_labels(messages)

    @staticmethod
    def _item_created_at(
        item: ExtractedMemory, fallback: datetime | None
    ) -> datetime | None:
        """记忆时间用**消息真实发生的日期**：跨日补账时别把三天前的事记成今天。"""
        if not item.occurred_on:
            return fallback
        try:
            day = _date.fromisoformat(str(item.occurred_on).strip()[:10])
        except ValueError:
            return fallback
        return datetime.combine(day, time.min, tzinfo=timezone.utc)

    # ---- 2. 提取 ------------------------------------------------------

    async def extract(
        self,
        transcript: str,
        llm_service,
        *,
        prompt_scope: str = PROMPT_SCOPE_DIARY,
        user_name_hint: str = "",
    ) -> list[ExtractedMemory]:
        """一段 transcript → 候选条目。**一次** LLM 调用，不落库。

        失败时**抛异常**（调用方决定重试还是放弃）：补账要靠它决定"这一天的锚点
        能不能往前走"，把失败伪装成"没有可记的"会让内容静悄悄丢掉。
        `extract_and_store` 仍按老规矩吞掉失败返回 []。
        """
        prompt = self._build_prompt(transcript, prompt_scope, user_name_hint)
        raw = await self._call_llm_for_extraction(llm_service, prompt, prompt_scope)
        return self._parse(raw)

    def _build_prompt(self, transcript: str, prompt_scope: str, user_name_hint: str) -> str:
        # 口径写错就直接炸（别悄悄退回日记口径，那会把群聊提示词换成第一人称）
        rules = _RULES_BY_SCOPE[prompt_scope]
        return f"{rules}\n{user_name_hint}对话内容：\n{transcript}"

    @staticmethod
    def _parse(raw: str) -> list[ExtractedMemory]:
        """解析 LLM 的 JSON 数组（容忍外面包着解释文字）。"""
        if not raw:
            return []
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start < 0 or end <= start:
                return []
            try:
                items = json.loads(raw[start:end])
            except json.JSONDecodeError:
                return []
        if not isinstance(items, list):
            return []

        out: list[ExtractedMemory] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            try:
                importance = int(item.get("importance", 5))
            except (TypeError, ValueError):
                importance = 5
            occurred_on = str(
                item.get("occurred_on") or item.get("date") or ""
            ).strip()
            out.append(
                ExtractedMemory(
                    content=content,
                    memory_type=str(item.get("memory_type") or "user_fact"),
                    importance=importance,
                    occurred_on=occurred_on or None,
                )
            )
        return out

    # ---- 3. 落库 ------------------------------------------------------

    async def store(
        self,
        items: list[ExtractedMemory],
        *,
        conversation_id: str,
        character_id: str | None = None,
        created_at: datetime | None = None,
        notify: bool = True,
    ) -> list[Memory]:
        """条目 → 记忆：按角色去重、算向量、落库。

        `created_at` 用**消息真实发生的日期**（补账跨多日时别把三天前记成今天）。
        `notify=False` 交给调用方自己汇总通知（群聊要一次提取、N 份存储）。
        """
        async with async_session() as session:
            stored: list[Memory] = []
            for item in items:
                existing = await self._store.find_similar(
                    session, item.content, character_id=character_id
                )
                if existing:
                    old_content = existing.content
                    existing.importance = max(existing.importance, item.importance)
                    existing.content = item.content  # use newer wording
                    await session.commit()
                    await MemoryStore.sync_fts5_update(
                        existing.id, old_content, item.content, existing.memory_type
                    )
                    stored.append(existing)
                    continue

                emb = None
                try:
                    from services.embedding_service import embed_single

                    emb = await embed_single(item.content)
                except Exception:
                    pass

                stored.append(
                    await self._store.add(
                        session,
                        content=item.content,
                        memory_type=item.memory_type,
                        importance=item.importance,
                        source_conversation_id=conversation_id,
                        embedding=emb,
                        character_id=character_id,
                        source=SOURCE_AI_SUMMARIZED,
                        created_at=self._item_created_at(item, created_at),
                        notify=notify,
                    )
                )

        if stored and notify:
            from services.memory_service import push_memory_notification

            push_memory_notification(conversation_id, len(stored))
        return stored

    # ---- 1:1 / 每日补账入口（行为不变） --------------------------------

    async def extract_and_store(
        self,
        conversation_id: str,
        llm_service,
        character_id: str | None = None,
        since_date: str | None = None,
        before: datetime | None = None,
    ) -> list[Memory]:
        """取窗口 → 提取 → 落库（一次一份）。1:1 与每日补账走这里。"""
        from services.memory_service import EXTRACTION_MIN_MESSAGES

        messages = await self.load_window(
            conversation_id, since_date=since_date, before=before
        )
        if len(messages) < EXTRACTION_MIN_MESSAGES:
            return []

        hint = await self._user_name_hint(character_id)
        try:
            items = await self.extract(
                self.render_diary_transcript(messages),
                llm_service,
                user_name_hint=hint,
            )
        except Exception as e:
            print(f"[MemoryExtractor] extraction LLM call failed: {e}", flush=True)
            return []

        if not items:
            return []
        print(f"[MemoryExtractor] extracted {len(items)} new memories", flush=True)
        return await self.store(
            items,
            conversation_id=conversation_id,
            character_id=character_id,
            created_at=_day_start(since_date) if since_date else None,
        )

    async def _user_name_hint(self, character_id: str | None) -> str:
        """「用户的名字是……」提示：优先该角色的画像，回退全局画像。"""
        if not character_id:
            return ""
        try:
            from sqlalchemy import desc as _desc

            from models.user_profile import UserProfile
            from services.memory_service import EXTRACTION_MIN_MESSAGES  # noqa: F401  (keep import shape)

            async with async_session() as session:
                result = await session.execute(
                    _select(UserProfile)
                    .where(UserProfile.character_id == character_id)
                    .order_by(_desc(UserProfile.updated_at))
                    .limit(1)
                )
                profile = result.scalar_one_or_none()
                if not profile:
                    result = await session.execute(
                        _select(UserProfile)
                        .where(UserProfile.character_id.is_(None))
                        .order_by(_desc(UserProfile.updated_at))
                        .limit(1)
                    )
                    profile = result.scalar_one_or_none()
                if profile and profile.user_name:
                    return (
                        f"用户的名字是「{profile.user_name}」。"
                        f"在日记中请用「{profile.user_name}」称呼他，不要用「用户」这个泛称。\n"
                    )
        except Exception:
            print(
                "[MemoryExtractor] failed to load UserProfile for name hint, "
                "continuing without it",
                flush=True,
            )
        return ""

    async def _call_llm_for_extraction(
        self, llm_service, prompt: str, prompt_scope: str = PROMPT_SCOPE_DIARY
    ) -> str:
        """Call the LLM non-streaming for memory extraction."""
        messages = [
            {"role": "system", "content": _SYSTEM_BY_SCOPE[prompt_scope]},
            {"role": "user", "content": prompt},
        ]
        return await llm_service.chat_sync(
            messages, max_tokens=1024, temperature=0.3
        )
