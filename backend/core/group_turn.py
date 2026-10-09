"""群轮编排（ticket #09, spec: group-chat Phase 2 · 群轮编排（并发 — ADR-0009））。

**纯逻辑**：不碰 DB / 网络 / 时钟。"谁在什么时候想、谁被唤醒、说几条、什么时候收束"
全在这里；"跑一个成员的一轮"由调用方注入（`speak`），由它负责 LLM、落库与推送。
并发用的是调用方事件循环上的 `asyncio` 原语（`create_task` / `wait`）—— 原语不是 IO，
`run()` 会把所有在途生成等完，所以测试直接 await 它就能确定地断言并发语义（spec S3）。

契约（ADR-0009）：

- **全员起跑**：一个用户消息让**所有**成员同时开始思考（各自的独立生成），谁先想好谁先说。
- **消息唤醒**：每有一条新消息落库，就唤醒每个「空闲且未用满 K」的成员再想一轮 ——
  但**不说醒说这句话的人自己**：那就是「继续说」换个名字（ADR-0009 的标题是
  "new messages wake **the rest**"，spec 举的例子是 B 评论 A）。
- **一次思考最多一条消息**：`<silent>` 不产消息（不落库、不推送），但**仍消耗一轮**。
- **两道上限**（每个用户轮）：每成员最多 K 次思考（N≤4 → 3，N>4 → 2）；全局角色
  消息最多 M = 9。调用次数天然有界（≤ N×K），所以不设独立调用上限。
- **额度预留**：发起一次思考**前**先确认还存得下它的消息（`已存 + 在途 < M`）——
  绝不"生成完再丢弃"。
- **在途永不取消**：正在生成的成员先把这一轮说完；它思考期间落库的新消息会在下一轮
  被自然读到（每轮都重读整段对话），所以"注入"不需要新机制。
- **收敛**：所有成员都用满 K，或没人再产出消息且没有在途生成。没人产消息就没有唤醒
  事件，所以"全员沉默"时每人只烧一轮。
- **新用户消息 = 全新的用户轮**：K 与 M 重置、唤醒全员；在途成员先说完手头这轮。

用户消息只有一条入口：`submit()` 入队，`run()` 跑完队列里所有待处理的消息。
`run()` 会把所有在途生成等完（并接力队列里的插话），所以调用方 await 它就是确定的。

**任务句柄由驱动自管**（`services/group_chat.py` 的 `_turn_task`）：`submit()` 的返回值
只表示"入队时是否已经有在途批次"，驱动不靠它决策。驱动之所以不会开出第二批，是因为
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

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence

#: 每个「用户轮」的角色消息总量上限（ADR-0009：M = 9）。
GROUP_MESSAGE_BUDGET = 9

#: 每成员每用户轮的思考轮上限按人数分档：N≤4 → 3，N>4 → 2（ADR-0009）。
SMALL_GROUP_SIZE = 4
SMALL_GROUP_ROUNDS = 3
LARGE_GROUP_ROUNDS = 2

#: 提示词约定：无话可说时只输出这个标记。
SILENT_MARKER = "<silent>"

#: (character_id, *, is_continuation, user_messages) → 回复原文（None = 什么都没生成出来）。
#: `is_continuation` 自 ticket #08 起恒为 False（续说已停用），形参暂留待清理。
SpeakFn = Callable[..., Awaitable["str | None"]]

#: 每条被采纳的消息一决定就交出去（驱动据此落库 + 推送，不必等整轮跑完）。
UtteranceSink = Callable[["GroupUtterance"], Awaitable[None]]

#: 每个用户轮跑完时回调（驱动据此按轮发事件；轮末调用时 `has_pending()` 仍准）。
#: 不带载荷：驱动只需要"这一轮结束了 + 队列里还有吗"，轮内容由 `run()` 的返回值给。
TurnEndSink = Callable[[], Awaitable[None]]

#: 标记周围可能出现的修饰：模型偶尔会写成 `"<silent>"`、`（<silent>）` 之类。
_DECORATION = "`*_~\"'“”‘’()（）[]【】{}<>《》:：,，.。!！?？-— \t\r\n"


def rounds_cap(member_count: int) -> int:
    """每成员每用户轮最多思考几次（按人数分档，ADR-0009）。"""
    return SMALL_GROUP_ROUNDS if member_count <= SMALL_GROUP_SIZE else LARGE_GROUP_ROUNDS


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


@dataclass
class _TurnState:
    """一个用户轮内的调度状态。

    **只由父任务（`_run_turn`）改写**：成员各自的生成跑在子任务里、只产出文本，
    文本回到父任务才计数、才交给 sink。所以并发下不需要锁，同一拍完成的多条也能
    按成员顺序确定地采纳（落库顺序因此不靠时间戳 —— 见 ticket #56 的消息序号）。
    """

    user_messages: tuple[str, ...]
    members: list[str]
    cap: int
    rounds: dict[str, int] = field(default_factory=dict)      # 每人已消耗的思考轮
    woken_at: dict[str, int] = field(default_factory=dict)    # 每人上次起跑时的唤醒代号
    gen: int = 0                    # 唤醒代号：每落库一条消息 +1
    messages: int = 0               # 本轮已产出的角色消息数
    utterances: list[GroupUtterance] = field(default_factory=list)
    running: dict[str, asyncio.Task] = field(default_factory=dict)   # 每人在途的任务

    @classmethod
    def start(
        cls, member_ids: Sequence[str], user_messages: tuple[str, ...]
    ) -> "_TurnState":
        """新用户轮：K/M 重置，全员都算"还没被唤醒过"，于是**所有人同时起跑**。"""
        # 去重保序：同一个角色出现两次会开出两条并发的同人设生成
        members = list(dict.fromkeys(member_ids))
        return cls(
            user_messages=user_messages,
            members=members,
            cap=rounds_cap(len(members)),
            rounds={m: 0 for m in members},
            woken_at={m: -1 for m in members},
        )

    def ready(self, character_id: str) -> bool:
        """空闲（没有在途轮）、未用满 K、且上次起跑之后又落了新消息（= 被唤醒）。"""
        return (
            character_id not in self.running
            and self.rounds[character_id] < self.cap
            and self.woken_at[character_id] < self.gen
        )

    def has_slot(self, max_messages: int) -> bool:
        """额度预留：在途的每一轮都占一个名额，先确认还存得下再发起。"""
        return self.messages + len(self.running) < max_messages

    def spend_round(self, character_id: str) -> None:
        """起跑即消耗一轮（跳过也算）。"""
        self.rounds[character_id] += 1
        self.woken_at[character_id] = self.gen

    def accept(self, character_id: str, content: str) -> GroupUtterance:
        """采纳一条消息：计数，并把它记成一次唤醒事件。

        说这句话的人自己**不**被自己的消息唤醒（那就是「继续说」换个名字）：把它
        记成"已经消费了这一代唤醒"，别人之后开口时会再次唤醒它。
        """
        utterance = GroupUtterance(character_id, content)
        self.utterances.append(utterance)
        self.messages += 1
        self.gen += 1
        self.woken_at[character_id] = self.gen
        return utterance


class GroupTurnOrchestrator:
    """驱动一条群对话的用户轮（ticket #09）。

    调用方（WS 侧的驱动）负责"何时开跑"：`submit()` 的返回值告诉它队列里已经有了
    消息而当前没有在途批次，于是自己起一个任务 await `run()`；`run()` 会把在它执行
    期间 submit 进来的插话自动接力成新轮，所以一个任务就能跑完一串用户轮。

    **成员之间并发，批次之间串行**：`run()` 不可重入（同一实例同时跑两批会串味），
    但一批之内每个成员各自一条独立生成、互不阻塞。**状态只由父任务改写**（子任务
    只把文本交回来），所以并发下不需要锁，同一拍完成的多条也按成员顺序确定地落库。
    """

    def __init__(
        self,
        speak: SpeakFn,
        *,
        on_utterance: "UtteranceSink | None" = None,
        on_turn_end: "TurnEndSink | None" = None,
        max_messages: int = GROUP_MESSAGE_BUDGET,
    ):
        self._speak = speak
        self._on_utterance = on_utterance
        self._on_turn_end = on_turn_end
        self._max_messages = max_messages
        self._queued: list[str] = []
        self._in_flight = False
        self._turn: _TurnState | None = None   # 一个用户轮的调度状态（每轮重建）

    @property
    def in_flight(self) -> bool:
        """是否有一批群轮在途（在途时新的用户消息只是排队，不打断）。"""
        return self._in_flight

    def has_pending(self) -> bool:
        """队列里是否还有没轮到的用户消息。"""
        return bool(self._queued)

    def submit(self, text: str) -> bool:
        """用户说了一句：入队。

        **同步**方法：没有 await，所以两条几乎同时到达的消息不可能各自开出一批。
        返回值只作参考：True = 入队时已经有在途批次（它会自己接力成新轮）；
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
        """把队列里的用户消息一轮一轮跑完（插话合并、每轮 K/M 重置）。

        每跑完一个用户轮就回调 `on_turn_end()`（此时队列**还没取**，所以
        `has_pending()` 仍能回答"还有下一轮吗"）；回调里 await 期间进来的新消息
        会在紧随其后的 `take_pending()` 被取走，不会漏。

        不可重入：同一实例同时在跑两批就违反"批次串行"，因此直接报错而不是悄悄串味。
        """
        if self._in_flight:
            raise RuntimeError(
                "group turn burst already running — 一批群轮只有一个 runner（成员之间才并发）"
            )
        self._in_flight = True
        try:
            results: list[GroupTurnResult] = []
            pending = self.take_pending()
            while pending:
                result = await self._run_turn(list(member_ids), tuple(pending))
                results.append(result)
                if self._on_turn_end is not None:
                    await self._on_turn_end()
                pending = self.take_pending()
            return results
        finally:
            self._in_flight = False
            # 队列**不**清空：异常路径上那里可能有已经落库的用户消息，清掉等于把
            # 那一轮永远丢掉（下一次 run() 会补上）。

    # ── 一个用户轮 ──────────────────────────────────────────────────

    async def _run_turn(
        self, member_ids: Sequence[str], user_messages: tuple[str, ...]
    ) -> GroupTurnResult:
        turn = self._turn = _TurnState.start(member_ids, user_messages)
        stopped = False
        failure: BaseException | None = None

        try:
            while True:
                if self._queued:
                    # 用户插话：旧轮不再开新的思考，但**在途的必须说完**（永不取消）。
                    stopped = True
                if failure is None and not stopped:
                    self._start_ready(turn)
                if not turn.running:
                    break  # 收敛：没人能起跑、也没有在途
                done, _ = await asyncio.wait(
                    set(turn.running.values()), return_when=asyncio.FIRST_COMPLETED
                )
                # 同一拍完成的多条按**成员顺序**采纳（父任务是唯一的状态改写者）
                for character_id, task in list(turn.running.items()):
                    if task not in done:
                        continue
                    del turn.running[character_id]
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        if failure is None:
                            failure = exc
                        continue
                    reply = task.result()
                    if not is_silent(reply):
                        await self._accept(character_id, reply)
        except BaseException:
            # 被取消（连接断开 → 驱动取消这一批）时连在途的孩子一起收掉：它们是
            # "这一批"的一部分，留着会继续跑。注意这与"插话不取消在途"不是一回事：
            # 那是**批次内**的插话，批次还在。
            for task in turn.running.values():
                task.cancel()
            await asyncio.gather(*turn.running.values(), return_exceptions=True)
            turn.running.clear()
            raise
        finally:
            self._turn = None

        if failure is not None:
            raise failure
        return GroupTurnResult(
            user_messages,
            tuple(turn.utterances),
            stopped_for_interjection=stopped or bool(self._queued),
            budget_exhausted=turn.messages >= self._max_messages,
        )

    def _start_ready(self, turn: _TurnState) -> None:
        """把此刻"该说且能说"的成员都跑起来（一次遍历 = 全员同时起跑）。"""
        for character_id in turn.members:
            if not turn.ready(character_id):
                continue  # 在途 / 用满 K / 上次起跑之后没有新消息
            if not turn.has_slot(self._max_messages):
                continue  # 额度预留：先确认还存得下这条消息，绝不 generate-then-drop
            turn.spend_round(character_id)
            turn.running[character_id] = asyncio.create_task(
                self._round(character_id, turn.user_messages)
            )

    async def _round(
        self, character_id: str, user_messages: tuple[str, ...]
    ) -> "str | None":
        """跑一个成员的一轮：**只产出文本**（空 / `<silent>` = 这一轮什么都不说）。

        不在这里计数、也不在这里落库 —— 状态只由父任务改写，所以并发下不需要锁。
        """
        return await self._speak(
            character_id, is_continuation=False, user_messages=user_messages
        )

    async def _accept(self, character_id: str, content: str) -> None:
        """采纳一条消息：交给状态计数（并记成一次唤醒事件），再立刻交给 sink。"""
        assert self._turn is not None  # 只有父任务会调它，且只在 `_run_turn` 期间
        utterance = self._turn.accept(character_id, content)
        if self._on_utterance is not None:
            await self._on_utterance(utterance)
