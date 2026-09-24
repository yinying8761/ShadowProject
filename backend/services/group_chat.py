"""群聊驱动（ticket #53）：把群轮编排器接到 Agent 与一条 WS 连接上。

编排（谁在什么时候说、说几条、什么时候收束）在 `core/group_turn` —— 纯逻辑。
这里只做接线：

1. **一个角色的一轮**交给共享 Agent：该角色人设 + 该角色记忆 + 全量工具（审批照常，
   归属该角色）。回复先不落库（`persist_reply=False`），交给编排器判"跳过"。
2. **确实说了的**：落库（`speaker_id` = 该角色）并推送 `group_message`；
   跳过的（`<silent>`）一律不落库、不推送。
3. **用户消息**的落库 / `message_ack`，以及"一个会话同一时刻只有一串群轮"。

spec: docs/specs/group-chat.md（Phase 2 · 群轮编排）。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.conversation_manager import ConversationManager
from core.group_turn import SILENT_MARKER, GroupTurnOrchestrator, GroupUtterance
from models.conversation import Conversation
from models.group import Group

SendJson = Callable[[dict], Awaitable[None]]

#: 群聊场景说明（追加到 system prompt 末尾）：谁在群里 + 什么时候该沉默。
GROUP_SCENARIO = """## 当前场景：群聊
你正在一个名为「{group_name}」的群聊里，群成员有：{member_names}（用户也在群里）。
- 这是群聊，不是一对一：可以回应最近发言的人（用户或别的角色），也可以只说说自己的想法。
- 有话就说，像在群里聊天那样 1-3 句，别长篇大论。
- **无话可说时只输出 {silent}**（不要加引号、不要解释、不要写别的字）。"""

#: 同一个用户轮里"接着说"的问法。
GROUP_CONTINUATION = (
    f"（系统：你刚说过话。还想补充就继续说；**不想说了只输出 {SILENT_MARKER}**。）"
)

#: 转发给前端的事件：工具活动与记忆提示是真实发生的（token / done 由驱动决定）。
_FORWARDED = ("tool_use", "tool_result", "memory_updated")


class GroupTurnError(RuntimeError):
    """某个角色这一轮生成失败：整个群轮收束（与 1:1 的失败语义一致）。"""


class GroupChatSession:
    """一条群对话 WS 连接的驱动。"""

    def __init__(
        self,
        conversation_id: str,
        *,
        agent,
        send_json: SendJson,
        approval_callback=None,
        on_turn_finished: Callable[[], None] | None = None,
        conversation_manager: ConversationManager | None = None,
    ):
        self._conversation_id = conversation_id
        self._agent = agent
        self._conversations = conversation_manager or ConversationManager()
        self._send_json = send_json
        self._approval_callback = approval_callback
        self._on_turn_finished = on_turn_finished
        self._orchestrator = GroupTurnOrchestrator(
            self._speak, on_utterance=self._store_and_push
        )
        self._turn_task: asyncio.Task | None = None
        self._persisting: list[asyncio.Task] = []
        self._scenario = ""

    @property
    def in_flight(self) -> bool:
        return self._orchestrator.in_flight

    def handle_user_message(self, text: str, *, client_message_id: str | None = None) -> None:
        """用户说了一句。

        **同步方法**（不 await）：两条几乎同时到达的消息必须落在同一串群轮上，
        否则会开出两轮并行的群轮 —— "串行、永不并行"是硬契约。落库与 ack 走后台
        任务，但每个角色读历史之前都会先把它们结清（`_settle_persists`），
        所以轮到谁说话时，用户那几句一定已经在历史里。
        """
        self._persisting.append(
            asyncio.create_task(self._persist_user_message(text, client_message_id))
        )
        self._orchestrator.submit(text)
        if self._turn_task is None or self._turn_task.done():
            self._turn_task = asyncio.create_task(self._run_turns())

    async def stop(self) -> None:
        """连接断开：在途群轮不再有意义，取消掉。"""
        task, self._turn_task = self._turn_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ── 接线 ────────────────────────────────────────────────────────

    async def _run_turns(self) -> None:
        cancelled = False
        try:
            member_ids, self._scenario = await self._roster()
            if member_ids:
                await self._orchestrator.run(member_ids)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as e:
            print(
                f"[GroupChat] turn failed conv={self._conversation_id[:8]}: {e}",
                flush=True,
            )
            await self._safe_send({"type": "error", "message": str(e)})
        finally:
            self._turn_task = None
            if self._on_turn_finished is not None:
                self._on_turn_finished()
            if not cancelled:
                await self._safe_send({"type": "done", "group": True})

    async def _speak(self, character_id, *, is_continuation, user_messages) -> str | None:
        """跑一个角色的一轮：LLM 只生成，落库与推送由编排器判定后交给 sink。"""
        from database import async_session

        await self._settle_persists()  # 角色读历史之前，用户那几句必须已经在库里
        suffix = self._scenario
        if is_continuation:
            suffix = f"{suffix}\n\n{GROUP_CONTINUATION}"

        reply = ""
        async with async_session() as session:
            async for event in self._agent.run(
                session=session,
                user_message=None,  # 用户消息由驱动落库，这里只读历史
                conversation_id=self._conversation_id,
                character_id=character_id,
                approval_callback=self._approval_callback,
                persist_reply=False,  # 先判"跳过"，再决定落不落库
                system_suffix=suffix,
                memory_query="\n".join(user_messages) or None,
            ):
                if event["type"] == "done":
                    reply = event.get("content", "")
                elif event["type"] == "error":
                    raise GroupTurnError(event.get("message") or "角色生成失败")
                elif event["type"] in _FORWARDED:
                    await self._safe_send(event)
                # token 先攒着：这一条可能是 <silent>，绝不能漏给前端
        return reply

    async def _store_and_push(self, utterance: GroupUtterance) -> None:
        """确实说了的：落库（`speaker_id` = 该角色）再推送。"""
        from database import async_session

        async with async_session() as session:
            msg = await self._conversations.add_message(
                session,
                self._conversation_id,
                "assistant",
                utterance.content,
                speaker_id=utterance.character_id,
            )
        await self._safe_send({
            "type": "group_message",
            "message_id": msg.id,
            "character_id": utterance.character_id,
            "content": utterance.content,
        })

    async def _persist_user_message(self, text: str, client_message_id: str | None) -> None:
        from database import async_session

        async with async_session() as session:
            msg = await self._conversations.add_message(
                session, self._conversation_id, "user", text
            )
        if client_message_id:
            await self._safe_send({
                "type": "message_ack",
                "client_message_id": client_message_id,
                "message_id": msg.id,
            })

    async def _settle_persists(self) -> None:
        """结清还没落库的用户消息（每个角色开口前都要做一次）。"""
        if not self._persisting:
            return
        pending, self._persisting = self._persisting, []
        await asyncio.gather(*pending)

    async def _roster(self) -> tuple[list[str], str]:
        """一次查询备好：成员 id（按发言顺序）+ 给 LLM 的场景说明。"""
        from database import async_session
        from models.character import CharacterProfile

        async with async_session() as session:
            conv = await session.get(Conversation, self._conversation_id)
            if conv is None or conv.group_id is None:
                return [], ""
            group = (await session.execute(
                select(Group)
                .options(selectinload(Group.members))
                .where(Group.id == conv.group_id)
            )).scalar_one_or_none()
            if group is None:
                return [], ""
            member_ids = [m.character_id for m in group.members]  # 顺序 = model 声明的 position
            by_id: dict[str, str] = {}
            if member_ids:
                rows = (await session.execute(
                    select(CharacterProfile.id, CharacterProfile.name)
                    .where(CharacterProfile.id.in_(member_ids))
                )).all()
                by_id = {cid: name for cid, name in rows}
            return member_ids, GROUP_SCENARIO.format(
                group_name=group.name,
                member_names="、".join(by_id[i] for i in member_ids if i in by_id),
                silent=SILENT_MARKER,
            )

    async def _safe_send(self, payload: dict) -> None:
        """推送失败（连接已断）不该毁掉这一轮：落库优先于推送。"""
        try:
            await self._send_json(payload)
        except Exception as e:
            print(f"[GroupChat] send skipped ({payload.get('type')}): {e}", flush=True)
