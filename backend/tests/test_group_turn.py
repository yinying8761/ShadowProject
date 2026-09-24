"""群轮编排器（ticket #53）：顺序 / 跳过 / 链预算 / 插话新轮 —— 全用假对象测。

Seam: `core/group_turn.GroupTurnOrchestrator`（spec S3）。注入"跑一个角色的一轮"的
`speak` 可调用对象；编排器不碰 DB / 网络 / 时钟，所以这里能直接测出
「谁在什么时候说、说几条、什么时候收束」的全部语义。

用户消息只有一个入口：`submit()`（同步入队）+ `run()`（跑完队列）。
"""

import asyncio

import pytest

from core.group_turn import GROUP_CHAIN_BUDGET, GroupTurnOrchestrator, is_silent


class FakeSpeakers:
    """按脚本回答的假说话人。

    - `script`: 每个角色的回复列表，按顺序消费；用完一律 `<silent>`。
    - `hook`: 每次 speak 时同步回调（用来模拟"正在生成时用户又发消息"）。
    - 并发保护：上一个角色这一轮没结束就轮到别人 → 直接断言失败（串行契约）。
    """

    def __init__(self, script: dict[str, list[str | None]] | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, bool, tuple[str, ...]]] = []
        self.hook = None
        self._current: str | None = None

    async def speak(self, character_id, *, is_continuation, user_messages):
        assert self._current is None, f"并发：{self._current} 尚未收尾就轮到 {character_id}"
        self._current = character_id
        try:
            self.calls.append((character_id, is_continuation, tuple(user_messages)))
            if self.hook:
                self.hook(character_id, is_continuation)
            # 让出事件循环：真有并发就会在这里被上面的断言抓到
            await asyncio.sleep(0)
            replies = self.script.get(character_id, [])
            return replies.pop(0) if replies else "<silent>"
        finally:
            self._current = None

    def calls_of(self, character_id: str) -> list[tuple[bool, tuple[str, ...]]]:
        return [(c, u) for cid, c, u in self.calls if cid == character_id]


async def _run(orch: GroupTurnOrchestrator, members, *user_messages) -> list:
    """提交若干条用户消息，然后把队列跑完（= WS 侧一个用户消息批次一个任务）。"""
    for text in user_messages:
        orch.submit(text)
    return await orch.run(list(members))


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


class TestOrderAndSkip:
    async def test_members_speak_one_after_another_in_member_order(self):
        speakers = FakeSpeakers({"a": ["A 说"], "b": ["B 说"]})

        turns = await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗")

        assert [u.character_id for u in turns[0].utterances] == ["a", "b"]
        assert [u.content for u in turns[0].utterances] == ["A 说", "B 说"]

    async def test_a_silent_member_is_dropped_without_an_utterance(self):
        speakers = FakeSpeakers({"a": ["<silent>"], "b": ["B 说"]})

        turns = await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗")

        assert [u.character_id for u in turns[0].utterances] == ["b"]
        assert speakers.calls_of("a")[0][0] is False  # 先问一次"要不要说"

    async def test_everyone_silent_ends_the_turn_with_nothing(self):
        turn = (await _run(GroupTurnOrchestrator(FakeSpeakers().speak), ["a", "b"], "在吗"))[0]

        assert turn.utterances == ()
        assert turn.stopped_for_interjection is False
        assert turn.budget_exhausted is False

    async def test_the_turn_carries_the_user_messages_it_was_anchored_on(self):
        speakers = FakeSpeakers({"a": ["A 说"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a"], "第一句", "第二句"))[0]

        assert turn.user_messages == ("第一句", "第二句")


class TestContinuationAndBudget:
    """「继续说」与链预算：一个用户轮的角色消息总量封顶。"""

    async def test_a_member_may_say_more_than_one_message(self):
        speakers = FakeSpeakers({"a": ["第一句", "第二句", "<silent>"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a"], "在吗"))[0]

        assert [u.content for u in turn.utterances] == ["第一句", "第二句"]
        # 第二次开始都是"接着说"的问法；得到 <silent> 就收住
        assert [cont for cont, _ in speakers.calls_of("a")] == [False, True, True]

    async def test_the_chain_budget_caps_total_messages_in_a_turn(self):
        speakers = FakeSpeakers({"a": [f"A{i}" for i in range(1, 10)], "b": ["B1"]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗"))[0]

        assert len(turn.utterances) == GROUP_CHAIN_BUDGET
        assert turn.budget_exhausted is True
        # 到顶即整群静默：后面的成员不再被打扰
        assert speakers.calls_of("b") == []

    async def test_the_budget_is_a_knob_not_a_magic_number(self):
        speakers = FakeSpeakers({"a": ["一", "二", "三"]})

        orch = GroupTurnOrchestrator(speakers.speak, chain_budget=2)
        turn = (await _run(orch, ["a"], "在吗"))[0]

        assert [u.content for u in turn.utterances] == ["一", "二"]
        assert turn.budget_exhausted is True

    async def test_a_skip_does_not_eat_the_budget(self):
        speakers = FakeSpeakers({"a": ["<silent>"], "b": [f"B{i}" for i in range(1, 8)]})

        turn = (await _run(GroupTurnOrchestrator(speakers.speak), ["a", "b"], "在吗"))[0]

        assert turn.utterances[0].character_id == "b"
        assert len(turn.utterances) == GROUP_CHAIN_BUDGET


def _interject_during(orch, text, *, character_id=None, nth=1):
    """模拟"角色还在生成时用户又发了一句"。

    在指定角色的第 nth 次「接着说」调用期间（= LLM 流在途）提交新消息，
    并断言在途状态确实被接受。
    """
    state = {"fired": 0}

    def hook(cid, is_continuation):
        if character_id is not None and cid != character_id:
            return
        if not is_continuation:
            return
        state["fired"] += 1
        if state["fired"] == nth:
            assert orch.submit(text) is True, "在途时插话必须被接受（排队，不打断）"

    return hook


def _interject_during_first_reply(orch, text, character_id="a"):
    """模拟"某个角色**第一句**还在生成时"用户又发了一句（只在第一轮触发一次）。"""
    fired = {"done": False}

    def hook(cid, is_continuation):
        if fired["done"] or cid != character_id or is_continuation:
            return
        fired["done"] = True
        assert orch.submit(text) is True, "在途时插话必须被接受（排队，不打断）"

    return hook


class TestInterjection:
    """插话：在途回复不被打断；当前这条流收尾后旧轮收束，插话合并成新轮（预算重置）。"""

    async def test_the_in_flight_reply_finishes_and_the_turn_then_winds_down(self):
        speakers = FakeSpeakers({"a": ["A1", "A2", "A3"], "b": ["B1"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        speakers.hook = _interject_during(orch, "等等，我还有一句")

        turns = await _run(orch, ["a", "b"], "第一句")

        assert len(turns) == 2
        # 旧轮：a 在途那条照样说完（不打断），收尾后立即收束 —— b 不再就旧消息发言
        assert [u.content for u in turns[0].utterances] == ["A1", "A2"]
        assert turns[0].stopped_for_interjection is True
        assert all(
            user_messages == ("等等，我还有一句",)
            for _, user_messages in speakers.calls_of("b")
        )

    async def test_an_interjection_while_the_first_reply_is_in_flight_winds_down(self):
        """插话落在**第一句**在途时：这一句说完就收束 —— 不续说，也不轮到别人。"""
        speakers = FakeSpeakers({"a": ["A1", "A2"], "b": ["B1"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        speakers.hook = _interject_during_first_reply(orch, "等等")

        turns = await _run(orch, ["a", "b"], "第一句")

        assert [u.content for u in turns[0].utterances] == ["A1"]
        # 旧轮里只问了 a 一次（第一句），没问"要不要接着说"
        assert [c for c, msgs in speakers.calls_of("a") if msgs == ("第一句",)] == [False]
        # b 只在**新轮**里被问到，旧轮里连问都没问
        assert all(user_messages == ("等等",) for _, user_messages in speakers.calls_of("b"))
        assert [t.user_messages for t in turns] == [("第一句",), ("等等",)]

    async def test_queued_interjections_merge_into_one_new_turn(self):
        speakers = FakeSpeakers({"a": ["A1", "A2", "A3"], "b": ["B1"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        fired = {"done": False}

        def hook(cid, is_continuation):
            # 趁 a 还在生成（在途流）时用户连发两句
            if fired["done"] or not (cid == "a" and is_continuation):
                return
            fired["done"] = True
            assert orch.submit("插话1") is True
            assert orch.submit("插话2") is True

        speakers.hook = hook

        turns = await _run(orch, ["a", "b"], "第一句")

        # 两条插话合并成同一个新轮（不是各开一轮）
        assert [t.user_messages for t in turns] == [("第一句",), ("插话1", "插话2")]
        # 旧轮：a 只说完在途那条就收束
        assert [u.content for u in turns[0].utterances] == ["A1", "A2"]
        # 新轮：a 接着说 A3，b 也发言 —— 预算是新的
        assert [u.character_id for u in turns[1].utterances] == ["a", "b"]

    async def test_each_turn_counts_its_own_budget(self):
        speakers = FakeSpeakers({"a": [f"A{i}" for i in range(1, 20)]})
        orch = GroupTurnOrchestrator(speakers.speak)

        def hook(cid, is_continuation):
            # 第一轮说到第 6 条（预算到顶）的那一刻，用户插话
            if is_continuation and len(speakers.calls) == GROUP_CHAIN_BUDGET:
                assert orch.submit("补充") is True

        speakers.hook = hook

        turns = await _run(orch, ["a"], "第一句")

        assert len(turns) == 2
        assert len(turns[0].utterances) == GROUP_CHAIN_BUDGET
        assert turns[0].budget_exhausted is True
        # 新轮预算重置：同一个角色又能说满 6 条
        assert turns[1].user_messages == ("补充",)
        assert len(turns[1].utterances) == GROUP_CHAIN_BUDGET

    async def test_each_utterance_is_reported_as_soon_as_it_is_decided(self):
        """驱动要能"边说边落库边推送"，不能等整轮跑完才知道说了什么。"""
        reported: list[tuple[str, str]] = []

        async def sink(utterance):
            reported.append((utterance.character_id, utterance.content))

        speakers = FakeSpeakers({"a": ["A1", "A2"], "b": ["B1"]})
        orch = GroupTurnOrchestrator(speakers.speak, on_utterance=sink)

        turns = await _run(orch, ["a", "b"], "在吗")

        assert reported == [("a", "A1"), ("a", "A2"), ("b", "B1")]
        assert [u.content for u in turns[0].utterances] == ["A1", "A2", "B1"]

    async def test_a_silent_member_reports_nothing(self):
        reported: list[str] = []

        async def sink(utterance):
            reported.append(utterance.content)

        orch = GroupTurnOrchestrator(FakeSpeakers({"a": ["<silent>"], "b": ["B1"]}).speak, on_utterance=sink)

        await _run(orch, ["a", "b"], "在吗")

        assert reported == ["B1"]

    async def test_a_queued_interjection_survives_a_failed_turn(self):
        """某个角色生成失败，不该把已经落库的插话一起弄丢 —— 下一次驱动补上。"""
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

    async def test_submit_while_idle_says_nobody_is_driving(self):
        orch = GroupTurnOrchestrator(FakeSpeakers().speak)

        assert orch.in_flight is False
        assert orch.submit("你好") is False  # 空闲 → 调用方必须自己 await run()

        turns = await orch.run(["a"])
        assert turns[0].user_messages == ("你好",)

    async def test_submit_during_a_turn_reports_in_flight(self):
        speakers = FakeSpeakers({"a": ["A1", "A2"]})
        orch = GroupTurnOrchestrator(speakers.speak)
        seen = {}

        def hook(cid, is_continuation):
            if is_continuation and not seen:
                seen["in_flight"] = orch.in_flight
                assert orch.submit("插话") is True

        speakers.hook = hook

        turns = await _run(orch, ["a"], "第一句")

        assert seen["in_flight"] is True
        assert [t.user_messages for t in turns] == [("第一句",), ("插话",)]

    async def test_a_second_concurrent_run_is_refused(self):
        """永不并行：同一实例同时跑两轮要报错，而不是悄悄串味。"""
        speakers = FakeSpeakers({"a": ["A1"]})
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
