"""群聊不变量（ticket 08 / spec: agent-eval-v2 §不变量注册表）。

用**脚本化假模型**驱动**真实调度器**（`GroupTurnOrchestrator` + `GroupChatSession`），
断言 ADR-0009 写死的那些上界与顺序 —— 零 token、零网络、秒级完成。

测的是"调度是否守规矩"，不是"角色说得好不好"：真模型下这些上界本来由编排器保证，
用真模型跑只会引入 flaky 并每次烧 N×K 次调用。

每个不变量都能被"故意违反"的观测打红（`tests/test_eval_invariants.py` 逐个验证），
否则这些断言本身就是不可信的。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from core.group_turn import (
    GROUP_MESSAGE_BUDGET,
    SILENT_MARKER,
    is_silent,
)

# ═══════════════════════════════════════════════════════════════════════
# 场景（数据）：成员数 + 依次发送的用户消息 + 每个成员的脚本
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GroupScenario:
    """一次群聊场景：谁在群里、用户说了什么、每个成员每轮说什么。"""

    id: str
    member_count: int
    user_messages: tuple[str, ...]
    #: 成员 id → 逐轮回复（超出部分重复最后一条；不在表里的成员永远是沉默）
    scripts: dict[str, list[str]] = field(default_factory=dict)
    #: 插话：第 N 条用户消息不排队等前一轮跑完，而是在出现这么多条角色消息时就发出去
    #: （None = 各轮依次串行；这也是"用户插话"这条用例的开关）
    interject_after: int | None = None
    #: 每个思考轮的模拟延迟（让"在途"真的有一个挂起点，取消才可能被观测到）
    delay: float = 0.0

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(f"g{i}" for i in range(1, self.member_count + 1))


def _speaking_script(member_ids: Iterable[str], lines: list[str]) -> dict[str, list[str]]:
    return {cid: list(lines) for cid in member_ids}


_THREE = GroupScenario(
    id="three_all_speak",
    member_count=3,
    user_messages=("你们都在吗？",),
    scripts=_speaking_script(("g1", "g2", "g3"), ["我在", "我也在", "还有我"]),
)

_FOUR_TWO_SILENT = GroupScenario(
    id="four_two_silent",
    member_count=4,
    user_messages=("有人吗？",),
    # g3 / g4 不在脚本里 → 永远沉默（沉默照样消耗思考轮、但不落库）
    scripts=_speaking_script(("g1", "g2"), ["甲在", "乙也在", "甲再说一句"]),
)

_FIVE = GroupScenario(
    id="five_members",
    member_count=5,
    user_messages=("开会了，都说说",),
    # 只有两人开口、其余三人一直沉默：这样每人恰好用满 K=2（若核心把 K 改大，这里会红）
    scripts=_speaking_script(("g1", "g2"), ["一", "二", "三"]),
)

_TWO_TURNS = GroupScenario(
    id="two_turns",
    member_count=3,
    user_messages=("第一件事：你们怎么看？", "第二件事呢？"),
    scripts=_speaking_script(("g1", "g2", "g3"), ["说一句", "再说一句", "还说一句"]),
)

_SLOW = GroupScenario(
    id="slow_members",
    member_count=3,
    user_messages=("慢慢说，别急",),
    scripts=_speaking_script(("g1", "g2", "g3"), ["慢一点说"]),
    delay=0.002,
)

_INTERJECTED = GroupScenario(
    id="interjected",
    member_count=3,
    user_messages=("先说说你们的看法", "等一下，我补充一句"),
    scripts=_speaking_script(("g1", "g2", "g3"), ["第一反应", "我也说说", "还有一点"]),
    interject_after=2,      # 出现两条角色消息后用户插话
    delay=0.002,
)

SCENARIOS: dict[str, GroupScenario] = {
    scenario.id: scenario
    for scenario in (_THREE, _FOUR_TWO_SILENT, _FIVE, _TWO_TURNS, _SLOW, _INTERJECTED)
}


# ═══════════════════════════════════════════════════════════════════════
# 观测模型
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RoundRecord:
    """一次思考轮（一次 `speak` 调用）。"""

    character_id: str
    #: 起跑时刻（场景内单调递增，用来判断"谁先谁后"）
    tick: int
    #: 第几个用户轮（1 起）
    turn_index: int
    text: str | None
    silent: bool
    cancelled: bool = False


@dataclass(frozen=True)
class PersistedRow:
    """落库的一条消息。"""

    seq: int
    role: str
    speaker_id: str | None
    content: str


@dataclass(frozen=True)
class TurnObservation:
    """一次场景跑完之后能观察到的一切（不变量的输入）。"""

    member_ids: tuple[str, ...]
    rounds: tuple[RoundRecord, ...]
    events: tuple[dict, ...]
    persisted: tuple[PersistedRow, ...]
    user_messages: tuple[str, ...]


# ═══════════════════════════════════════════════════════════════════════
# 脚本化假模型 + 场景驱动
# ═══════════════════════════════════════════════════════════════════════


class ScenarioClock:
    """场景内单调递增的 tick + 当前用户轮序号（由 `turn_end` 事件推进）。"""

    def __init__(self) -> None:
        self.tick = 0
        self.turn_ends = 0

    @property
    def turn(self) -> int:
        """当前用户轮（1 起）：已结束的轮数 + 1。"""
        return self.turn_ends + 1

    def next(self) -> int:
        self.tick += 1
        return self.tick

    def note_turn_end(self) -> None:
        self.turn_ends += 1


class ScriptedGroupAgent:
    """按脚本逐轮产出文本的假 Agent（一次 `run` = 一个思考轮）。

    `GroupChatSession._speak` 只关心 `done` 事件里的 `content`，所以这里不碰模型。
    """

    def __init__(
        self,
        scripts: dict[str, list[str]],
        clock: ScenarioClock,
        *,
        delay: float = 0.0,
    ) -> None:
        self._scripts = scripts
        self._clock = clock
        self._delay = delay
        self.rounds: list[RoundRecord] = []

    def _script_for(self, character_id: str) -> list[str]:
        return self._scripts.get(character_id, [SILENT_MARKER])

    def _next_line(self, character_id: str) -> str:
        script = self._script_for(character_id)
        index = len([r for r in self.rounds if r.character_id == character_id])
        return script[min(index, len(script) - 1)]

    async def run(self, **kwargs):
        character_id = kwargs["character_id"]
        tick = self._clock.next()
        turn_index = self._clock.turn
        cancelled = False
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            text = self._next_line(character_id)
            yield {"type": "done", "content": text, "message_id": f"m-{tick}"}
        except asyncio.CancelledError:      # pragma: no cover - 只有违反不变量时才会走到
            cancelled = True
            raise
        finally:
            text = None if cancelled else locals().get("text")
            self.rounds.append(
                RoundRecord(
                    character_id=character_id,
                    tick=tick,
                    turn_index=turn_index,
                    text=text,
                    silent=is_silent(text),
                    cancelled=cancelled,
                )
            )


class _BatchWaiter:
    """等一批群轮跑完（`on_batch_finished` 回调）。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def __call__(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
        self._event.clear()


async def _seed_group(session_factory, scenario: GroupScenario, conversation_id: str) -> None:
    from models.character import CharacterProfile
    from models.conversation import Conversation
    from models.group import Group, GroupMember

    group_id = f"grp-{scenario.id}"
    async with session_factory() as session:
        if await session.get(Group, group_id) is not None:
            return                      # 同一个库里重复跑同一场景：已经 seed 过了
        session.add(Group(id=group_id, name=f"Eval 群 {scenario.id}"))
        for position, character_id in enumerate(scenario.member_ids):
            # 角色 id 跨场景复用（g1..g5）：已经存在就不要再插一遍（name 是唯一的）
            if await session.get(CharacterProfile, character_id) is None:
                session.add(
                    CharacterProfile(id=character_id, name=f"成员{position + 1}")
                )
            session.add(
                GroupMember(
                    group_id=group_id, character_id=character_id, position=position
                )
            )
        session.add(
            Conversation(
                id=conversation_id, group_id=group_id, title=f"Eval {scenario.id}"
            )
        )
        await session.commit()


async def _persisted_rows(session_factory, conversation_id: str) -> tuple[PersistedRow, ...]:
    from sqlalchemy import select

    from models.message import Message

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq, Message.id)
            )
        ).scalars().all()
    return tuple(
        PersistedRow(
            seq=int(row.seq),
            role=row.role,
            speaker_id=row.speaker_id,
            content=row.content or "",
        )
        for row in rows
    )


async def run_scenario(
    scenario: GroupScenario,
    session_factory,
    *,
    delay: float = 0.0,
) -> TurnObservation:
    """用脚本化假模型跑一遍场景，返回可观测量。

    真实的 `GroupChatSession` + `GroupTurnOrchestrator` 负责调度/落库/推送；只有模型
    被换成脚本。会话库通过替换 `database.async_session` 指到评测库。
    """
    import database

    from services.group_chat import GroupChatSession

    conversation_id = f"conv-{scenario.id}"
    await _seed_group(session_factory, scenario, conversation_id)

    clock = ScenarioClock()
    agent = ScriptedGroupAgent(scenario.scripts, clock, delay=delay or scenario.delay)
    events: list[dict] = []
    waiter = _BatchWaiter()
    interjected = {"done": False}

    session_holder: dict[str, object] = {}

    async def send_json(payload: dict) -> None:
        events.append({**payload, "tick": clock.next(), "turn_index": clock.turn})
        if payload.get("type") == "turn_end":
            # 一轮结束：下一批思考属于下一轮（用户轮序号由它推进）
            clock.note_turn_end()
        # 用户插话：不等这一轮跑完，直接塞进队列（编排器会让旧的思考说完、再开新轮）
        if (
            scenario.interject_after is not None
            and not interjected["done"]
            and payload.get("type") == "group_message"
            and len([e for e in events if e.get("type") == "group_message"])
            >= scenario.interject_after
            and len(scenario.user_messages) > 1
        ):
            interjected["done"] = True
            session_holder["session"].handle_user_message(scenario.user_messages[1])

    original_async_session = database.async_session
    database.async_session = session_factory
    try:
        session = GroupChatSession(
            conversation_id,
            agent=agent,
            send_json=send_json,
            on_batch_finished=waiter,
        )
        session_holder["session"] = session

        if scenario.interject_after is not None:
            # 第一条用户消息 → 批次在插话后继续跑到收敛（一次 wait 就够）
            session.handle_user_message(scenario.user_messages[0])
            await waiter.wait()
        else:
            for text in scenario.user_messages:
                session.handle_user_message(text)
                await waiter.wait()
    finally:
        database.async_session = original_async_session

    return TurnObservation(
        member_ids=scenario.member_ids,
        rounds=tuple(agent.rounds),
        events=tuple(events),
        persisted=await _persisted_rows(session_factory, conversation_id),
        user_messages=scenario.user_messages,
    )


# ═══════════════════════════════════════════════════════════════════════
# 不变量：名字 → 断言（失败抛 AssertionError，消息里写清"哪个成员/哪一轮"）
# ═══════════════════════════════════════════════════════════════════════


def _per_turn_rounds(observation: TurnObservation) -> dict[tuple[int, str], int]:
    counts: dict[tuple[int, str], int] = defaultdict(int)
    for record in observation.rounds:
        counts[(record.turn_index, record.character_id)] += 1
    return dict(counts)


def all_members_start(observation: TurnObservation) -> None:
    """用户消息之后，每个成员都至少有一次思考轮。"""
    started = {record.character_id for record in observation.rounds}
    missing = [m for m in observation.member_ids if m not in started]
    assert not missing, f"这些成员一次都没开始思考：{missing}"


#: 每成员每轮的思考上限 —— **独立复述 ADR-0009**，故意不调 `core.group_turn.rounds_cap`：
#: 用它来算就等于"拿实现验证实现"，核心把 K 改大时永远不会红。
_EXPECTED_ROUND_CAPS = {1: 3, 2: 3, 3: 3, 4: 3, 5: 2, 6: 2, 7: 2, 8: 2}


def expected_rounds_cap(member_count: int) -> int:
    """人 → 每成员每轮思考上限（≤4 人 3 次，>4 人 2 次）。"""
    if member_count in _EXPECTED_ROUND_CAPS:
        return _EXPECTED_ROUND_CAPS[member_count]
    return 2      # 更大的群按大群档


def per_member_rounds(observation: TurnObservation) -> None:
    """每成员每用户轮思考轮 ≤ K（N≤4 → 3，N>4 → 2）。"""
    cap = expected_rounds_cap(len(observation.member_ids))
    over = {
        key: count
        for key, count in _per_turn_rounds(observation).items()
        if count > cap
    }
    assert not over, f"每成员每轮最多 {cap} 次思考，超了：{over}"


def _turn_of_persisted(observation: TurnObservation) -> dict[int, int]:
    """每条落库消息属于第几个用户轮。

    以**推送事件**的 `turn_index` 为准（由 `turn_end` 推进，插话场景也准）；没有对应
    事件的（理论上不会有）沿用上一条的轮次，而不是按"用户消息切段"猜。
    """
    from_events = {
        event["seq"]: event.get("turn_index", 1)
        for event in observation.events
        if event.get("type") == "group_message" and "seq" in event
    }
    turns: dict[int, int] = {}
    current = 1
    for row in observation.persisted:
        current = from_events.get(row.seq, current)
        turns[row.seq] = current
    return turns


def global_messages(observation: TurnObservation) -> None:
    """每个用户轮落库的角色消息 ≤ M=9。"""
    turns = _turn_of_persisted(observation)
    per_turn: dict[int, int] = defaultdict(int)
    for row in observation.persisted:
        if row.role == "assistant":
            per_turn[turns[row.seq]] += 1
    over = {turn: n for turn, n in per_turn.items() if n > GROUP_MESSAGE_BUDGET}
    assert not over, f"每轮落库角色消息 {over} 超过上限 {GROUP_MESSAGE_BUDGET}"


def _turn_of_each_message(observation: TurnObservation) -> dict[int, int]:
    """（兼容旧名）落库消息属于第几个用户轮 —— 见 `_turn_of_persisted`。"""
    return _turn_of_persisted(observation)


def silent_not_persisted(observation: TurnObservation) -> None:
    """`<silent>` 不落库、不推送（token 也不行），也不该留下空壳消息。"""
    leaked_rows = [r for r in observation.persisted if SILENT_MARKER in r.content]
    assert not leaked_rows, f"<silent> 被落库了：{[(r.seq, r.content) for r in leaked_rows]}"

    # 沉默轮不该造出"空消息"：一条空的角色行同样是落库了
    empty_rows = [
        r for r in observation.persisted if r.role == "assistant" and not r.content.strip()
    ]
    assert not empty_rows, f"沉默轮留下了空的角色消息：{[r.seq for r in empty_rows]}"

    leaked_events = [
        e
        for e in observation.events
        if e.get("type") in {"group_message", "token"}
        and SILENT_MARKER in str(e.get("content", ""))
    ]
    assert not leaked_events, f"<silent> 被推送了：{leaked_events[:3]}"

    empty_events = [
        e
        for e in observation.events
        if e.get("type") in {"group_message", "token"}
        and "content" in e
        and not str(e["content"]).strip()
    ]
    assert not empty_events, f"沉默了却推送了空消息：{empty_events[:3]}"


def sequence_monotonic(observation: TurnObservation) -> None:
    """落库消息序号严格递增，且与推送顺序一致。"""
    seqs = [row.seq for row in observation.persisted]
    assert seqs == sorted(seqs), f"落库序号不单调：{seqs}"
    assert len(set(seqs)) == len(seqs), f"落库序号重复：{seqs}"

    pushed = [e["seq"] for e in observation.events if e.get("type") == "group_message"]
    assert pushed == sorted(pushed), f"推送顺序与序号不一致：{pushed}"


def wake_excludes_speaker(observation: TurnObservation) -> None:
    """新消息不唤醒说话者本人。

    只看**同一个用户轮内**、本成员起跑之前最后被采纳的那条消息：轮首的起跑是用户消息
    唤醒的（不是某条角色消息），所以同轮里还没有角色消息时跳过。
    """
    utterances = [
        e for e in observation.events if e.get("type") == "group_message"
    ]
    for record in observation.rounds:
        prior = [
            u
            for u in utterances
            if u["tick"] < record.tick and u.get("turn_index", record.turn_index) == record.turn_index
        ]
        if not prior:
            continue
        last = prior[-1]
        assert last.get("character_id") != record.character_id, (
            f"{record.character_id} 被自己刚说的那条消息唤醒了（tick={record.tick}）"
        )


def new_turn_resets(observation: TurnObservation) -> None:
    """新用户消息 → K/M 重置且全员被唤醒，且**每一轮都真的产出了消息**。"""
    by_turn: dict[int, set[str]] = defaultdict(set)
    for record in observation.rounds:
        by_turn[record.turn_index].add(record.character_id)

    # 每条用户消息都应该真的开出一轮（否则"重置"没有被测到）
    assert len(by_turn) == len(observation.user_messages), (
        f"{len(observation.user_messages)} 条用户消息只开出 {len(by_turn)} 轮"
    )
    for turn, members in sorted(by_turn.items()):
        missing = set(observation.member_ids) - members
        assert not missing, f"第 {turn} 个用户轮里这些成员没被唤醒：{missing}"

    # 重置之后必须**真的重新开口**：只唤醒、不产出，等于重置没生效
    turns_of_rows = _turn_of_persisted(observation)
    produced = {
        turns_of_rows[row.seq]
        for row in observation.persisted
        if row.role == "assistant"
    }
    silent_turns = [turn for turn in by_turn if turn not in produced]
    assert not silent_turns, f"这些用户轮里没有任何角色消息（重置没生效？）：{silent_turns}"


def no_cancel_in_flight(observation: TurnObservation) -> None:
    """在途轮次从不被取消。"""
    cancelled = [r for r in observation.rounds if r.cancelled]
    assert not cancelled, f"有在途轮被取消：{cancelled}"


INVARIANTS = {
    "all_members_start": all_members_start,
    "per_member_rounds": per_member_rounds,
    "global_messages": global_messages,
    "silent_not_persisted": silent_not_persisted,
    "sequence_monotonic": sequence_monotonic,
    "wake_excludes_speaker": wake_excludes_speaker,
    "new_turn_resets": new_turn_resets,
    "no_cancel_in_flight": no_cancel_in_flight,
}


def check_invariants(
    names: Iterable[str], observation: TurnObservation
) -> tuple[dict[str, bool], list[str]]:
    """逐个跑不变量：返回 (每项是否通过, 失败说明)。名字不认识 → ValueError。"""
    checks: dict[str, bool] = {}
    messages: list[str] = []
    for name in names:
        check = INVARIANTS.get(name)
        if check is None:
            raise ValueError(
                f"unknown invariant {name!r}; known: {sorted(INVARIANTS)}"
            )
        try:
            check(observation)
        except AssertionError as exc:
            checks[name] = False
            messages.append(f"{name}: {exc}")
        else:
            checks[name] = True
    return checks, messages
