"""失败账本（ticket #71 / spec recovery-give-up §a）：纯逻辑，不碰 LLM / DB。

六条路径必须各自成立：连续计数、成功清零、达到阈值停用、停用后允许一个替代、
替代也失败立即停、用户拒绝不计入但工具停用。
"""

from core.failure_ledger import FailureLedger


class TestConsecutiveFailures:
    def test_two_consecutive_failures_disable_the_tool(self):
        ledger = FailureLedger()

        ledger.record_failure("read_file")
        assert not ledger.is_disabled("read_file")
        assert ledger.streaks == {"read_file": 1}

        ledger.record_failure("read_file")
        assert ledger.is_disabled("read_file")
        assert "连续失败 2 次" in ledger.disabled_tools["read_file"]

    def test_one_failure_is_not_enough(self):
        ledger = FailureLedger(limit=2)

        ledger.record_failure("read_file")

        assert ledger.available(["read_file"]) == ["read_file"]

    def test_a_success_resets_the_streak(self):
        ledger = FailureLedger()

        ledger.record_failure("read_file")
        ledger.record_success("read_file")

        assert ledger.streaks == {}
        ledger.record_failure("read_file")
        assert not ledger.is_disabled("read_file")      # 计数从 1 重新开始

    def test_tools_are_counted_independently(self):
        ledger = FailureLedger()

        ledger.record_failure("read_file")
        ledger.record_failure("fetch_url")

        assert ledger.streaks == {"read_file": 1, "fetch_url": 1}
        assert not ledger.is_disabled("read_file")


class TestSubstitution:
    def _disable_read_file(self) -> FailureLedger:
        ledger = FailureLedger()
        ledger.record_failure("read_file")
        ledger.record_failure("read_file")
        assert ledger.is_disabled("read_file")
        return ledger

    def test_the_next_different_tool_becomes_the_substitute(self):
        ledger = self._disable_read_file()

        assert ledger.note_tool_started("fetch_url") is True
        assert ledger.should_stop is False

    def test_the_substitute_failing_stops_the_loop_immediately(self):
        ledger = self._disable_read_file()
        ledger.note_tool_started("fetch_url")

        ledger.record_failure("fetch_url")

        assert ledger.should_stop is True
        assert ledger.is_disabled("fetch_url")
        assert "替补工具也失败" in ledger.disabled_tools["fetch_url"]

    def test_the_substitute_succeeding_resumes_normal_operation(self):
        ledger = self._disable_read_file()
        ledger.note_tool_started("fetch_url")

        ledger.record_success("fetch_url")

        assert ledger.should_stop is False
        ledger.record_failure("fetch_url")       # 之后再失败也只是普通计数
        assert not ledger.should_stop

    def test_only_one_substitute_per_turn(self):
        ledger = self._disable_read_file()
        ledger.note_tool_started("fetch_url")     # 用掉唯一一次替代预算
        ledger.record_success("fetch_url")

        # 第二个工具出问题：不再算"替补"（预算已用完）
        ledger.record_failure("see_screen")
        ledger.record_failure("see_screen")
        assert ledger.is_disabled("see_screen")
        ledger.note_tool_started("research")      # 不再是替补
        ledger.record_failure("research")
        assert ledger.should_stop is False        # 只是普通失败计数

    def test_a_disabled_tool_is_not_started_again(self):
        ledger = self._disable_read_file()

        assert ledger.note_tool_started("read_file") is False
        assert ledger.available(["read_file", "fetch_url"]) == ["fetch_url"]


class TestApprovalDenial:
    def test_a_denied_tool_is_disabled_but_not_counted_as_a_failure(self):
        ledger = FailureLedger()

        ledger.record_denial("write_file")

        assert ledger.is_disabled("write_file")
        assert ledger.streaks == {}               # 用户拒绝不是"工具坏了"
        assert "用户拒绝审批" in ledger.disabled_tools["write_file"]

    def test_a_denial_after_one_failure_clears_the_streak(self):
        ledger = FailureLedger()
        ledger.record_failure("write_file")

        ledger.record_denial("write_file")

        assert ledger.streaks == {}
        assert ledger.is_disabled("write_file")

    def test_a_disabled_tool_cannot_be_counted_further(self):
        ledger = FailureLedger()
        ledger.record_denial("write_file")

        ledger.record_failure("write_file")

        assert ledger.should_stop is False        # 不累加、也不是"替补失败"


class TestQueries:
    def test_describe_lists_the_reasons(self):
        ledger = FailureLedger()
        ledger.record_failure("read_file")
        ledger.record_failure("read_file")
        ledger.record_denial("write_file")

        described = ledger.describe()

        assert "read_file" in described and "连续失败" in described
        assert "write_file" in described and "用户拒绝" in described

    def test_describe_is_empty_without_failures(self):
        assert FailureLedger().describe() == "无失败"

    def test_available_keeps_order(self):
        ledger = FailureLedger()
        ledger.record_denial("b")

        assert ledger.available(["a", "b", "c"]) == ["a", "c"]
