"""群轮编排（ticket #53, spec: group-chat Phase 2 · 群轮编排）。

**纯逻辑**：不碰 DB / 网络 / 时钟。"谁在什么时候说、说几条、什么时候收束"
全在这里；"跑一个角色的一轮"由调用方注入（`speak`），由它负责 LLM、落库与推送。
这样「顺序 / 跳过 / 链预算 / 插话新轮」都能用假对象直接测出来（spec S3）。

契约：

- **串行**：一次只跑一个角色的一轮（一个 LLM 流），永不并行；顺序 = 传入的成员顺序。
- **三种输出**：跳过（整条回复只有 `<silent>`，丢弃）／一条消息／继续说（同一角色再
  生成一条，仍计入链预算）。
- **链预算**：每个用户轮的角色消息总量上限（默认 6）；到顶即整群静默。
- **插话**：用户轮在途时又发消息 → 不打断在途流；当前这条流收尾后旧轮**立即**收束
  （既不续说，也不让剩余成员就旧消息发言），排队的消息**合并为一个新轮**（预算重置）。

用户消息只有一条入口：`submit()` 入队，`run()` 跑完队列里所有待处理的消息。
这样"两条消息几乎同时到达"不会开出两轮并行的群轮 —— 入队是同步的，没有竞态窗口。

**任务句柄由驱动自管**（`services/group_chat.py` 的 `_turn_task`）：`submit()` 的返回值
只表示"入队时是否已经有在途轮"，驱动不靠它决策。驱动之所以不会开出第二轮，是因为
`run()` 里「`take_pending()` 取空 → `_in_flight` 置回 False」之间**没有任何 await**；
不可重入守卫（`RuntimeError`）是最后一道防线，不是串行的实现手段。

回调（都由调用方注入，编排器本身仍是纯逻辑）：

- `on_utterance(utterance)` —— 每条被采纳的消息一确定就交出去（驱动落库 + 推送）。
- `on_turn_end()` —— **每个用户轮**跑完就交出去（驱动据此按轮发信号）。

  回调发生在 `take_pending()` **之前**，所以回调里 `has_pending()` 能回答"还有下一轮吗"，
  而且回调 await 期间进来的新消息会被紧随其后的 `take_pending()` 取走、不会漏。
  代价是 `has_pending() == False` 只是"此刻没有"，回调期间用户仍可能插话 —— 调用方
  据此发信号时要容忍"说完没有、马上又来一轮"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

#: 每个「用户轮」的角色消息总量上限（spec：链预算）。
GROUP_CHAIN_BUDGET = 6

#: 提示词约定：无话可说时只输出这个标记。
SILENT_MARKER = "<silent>"

#: (character_id, *, is_continuation, user_messages) → 回复原文（None = 什么都没生成出来）。
SpeakFn = Callable[..., Awaitable["str | None"]]

#: 每条被采纳的消息一决定就交出去（驱动据此落库 + 推送，不必等整轮跑完）。
UtteranceSink = Callable[["GroupUtterance"], Awaitable[None]]

#: 每个用户轮跑完时回调（驱动据此按轮发事件；轮末调用时 `has_pending()` 仍准）。
#: 不带载荷：驱动只需要"这一轮结束了 + 队列里还有吗"，轮内容由 `run()` 的返回值给。
TurnEndSink = Callable[[], Awaitable[None]]

#: 标记周围可能出现的修饰：模型偶尔会写成 `"<silent>"`、`（<silent>）` 之类。
_DECORATION = "`*_~\"'“”‘’()（）[]【】{}<>《》:：,，.。!！?？-— \t\r\n"


def is_silent(reply: str | None) -> bool:
    """这条回复是不是"无话可说"（跳过）。

    判定标准：整条回复除了标记之外只剩空白 / 引号 / 括号之类的修饰，才算跳过。
    夹带内容的（"<silent> 但我想说……"）必须当成人话 —— 否则角色真正想说的话会被
    一个标记一起吞掉。空的回复（`None` / `""`）也算跳过：什么都没生成出来。
    """
    if reply is None:
        return True
    text = reply.strip().lower()
    if not text:
        return True
    if SILENT_MARKER not in text:
        return False
    return not text.replace(SILENT_MARKER, "").strip(_DECORATION)


@dataclass(frozen=True)
class GroupUtterance:
    """群里一条已确定要落库的角色消息。"""

    character_id: str
    content: str


@dataclass(frozen=True)
class GroupTurnResult:
    """一个用户轮的编排结果。"""

    user_messages: tuple[str, ...]
    utterances: tuple[GroupUtterance, ...]
    stopped_for_interjection: bool = False
    budget_exhausted: bool = False


class GroupTurnOrchestrator:
    """驱动一条群对话的用户轮（ticket #53）。

    调用方（WS 侧的驱动）负责"何时开跑"：`submit()` 的返回值告诉它队列里已经有了
    消息而当前没有在途轮，于是自己起一个任务 await `run()`；`run()` 会把在它执行
    期间 submit 进来的插话自动接力成新轮，所以一个任务就能跑完一串用户轮。
    """

    def __init__(
        self,
        speak: SpeakFn,
        *,
        on_utterance: "UtteranceSink | None" = None,
        on_turn_end: "TurnEndSink | None" = None,
        chain_budget: int = GROUP_CHAIN_BUDGET,
    ):
        self._speak = speak
        self._on_utterance = on_utterance
        self._on_turn_end = on_turn_end
        self._chain_budget = chain_budget
        self._queued: list[str] = []
        self._in_flight = False

    @property
    def in_flight(self) -> bool:
        """是否有群轮在途（在途时新的用户消息只是排队，不打断）。"""
        return self._in_flight

    def has_pending(self) -> bool:
        """队列里是否还有没轮到的用户消息。"""
        return bool(self._queued)

    def submit(self, text: str) -> bool:
        """用户说了一句：入队。

        **同步**方法：没有 await，所以两条几乎同时到达的消息不可能各自开出一轮。
        返回值只作参考：True = 入队时已经有在途群轮（它会自己接力成新轮）；
        False = 当前空闲。**任务句柄由驱动自己管**（`group_chat._turn_task`），
        驱动不需要按这个返回值决策 —— 见模块 docstring 里的说明。
        """
        self._queued.append(text)
        return self._in_flight

    def take_pending(self) -> list[str]:
        """取出待处理的用户消息（多条合并成一个新轮）。"""
        queued, self._queued = self._queued, []
        return queued

    async def run(self, member_ids: Sequence[str]) -> list[GroupTurnResult]:
        """把队列里的用户消息一轮一轮跑完（插话合并、每轮预算重置）。

        每跑完一个用户轮就回调 `on_turn_end()`（此时队列**还没取**，所以
        `has_pending()` 仍能回答"还有下一轮吗"）；回调里 await 期间进来的新消息
        会在紧随其后的 `take_pending()` 被取走，不会漏。

        不可重入：同一实例同时在跑两轮就违反"永不并行"，因此直接报错而不是悄悄串味。
        """
        if self._in_flight:
            raise RuntimeError("group turn already running — 群轮必须串行，永不并行")
        self._in_flight = True
        try:
            results: list[GroupTurnResult] = []
            pending = self.take_pending()
            while pending:
                result = await self._run_one(member_ids, tuple(pending))
                results.append(result)
                if self._on_turn_end is not None:
                    await self._on_turn_end()
                pending = self.take_pending()
            return results
        finally:
            self._in_flight = False
            # 队列**不**清空：异常路径上那里可能有已经落库的用户消息，清掉等于把
            # 那一轮永远丢掉（下一次 run() 会补上）。

    async def _run_one(
        self, member_ids: Sequence[str], user_messages: tuple[str, ...]
    ) -> GroupTurnResult:
        utterances: list[GroupUtterance] = []
        stopped_for_interjection = False
        for character_id in member_ids:
            if self._queued:
                # 用户插话了：当前角色这一轮（= 当前这条流）已经收尾，旧轮立即收束 ——
                # 剩下的成员不再就旧消息发言，交给 run() 开新轮。
                stopped_for_interjection = True
                break
            if len(utterances) >= self._chain_budget:
                break  # 到顶：整群静默，剩下的成员不再被打扰
            reply = await self._speak(
                character_id, is_continuation=False, user_messages=user_messages
            )
            if is_silent(reply):
                continue
            await self._accept(utterances, character_id, reply)

            while len(utterances) < self._chain_budget:
                if self._queued:
                    # 插话：当前这条流（第一句也算）已经收尾 —— 既不续说，
                    # 也不让剩余成员就旧消息发言。
                    break
                more = await self._speak(
                    character_id, is_continuation=True, user_messages=user_messages
                )
                if is_silent(more):
                    break  # 说完了
                await self._accept(utterances, character_id, more)

        return GroupTurnResult(
            user_messages,
            tuple(utterances),
            stopped_for_interjection=stopped_for_interjection or bool(self._queued),
            budget_exhausted=len(utterances) >= self._chain_budget,
        )

    async def _accept(
        self, utterances: list[GroupUtterance], character_id: str, content: str
    ) -> None:
        """采纳一条消息：计数，并立刻交给 sink（落库 + 推送不等到轮末）。"""
        utterance = GroupUtterance(character_id, content)
        utterances.append(utterance)
        if self._on_utterance is not None:
            await self._on_utterance(utterance)
