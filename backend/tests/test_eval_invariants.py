"""群聊不变量（ticket 08 / spec S4）：自测 + 真实调度器上的场景。

两件事都必须成立：

1. **每个不变量都能被打红** —— 手工构造一个"故意违反"的观测，断言它抛 AssertionError。
   否则这些断言本身不可信（永远绿的测试等于没测）。
2. **真实调度器上它们确实绿** —— 用脚本化假模型驱动 `GroupChatSession` +
   `GroupTurnOrchestrator`，零 token、零网络、秒级完成。
"""

import asyncio

import pytest

from eval.group_turn_eval import (
    INVARIANTS,
    SCENARIOS,
    PersistedRow,
    RoundRecord,
    TurnObservation,
    check_invariants,
    run_scenario,
)


def _obs(
    *,
    members=("g1", "g2", "g3"),
    rounds=(),
    events=(),
    persisted=(),
    user_messages=("hi",),
) -> TurnObservation:
    return TurnObservation(
        member_ids=tuple(members),
        rounds=tuple(rounds),
        events=tuple(events),
        persisted=tuple(persisted),
        user_messages=tuple(user_messages),
    )


def _round(character_id="g1", *, tick=1, turn=1, text="说了", silent=False, cancelled=False):
    return RoundRecord(
        character_id=character_id,
        tick=tick,
        turn_index=turn,
        text=text,
        silent=silent,
        cancelled=cancelled,
    )


def _row(seq, *, role="assistant", speaker="g1", content="说了") -> PersistedRow:
    return PersistedRow(seq=seq, role=role, speaker_id=speaker, content=content)


class TestEachInvariantGoesRed:
    """S4：每个不变量各有一条"故意违反 → 断言红"。"""

    def test_all_members_start(self):
        obs = _obs(rounds=[_round("g1"), _round("g2")])   # g3 一直没开口

        with pytest.raises(AssertionError):
            INVARIANTS["all_members_start"](obs)

    def test_per_member_rounds(self):
        # 3 人 → K=3；给 g1 4 轮
        obs = _obs(rounds=[_round("g1", tick=t) for t in range(1, 5)])

        with pytest.raises(AssertionError):
            INVARIANTS["per_member_rounds"](obs)

    def test_global_messages(self):
        # 上限 9：10 条角色消息必须被打红
        persisted = [_row(1, role="user", speaker=None)] + [
            _row(i + 2) for i in range(10)
        ]

        with pytest.raises(AssertionError):
            INVARIANTS["global_messages"](_obs(persisted=persisted))

    def test_silent_not_persisted(self):
        obs = _obs(persisted=[_row(1, content="<silent>")])

        with pytest.raises(AssertionError):
            INVARIANTS["silent_not_persisted"](obs)

    def test_silent_not_pushed_as_token(self):
        obs = _obs(events=[{"type": "token", "content": "<silent>", "tick": 1}])

        with pytest.raises(AssertionError):
            INVARIANTS["silent_not_persisted"](obs)

    def test_sequence_monotonic(self):
        obs = _obs(persisted=[_row(2), _row(1)])

        with pytest.raises(AssertionError):
            INVARIANTS["sequence_monotonic"](obs)

    def test_sequence_monotonic_push_order(self):
        obs = _obs(
            events=[
                {"type": "group_message", "seq": 2, "tick": 1},
                {"type": "group_message", "seq": 1, "tick": 2},
            ]
        )

        with pytest.raises(AssertionError):
            INVARIANTS["sequence_monotonic"](obs)

    def test_wake_excludes_speaker(self):
        obs = _obs(
            rounds=[_round("g1", tick=5)],
            events=[{"type": "group_message", "character_id": "g1", "tick": 3}],
        )

        with pytest.raises(AssertionError):
            INVARIANTS["wake_excludes_speaker"](obs)

    def test_new_turn_resets(self):
        # 第 2 轮只唤醒了 2 个成员
        obs = _obs(
            user_messages=("一", "二"),
            rounds=[
                _round("g1", turn=1, tick=1),
                _round("g2", turn=1, tick=2),
                _round("g3", turn=1, tick=3),
                _round("g1", turn=2, tick=4),
                _round("g2", turn=2, tick=5),
            ],
        )

        with pytest.raises(AssertionError):
            INVARIANTS["new_turn_resets"](obs)

    def test_no_cancel_in_flight(self):
        obs = _obs(rounds=[_round("g1", cancelled=True)])

        with pytest.raises(AssertionError):
            INVARIANTS["no_cancel_in_flight"](obs)


class TestCheckInvariants:
    def test_a_satisfied_observation_passes_everything(self):
        obs = _obs(
            rounds=[
                _round("g1", tick=1),
                _round("g2", tick=2),
                _round("g3", tick=3),
            ],
            events=[
                {"type": "group_message", "character_id": "g1", "seq": 2, "tick": 4},
                {"type": "group_message", "character_id": "g2", "seq": 3, "tick": 5},
            ],
            persisted=[
                _row(1, role="user", speaker=None),
                _row(2, speaker="g1"),
                _row(3, speaker="g2"),
            ],
        )

        checks, messages = check_invariants(INVARIANTS, obs)

        assert all(checks.values()), messages
        assert messages == []

    def test_failures_carry_a_reason(self):
        obs = _obs(rounds=[_round("g1")])

        checks, messages = check_invariants(["all_members_start"], obs)

        assert checks == {"all_members_start": False}
        assert messages and "g2" in messages[0]

    def test_an_unknown_invariant_name_is_an_error(self):
        with pytest.raises(ValueError):
            check_invariants(["no_such_invariant"], _obs())


# ═══════════════════════════════════════════════════════════════════════
# 真实调度器上的场景（脚本化假模型）
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def eval_env(tmp_path):
    """临时文件库（群场景会并发读写，memory + StaticPool 反而测不出真问题）。"""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    # 建表前必须先把模型模块导入进来（否则 Base.metadata 是空的）
    import models.character  # noqa: F401
    import models.conversation  # noqa: F401
    import models.group  # noqa: F401
    import models.llm_usage  # noqa: F401
    import models.memory  # noqa: F401
    import models.message  # noqa: F401
    import models.user_config  # noqa: F401

    from database import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'eval-group.db'}")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    factory = asyncio.run(_create())
    yield factory
    asyncio.run(engine.dispose())


class TestScenariosOnTheRealScheduler:
    """零 token、零网络：模型是脚本，调度器/落库/推送是真的。"""

    @pytest.mark.asyncio
    async def test_three_members_all_speak_within_the_budget(self, eval_env):
        observation = await run_scenario(SCENARIOS["three_all_speak"], eval_env)

        checks, messages = check_invariants(INVARIANTS, observation)
        assert all(checks.values()), messages
        # 3 人全员真说了话：K=3 → 最多 9 条 = 全局预算
        assert len([r for r in observation.persisted if r.role == "assistant"]) == 9

    @pytest.mark.asyncio
    async def test_four_members_with_two_silent_members(self, eval_env):
        observation = await run_scenario(SCENARIOS["four_two_silent"], eval_env)

        checks, messages = check_invariants(INVARIANTS, observation)
        assert all(checks.values()), messages

        # 沉默的两位一轮都没落库，但都思考过（沉默照样消耗轮次）
        assistant_speakers = {
            r.speaker_id for r in observation.persisted if r.role == "assistant"
        }
        assert assistant_speakers == {"g1", "g2"}
        silent_members = {r.character_id for r in observation.rounds if r.silent}
        assert silent_members == {"g3", "g4"}

    @pytest.mark.asyncio
    async def test_five_members_use_the_large_group_cap(self, eval_env):
        observation = await run_scenario(SCENARIOS["five_members"], eval_env)

        checks, messages = check_invariants(INVARIANTS, observation)
        assert all(checks.values()), messages

        # N>4 → K=2：开口的两人各恰好用满两轮（把 K 改大就会红）
        per_member: dict[str, int] = {}
        for record in observation.rounds:
            per_member[record.character_id] = per_member.get(record.character_id, 0) + 1
        assert per_member["g1"] == 2 and per_member["g2"] == 2
        assert len([r for r in observation.persisted if r.role == "assistant"]) == 4

    @pytest.mark.asyncio
    async def test_a_second_user_turn_resets_the_budgets(self, eval_env):
        observation = await run_scenario(SCENARIOS["two_turns"], eval_env)

        checks, messages = check_invariants(INVARIANTS, observation)
        assert all(checks.values()), messages

        turns = {r.turn_index for r in observation.rounds}
        assert turns == {1, 2}
        for turn in (1, 2):
            members = {r.character_id for r in observation.rounds if r.turn_index == turn}
            assert members == {"g1", "g2", "g3"}

    @pytest.mark.asyncio
    async def test_in_flight_rounds_are_not_cancelled(self, eval_env):
        observation = await run_scenario(SCENARIOS["slow_members"], eval_env, delay=0.002)

        assert not [r for r in observation.rounds if r.cancelled]
        checks, messages = check_invariants(["no_cancel_in_flight"], observation)
        assert all(checks.values()), messages

    @pytest.mark.asyncio
    async def test_an_interjection_starts_a_new_turn_without_cancelling_in_flight_rounds(
        self, eval_env
    ):
        """用户插话：旧的思考必须说完（不取消），插话之后再开一轮、K/M 重新计。"""
        observation = await run_scenario(
            SCENARIOS["interjected"], eval_env, delay=0.002
        )

        checks, messages = check_invariants(INVARIANTS, observation)
        assert all(checks.values()), messages

        # 插话真的造出了第二轮，且第二轮里全员都被唤醒
        turns = {r.turn_index for r in observation.rounds}
        assert turns == {1, 2}
        assert {r.character_id for r in observation.rounds if r.turn_index == 2} == {
            "g1",
            "g2",
            "g3",
        }
        # 插话前就已经在跑的那一轮没有被取消
        assert not [r for r in observation.rounds if r.cancelled]

    @pytest.mark.asyncio
    async def test_every_scenario_is_fast(self, eval_env):
        """不变量用例必须秒级（它们是日常回归的一部分）。"""
        loop = asyncio.get_running_loop()
        started = loop.time()

        for scenario in SCENARIOS.values():
            await run_scenario(scenario, eval_env)

        assert loop.time() - started < 2.0

    @pytest.mark.asyncio
    async def test_scenarios_are_fast(self, eval_env):
        """不变量用例必须秒级（它们是日常回归的一部分）。"""
        loop = asyncio.get_running_loop()
        started = loop.time()

        await run_scenario(SCENARIOS["three_all_speak"], eval_env)

        assert loop.time() - started < 2.0
