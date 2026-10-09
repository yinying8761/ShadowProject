"""群轮并发调度器（ticket #09 / ADR-0009）：起跑 / 唤醒 / K 分档 / M 封顶 / 预留 / 收敛。

Seam: `core.group_turn.GroupTurnOrchestrator`（spec S3）。只注入"跑一个成员一轮"的
`speak` 可调用对象 + 假脚本；调度器不碰 DB / 网络，所以「全员同时起跑、谁被唤醒、
每人几轮、全局几条、什么时候收束」全都能在这里直接测出来。

`run()` 会等所有在途生成结束，所以测试直接 await 它即可 —— 不需要额外的等待原语。

用户消息仍然只有一个入口：`submit()`（同步入队）+ `run()`（跑完队列）。
"""

import asyncio

import pytest

from core.group_turn import (
    GROUP_MESSAGE_BUDGET,
    GroupTurnOrchestrator,
    is_silent,
    rounds_cap,
)


class FakeMembers:
    """按脚本回答的假成员：**并发安全**，记录调用与并发峰值。

    - `script`: 每个角色的回复列表，按顺序消费；用完一律 `<silent>`。
    - `hook`: 每次生成时同步回调（模拟"正在生成时用户又发消息"）。
    - `peak`: 同时在途的生成数峰值 —— "全员同时起跑"就是靠它断言的（串行实现只有 1）。

    `await asyncio.sleep(0)` 让出事件循环：真并发才会让同一批的所有成员都在这一拍里
    进入生成，峰值因此是确定的。
    """

    def __init__(self, script: dict[str, list[str | None]] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[str] = []           # 被起跑的顺序
        self.rounds: dict[str, int] = {}     # 每人的思考轮数
        self.peak = 0
        self.hook = None
        self._live = 0

    async def speak(self, character_id, *, is_continuation, user_messages):
        # 续说已停用（ticket #08）：这个形参现在永远是 False，锁住"不可达"
        assert is_continuation is False
        self.calls.append(character_id)
        self.rounds[character_id] = self.rounds.get(character_id, 0) + 1
        self._live += 1
        self.peak = max(self.peak, self._live)
        try:
            if self.hook:
                self.hook(character_id)
            await asyncio.sleep(0)
            replies = self.script.get(character_id, [])
            return replies.pop(0) if replies else "<silent>"
        finally:
            self._live -= 1


async def _run(orch: GroupTurnOrchestrator, members, *user_messages) -> list:
    """提交若干条用户消息，然后把队列跑完（= WS 侧一个用户消息批次一个任务）。"""
    for text in user_messages:
        orch.submit(text)
    return await orch.run(list(members))


def _speakers_of(turn, members) -> set[str]:
    return {u.character_id for u in turn.utterances}


def _one_submit_per_turn(orch, text):
    """在**第一个**在途轮里插一句话（只插一次）。"""
    fired = {"done": False}

    def hook(character_id):
        if fired["done"]:
            return
        fired["done"] = True
        assert orch.submit(text) is True, "在途时插话必须被接受（排队，不打断）"

    return hook


class TestSilentMarker:
    """`<silent>` 的识别：只有"整条就是标记"才算跳过。"""

    @pytest.mark.parametrize(
        "reply",
        ["<silent>", " <silent>\n", "`<silent>`", "（<silent>）", "“<silent>”", "", "   ", None],
    )
    def test_recognised_as_silent(self, reply):
        assert is_silent(reply) is True

    @pytest.mark.parametrize(
        "reply",
        ["<silent>不过我还是想说", "我没什么想说的", "silent", "（无话可说）"],
    )
    def test_anything_carrying_real_content_is_not_silent(self, reply):
        assert is_silent(reply) is False


class TestRoundsCapTiers:
    """K 按人数分档（ADR-0009：N≤4 → 3；N>4 → 2）。"""

    @pytest.mark.parametrize("member_count", [1, 2, 3, 4])
    def test_small_groups_get_three_rounds(self, member_count):
        assert rounds_cap(member_count) == 3

    @pytest.mark.parametrize("member_count", [5, 6, 9])
    def test_larger_groups_get_two_rounds(self, member_count):
        assert rounds_cap(member_count) == 2


class TestConcurrentStart:
    """全员起跑：一个用户消息让所有成员**同时**开始思考。"""

    async def test_a_user_message_starts_every_member_at_once(self):
        speakers = FakeMembers({"a": ["A1"], "b": ["B1"], "c": ["C1"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b", "c"], "在吗"))[0]

        assert speakers.peak == 3, "全员必须同时在途（串行实现只会有 1）"
        assert _speakers_of(turn, ["a", "b", "c"]) == {"a", "b", "c"}
        assert all(rounds >= 1 for rounds in speakers.rounds.values())

    async def test_a_silent_member_is_dropped_without_an_utterance(self):
        speakers = FakeMembers({"a": ["<silent>"], "b": ["B1"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗"))[0]

        assert [u.content for u in turn.utterances] == ["B1"]
        assert speakers.rounds["a"] >= 1  # 跳过照样消耗一轮（只是不产消息）

    async def test_everyone_silent_converges_after_one_round_each(self):
        """没人产消息 → 没有唤醒事件 → 每人一轮就收敛（不会白烧 K 轮）。"""
        speakers = FakeMembers()

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗"))[0]

        assert turn.utterances == ()
        assert speakers.rounds == {"a": 1, "b": 1}
        assert turn.budget_exhausted is False

    async def test_the_turn_carries_the_user_messages_it_was_anchored_on(self):
        speakers = FakeMembers({"a": ["A1"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a"], "第一句", "第二句"))[0]

        assert turn.user_messages == ("第一句", "第二句")


class TestWakeAndCaps:
    """事件唤醒 + 两道上限（每人 K 轮、全局 M 条）。"""

    async def test_a_new_message_wakes_the_other_idle_members(self):
        """a 说了一句 → 把第一轮沉默的 b 唤醒，b 因此说上话。"""
        speakers = FakeMembers({"a": ["A1"], "b": ["<silent>", "B1"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗"))[0]

        assert [u.content for u in turn.utterances] == ["A1", "B1"]
        assert speakers.rounds["b"] >= 2  # 被 a 的消息唤醒过

    async def test_a_speaker_is_not_woken_by_its_own_message(self):
        """自己的消息不唤醒自己 —— 否则就是「继续说」换个名字（ADR-0009 标题：wake **the rest**）。"""
        speakers = FakeMembers({"a": ["A1", "A2", "A3"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a"], "在吗"))[0]

        assert [u.content for u in turn.utterances] == ["A1"]
        assert speakers.rounds["a"] == 1

    async def test_each_member_is_capped_at_three_rounds_in_a_small_group(self):
        """N=2（≤4 → K=3）：两人互相唤醒直到双双用满 3 轮，恰好 6 条消息。"""
        speakers = FakeMembers({m: [f"{m.upper()}{i}" for i in range(1, 6)] for m in "ab"})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗"))[0]

        assert speakers.rounds == {"a": 3, "b": 3}
        assert len(turn.utterances) == 6  # M=9 未到顶：封顶的是 K

    async def test_each_member_is_capped_at_two_rounds_above_four_members(self):
        """N=5（>4 → K=2）：a/b 一直说，a/b 各 2 轮；沉默的 c/d/e 也只被唤醒到 2 轮。"""
        members = list("abcde")
        speakers = FakeMembers({"a": ["A1", "A2"], "b": ["B1", "B2"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), members, "在吗"))[0]

        assert speakers.rounds == {m: 2 for m in members}
        assert len(turn.utterances) == 4
        assert turn.budget_exhausted is False

    async def test_the_global_budget_caps_the_turn_without_dropping_a_generation(self):
        """N=4（K=3）全员一直说：全局封顶 M=9，而且**没有一次生成被丢掉**。"""
        members = list("abcd")
        speakers = FakeMembers({m: [f"{m.upper()}{i}" for i in range(1, 9)] for m in members})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), members, "在吗"))[0]

        assert len(turn.utterances) == GROUP_MESSAGE_BUDGET == 9
        assert len(speakers.calls) == 9, "发起 9 次就该存下 9 条（额度预留：不多起一次）"
        assert all(rounds <= 3 for rounds in speakers.rounds.values())
        assert turn.budget_exhausted is True

    async def test_a_round_is_never_started_without_a_message_slot(self):
        """额度预留：余额只剩 1 条时**只起一个人**，b 压根没被起跑（不是跑完再丢）。"""
        speakers = FakeMembers({"a": ["A1"], "b": ["B1"]})

        turn = (await _run(
            GroupTurnOrchestrator(speakers.speak, max_messages=1), ["a", "b"], "在吗"
        ))[0]

        assert [u.content for u in turn.utterances] == ["A1"]
        assert speakers.calls == ["a"]
        assert turn.budget_exhausted is True


class TestInterjection:
    """插话：不打断在途生成；新用户消息 = **全新的用户轮**（K/M 重置 + 唤醒全员）。"""

    async def test_an_interjection_does_not_cancel_the_in_flight_rounds(self):
        speakers = FakeMembers({"a": ["A1"], "b": ["B1"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        speakers.hook = _one_submit_per_turn(orch, "等等")

        turns = await _run(orch, ["a", "b"], "第一句")

        # 插话那一刻还在途的两轮都照样说完、都落库（一个都没被取消）
        assert _speakers_of(turns[0], ["a", "b"]) == {"a", "b"}
        assert turns[0].stopped_for_interjection is True
        assert [t.user_messages for t in turns] == [("第一句",), ("等等",)]

    async def test_a_queued_interjection_wakes_everyone_in_a_fresh_user_turn(self):
        """新用户消息 = K/M 重置 + 唤醒全员：上一轮说过话的人在新轮里照样被起跑。"""
        speakers = FakeMembers({"a": ["A1", "A2"], "b": ["B1", "B2"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        speakers.hook = _one_submit_per_turn(orch, "补一句")

        turns = await _run(orch, ["a", "b"], "第一句")

        assert len(turns) == 2
        # 旧轮：两人各说一条就被插话打断（收束，不再开新思考）
        assert _speakers_of(turns[0], ["a", "b"]) == {"a", "b"}
        # 新轮：全员重新起跑，每人又拿到一轮（额度是新的）
        assert _speakers_of(turns[1], ["a", "b"]) == {"a", "b"}
        assert speakers.rounds["a"] >= 2 and speakers.rounds["b"] >= 2

    async def test_queued_interjections_merge_into_one_new_turn(self):
        speakers = FakeMembers({"a": ["A1", "A2"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        fired = {"done": False}

        def hook(character_id):
            if fired["done"]:
                return
            fired["done"] = True
            assert orch.submit("插话1") is True
            assert orch.submit("插话2") is True

        speakers.hook = hook

        turns = await _run(orch, ["a"], "第一句")

        # 两条插话合并成同一个新轮（不是各开一轮）
        assert [t.user_messages for t in turns] == [("第一句",), ("插话1", "插话2")]

    async def test_each_turn_counts_its_own_budget(self):
        """新用户轮 = 额度重置 + 唤醒全员：上一轮说过话的人，在新轮里照样被起跑。"""
        speakers = FakeMembers({"a": [f"A{i}" for i in range(1, 9)]})
        orch = GroupTurnOrchestrator(speakers.speak)
        speakers.hook = _one_submit_per_turn(orch, "补充")

        turns = await _run(orch, ["a"], "第一句")

        assert [t.user_messages for t in turns] == [("第一句",), ("补充",)]
        # 一轮一条（单人群里"自己的消息不唤醒自己"），但两轮**都开了口** —— 新轮没被旧轮额度拖住
        assert [[u.content for u in t.utterances] for t in turns] == [["A1"], ["A2"]]

    async def test_each_utterance_is_reported_as_soon_as_it_is_decided(self):
        """驱动要能"边说边落库边推送"，不能等整轮跑完才知道说了什么。"""
        reported: list[tuple[str, str]] = []

        async def sink(utterance):
            reported.append((utterance.character_id, utterance.content))

        speakers = FakeMembers({"a": ["A1"], "b": ["B1"]})
        orch = GroupTurnOrchestrator(speakers.speak, on_utterance=sink)

        await _run(orch, ["a", "b"], "在吗")

        assert set(reported) == {("a", "A1"), ("b", "B1")}

    async def test_a_silent_member_reports_nothing(self):
        reported: list[str] = []

        async def sink(utterance):
            reported.append(utterance.content)

        orch = GroupTurnOrchestrator(
            FakeMembers({"a": ["<silent>"], "b": ["B1"]}).speak, on_utterance=sink
        )

        await _run(orch, ["a", "b"], "在吗")

        assert reported == ["B1"]

    async def test_each_turn_is_reported_when_it_ends(self):
        """驱动按**轮**收信号（审查 P3）：插话还没跑的轮说 pending=True。"""
        seen: list[bool] = []

        async def on_turn_end():
            # 回调不带载荷；轮末队列还没被取 → 这里能回答"还有下一轮吗"
            seen.append(orch.has_pending())

        speakers = FakeMembers({"a": ["A1"]})
        orch = GroupTurnOrchestrator(speakers.speak, on_turn_end=on_turn_end)
        speakers.hook = _one_submit_per_turn(orch, "插话")

        turns = await _run(orch, ["a"], "第一句")

        assert [t.user_messages for t in turns] == [("第一句",), ("插话",)]
        assert seen == [True, False]

    async def test_a_queued_interjection_survives_a_failed_turn(self):
        """某个成员生成失败，不该把已经落库的插话一起弄丢 —— 下一次驱动补上。"""
        calls = {"n": 0}

        async def flaky_speak(character_id, *, is_continuation, user_messages):
            calls["n"] += 1
            if calls["n"] == 1:
                orch.submit("第二句")
                raise RuntimeError("LLM 炸了")
            return "回一句"

        orch = GroupTurnOrchestrator(flaky_speak)
        with pytest.raises(RuntimeError):
            await _run(orch, ["a"], "第一句")

        assert orch.has_pending() is True  # 插话还在队列里
        turns = await orch.run(["a"])
        assert turns[0].user_messages == ("第二句",)

    async def test_a_failed_round_does_not_cancel_its_siblings(self):
        """失败也不取消在途：同一轮的其他成员先说完（在途永不取消）。"""
        finished: list[str] = []

        async def speak(character_id, *, is_continuation, user_messages):
            if character_id == "a":
                raise RuntimeError("a 炸了")
            await asyncio.sleep(0)
            finished.append(character_id)
            return "B 说完"

        orch = GroupTurnOrchestrator(speak)
        with pytest.raises(RuntimeError):
            await _run(orch, ["a", "b"], "第一句")

        assert finished == ["b"]

    async def test_submit_while_idle_says_nobody_is_driving(self):
        orch = GroupTurnOrchestrator(FakeMembers().speak)

        assert orch.in_flight is False
        assert orch.submit("你好") is False  # 空闲 → 调用方必须自己 await run()

        turns = await orch.run(["a"])
        assert turns[0].user_messages == ("你好",)

    async def test_submit_during_a_turn_reports_in_flight(self):
        speakers = FakeMembers({"a": ["A1"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        seen = {}
        submit_once = _one_submit_per_turn(orch, "插话")

        def hook(character_id):
            seen["in_flight"] = orch.in_flight
            submit_once(character_id)

        speakers.hook = hook

        turns = await _run(orch, ["a"], "第一句")

        assert seen["in_flight"] is True
        assert [t.user_messages for t in turns] == [("第一句",), ("插话",)]

    async def test_a_second_concurrent_run_is_refused(self):
        """同一实例同时跑两批要报错，而不是悄悄串味（成员之间才是并发的）。"""
        speakers = FakeMembers({"a": ["A1"]})
        started = asyncio.Event()

        async def blocking_speak(character_id, *, is_continuation, user_messages):
            started.set()
            await asyncio.sleep(0.05)
            return await speakers.speak(
                character_id, is_continuation=is_continuation, user_messages=user_messages
            )

        orch = GroupTurnOrchestrator(blocking_speak)
        orch.submit("第一句")
        first = asyncio.create_task(orch.run(["a"]))
        await started.wait()

        with pytest.raises(RuntimeError):
            await orch.run(["a"])

        await first
