"""群聊驱动（ticket #53）：把群轮编排器接到 Agent 与一条 WS 连接上。

编排（谁在什么时候说、说几条、什么时候收束）在 `core/group_turn` —— 纯逻辑。
这里只做接线：

1. **一个角色的一轮**交给共享 Agent：该角色人设 + 该角色记忆 + 全量工具（审批照常，
   归属该角色）。回复先不落库（`persist_reply=False`），交给编排器判"跳过"。
2. **确实说了的**：落库（`speaker_id` = 该角色）并推送 `group_message`；
   跳过的（`<silent>`）一律不落库、不推送。
3. **用户消息**的落库 / `message_ack`，以及"一条会话同一时刻只有**一批**群轮"
   （批次**之内**成员并发、批次之间串行 —— ADR-0009）。

spec: docs/specs/group-chat.md（Phase 2 · 群轮编排）。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.conversation_manager import ConversationManager
from core.group_turn import (
    SILENT_MARKER,
    GroupTurnOrchestrator,
    GroupUtterance,
)
from services.group_memory import schedule_catch_up
from models.conversation import Conversation
from models.group import Group

SendJson = Callable[[dict], Awaitable[None]]

#: 群聊场景说明（追加到 system prompt 末尾）：谁在群里 + 什么时候该沉默。
GROUP_SCENARIO = """## 当前场景：群聊
你正在一个名为「{group_name}」的群聊里，群成员有：{member_names}（用户也在群里）。
- 这是群聊，不是一对一：可以回应最近发言的人（用户或别的角色），也可以只说说自己的想法。
- 有话就说，像在群里聊天那样 1-3 句，别长篇大论。
- **上面那段带时间和「[名字]:」的对话记录是只读的**：不要在上面续写，也不要自己
  写「[名字]:」或时间戳 —— 你说的话会由系统作为新的一条记录下来。你只输出**你要说
  的那句话本身**（纯文本，可以分段落）。
- **无话可说时只输出 {silent}**（不要加引号、不要解释、不要写别的字）。"""

#: 群轮的收尾合成轮（`Agent.run(user_nudge=...)`）。**不可省**：带 `tools` 的请求
#: 若以 assistant 结尾，DeepSeek 思考模式直接 400（"reasoning_content in the thinking
#: mode must be passed back"）—— 而一个 burst 里第二个请求的历史正好以"上一个角色
#: 刚说的话"结尾。所以每一轮都要用一条 user 轮收尾（不落库）。
#: 一句话交代"轮到你说话"，同一个用户轮里"接着说"的用另一句。
GROUP_TURN_OPEN = "（系统：现在轮到你发言，按群聊规则回应；无话可说只输出标记。）"

#: 同一个用户轮里"接着说"的问法。这里必须重申"只写话本身"：续说请求的上下文就是一串
#: `时间 [说话人]: 内容`，模型很容易顺手把**下一行**写出来（2026-09-27 / 09-28 两次实测，
#: 第二次是自己说了一段之后另起一行续写记录）。
GROUP_CONTINUATION = (
    f"（系统：你刚说过话。还想补充就直接接着说你**自己的话**（纯文本，别另起一行写"
    f"「[名字]:」或时间戳）；**不想说了只输出 {SILENT_MARKER}**。）"
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
        on_batch_finished: Callable[[], None] | None = None,
        conversation_manager: ConversationManager | None = None,
    ):
        self._conversation_id = conversation_id
        self._agent = agent
        self._conversations = conversation_manager or ConversationManager()
        self._send_json = send_json
        self._approval_callback = approval_callback
        self._on_batch_finished = on_batch_finished
        self._orchestrator = GroupTurnOrchestrator(
            self._speak,
            on_utterance=self._store_and_send,
            on_turn_end=self._on_turn_end,
        )
        self._turn_task: asyncio.Task | None = None
        self._persisting: list[asyncio.Task] = []
        self._settling: asyncio.Future | None = None
        self._scenario = ""
        self._member_names: dict[str, str] = {}

    def start(self) -> None:
        """打开群对话：排上后台补账（先提取 → 后 compact；同一对话在途去重）。

        任务不绑在这条连接上 —— 用户走开也照样补完（ticket #55）。
        """
        schedule_catch_up(self._conversation_id)

    @property
    def in_flight(self) -> bool:
        return self._orchestrator.in_flight

    def handle_user_message(self, text: str, *, client_message_id: str | None = None) -> None:
        """用户说了一句。

        **同步方法**（不 await）：两条几乎同时到达的消息必须落在同一批群轮上，
        否则会开出两批并行的群轮 —— 批次之间仍是串行（批次**之内**成员并发，
        ADR-0009）。落库与 ack 走后台任务，但每个角色读历史之前都会先把它们结清
        （`_settle_persists`），所以轮到谁说话时，用户那几句一定已经在历史里。
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
            member_ids, self._scenario, self._member_names = await self._roster()
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
            if self._on_batch_finished is not None:
                self._on_batch_finished()
            if not cancelled:
                await self._safe_send({"type": "done", "group": True})

    async def _on_turn_end(self) -> None:
        """一个用户轮跑完：按轮发信号（审查 P3）。

        `pending` = 队列里还有插话、马上会开下一轮；前端据此决定"正在回应"
        要不要收掉，而不是等整批跑完。批次结束仍由上面的 `done` 兜底 ——
        事件形状见 CONTEXT.md §5.1。

        `pending=False` 只是"此刻队列是空的"：本回调 await 期间用户仍可能插话，
        于是紧接着还会开一轮。前端不该把 false 当成"这一批彻底结束了"。
        """
        await self._safe_send({
            "type": "turn_end",
            "group": True,
            "pending": self._orchestrator.has_pending(),
        })

    async def _speak(self, character_id, *, is_continuation, user_messages) -> str | None:
        """跑一个角色的一轮：LLM 只生成，落库与推送由编排器判定后交给 sink。

        成员并发开口（ticket #09），所以每次调用都开自己的 session；用户消息的结清
        走 `_settle_persists`（并发调用会等同一批）。

        进来/出去各报一次 `group_thinking`（ticket #10）：前端据此为**每个成员**各显示
        一个"正在输入"，谁说完谁收掉 —— 而不是所有成员共用一个"正在回应"。
        """
        from database import async_session

        await self._thinking(character_id, True)
        try:
            await self._settle_persists()  # 角色读历史之前，用户那几句必须已经在库里
            suffix = self._scenario
            # 收尾用哪句合成轮：接着说是另一句（原本挂在 system prompt 上，但那只改措辞、
            # 不改"最后一条是 assistant"这件事）。
            nudge = GROUP_CONTINUATION if is_continuation else GROUP_TURN_OPEN

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
                    user_nudge=nudge,  # 必须让请求以 user 结尾，见 GROUP_TURN_OPEN
                ):
                    if event["type"] == "done":
                        reply = event.get("content", "")
                    elif event["type"] == "error":
                        raise GroupTurnError(event.get("message") or "角色生成失败")
                    elif event["type"] in _FORWARDED:
                        await self._safe_send(event)
                    # token 先攒着：这一条可能是 <silent>，绝不能漏给前端
            return reply
        finally:
            await self._thinking(character_id, False)

    async def _thinking(self, character_id: str, thinking: bool) -> None:
        """报一次"这个成员开始/结束思考"（前端按成员显示/收起"正在输入"）。"""
        await self._safe_send({
            "type": "group_thinking",
            "character_id": character_id,
            "speaker": self._member_names.get(character_id),
            "thinking": thinking,
        })

    async def _store_and_send(self, utterance: GroupUtterance) -> None:
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
            # 名字在后端解析（与历史接口同一规则：按消息自己的 speaker_id），
            # 前端不必再维护第二套说话人规则。
            "speaker": self._member_names.get(utterance.character_id),
            # 推落库后的文本（`add_message` 会剥掉模型带出来的对话记录前缀）：
            # 推送与库里必须一致，否则刷新一下内容就变了。
            "content": msg.content,
            # 会话内单调序号（ticket #56）：并发下到达顺序 ≠ 发言顺序，
            # 前端据此把消息插到正确的位置（ticket #11）。
            "seq": msg.seq,
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
                # 用户消息的序号也带上：前端把"本地那条"换成真身时一起补上序号，
                # 这样群里的每一条（含用户自己的）都能按序号排序。
                "seq": msg.seq,
            })

    async def _settle_persists(self) -> None:
        """结清还没落库的用户消息（每个成员开口前都要做一次）。

        成员现在是**并发**开口的（ticket #09），所以这里必须让所有并发调用等**同一批**：
        第一个调用者取走列表去 await，后到的若看到空列表就往下走，会读到"用户消息
        还没落库"的历史（回复的序号甚至可能排到用户消息前面）。批次的创建者负责清理；
        这一批期间新提交的消息留在列表里，由下一次调用结清。
        """
        batch = self._settling
        if batch is None:
            if not self._persisting:
                return
            pending, self._persisting = self._persisting, []
            batch = self._settling = asyncio.gather(*pending)
        try:
            # shield：某个成员这一轮被取消（连接断开）不该把落库一起取消 ——
            # 用户那几句已经收下了，必须落进库里。
            await asyncio.shield(batch)
        finally:
            if self._settling is batch:
                self._settling = None

    async def _roster(self) -> tuple[list[str], str, dict[str, str]]:
        """一次查询备好：成员 id（按发言顺序）+ 场景说明 + id→名字。"""
        from database import async_session
        from models.character import CharacterProfile

        async with async_session() as session:
            conv = await session.get(Conversation, self._conversation_id)
            if conv is None or conv.group_id is None:
                return [], "", {}
            group = (await session.execute(
                select(Group)
                .options(selectinload(Group.members))
                .where(Group.id == conv.group_id)
            )).scalar_one_or_none()
            if group is None:
                return [], "", {}
            member_ids = [m.character_id for m in group.members]  # 顺序 = model 声明的 position
            by_id: dict[str, str] = {}
            if member_ids:
                rows = (await session.execute(
                    select(CharacterProfile.id, CharacterProfile.name)
                    .where(CharacterProfile.id.in_(member_ids))
                )).all()
                by_id = {cid: name for cid, name in rows}
            scenario = GROUP_SCENARIO.format(
                group_name=group.name,
                member_names="、".join(by_id[i] for i in member_ids if i in by_id),
                silent=SILENT_MARKER,
            )
            return member_ids, scenario, by_id

    async def _safe_send(self, payload: dict) -> None:
        """推送失败（连接已断）不该毁掉这一轮：落库优先于推送。"""
        try:
            await self._send_json(payload)
        except Exception as e:
            print(f"[GroupChat] send skipped ({payload.get('type')}): {e}", flush=True)
