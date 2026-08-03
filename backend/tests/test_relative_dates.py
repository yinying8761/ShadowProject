"""
Tests for B3: format_relative_date + time-labelled memory/summary injection.
"""

from datetime import datetime, timezone, timedelta

import pytest
from core.prompt_manager import PromptManager


class TestFormatRelativeDate:
    """Pure-function tests for format_relative_date."""

    @pytest.mark.parametrize("hours_ago, expected", [
        (0, "今天"),
        (1, "今天"),
    ])
    def test_today(self, hours_ago, expected):
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        assert PromptManager.format_relative_date(dt) == expected

    def test_yesterday(self):
        dt = datetime.now(timezone.utc) - timedelta(days=1)
        assert PromptManager.format_relative_date(dt) == "昨天"

    @pytest.mark.parametrize("days, expected", [
        (2, "2天前"),
        (3, "3天前"),
        (7, "7天前"),
    ])
    def test_days_ago(self, days, expected):
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        assert PromptManager.format_relative_date(dt) == expected

    @pytest.mark.parametrize("days, expected", [
        (8, "1周前"),
        (14, "2周前"),
        (21, "3周前"),
        (30, "4周前"),
    ])
    def test_weeks_ago(self, days, expected):
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        assert PromptManager.format_relative_date(dt) == expected

    @pytest.mark.parametrize("days, expected", [
        (31, "1个月前"),
        (60, "2个月前"),
        (150, "5个月前"),
    ])
    def test_months_ago(self, days, expected):
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        assert PromptManager.format_relative_date(dt) == expected

    def test_absolute_date_after_6_months(self):
        dt = datetime.now(timezone.utc) - timedelta(days=200)
        result = PromptManager.format_relative_date(dt)
        assert "年" in result
        assert "月" in result
        assert "天前" not in result
        assert "周前" not in result

    def test_boundary_7_days(self):
        """Exactly 7 days = 7天前 (within days range, not weeks)."""
        dt = datetime.now(timezone.utc) - timedelta(days=7)
        assert PromptManager.format_relative_date(dt) == "7天前"

    def test_boundary_30_days(self):
        """30 days = 4周前 (not 1个月前)."""
        dt = datetime.now(timezone.utc) - timedelta(days=30)
        assert PromptManager.format_relative_date(dt) == "4周前"

    def test_boundary_180_days(self):
        """Exactly 180 days triggers absolute date (≥6 months)."""
        dt = datetime.now(timezone.utc) - timedelta(days=180)
        result = PromptManager.format_relative_date(dt)
        # 180 days >= 6 months → absolute date
        assert "年" in result
        assert "月" in result

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime is treated as UTC (correctly handled by tz awareness)."""
        dt = datetime.now(timezone.utc) - timedelta(days=2)  # UTC, clearly 2 days ago
        dt_naive = dt.replace(tzinfo=None)  # strip tz — treated as UTC
        assert PromptManager.format_relative_date(dt_naive) == "2天前"


class TestMemoryInjectionLabels:
    """Time labels in build_system_prompt output."""

    @pytest.fixture
    def pm(self):
        return PromptManager()

    def test_memories_receive_date_labels(self, pm):
        """retrieved_memories with (X天前) prefix appear in prompt."""
        result = pm.build_system_prompt(
            character_name="小樱",
            personality="温柔",
            role="companion",
            archetype="friend",
            retrieved_memories=["(3天前) 用户聊了 Rust", "(1周前) 用户开始健身"],
            conversation_summary="(截至昨天) 用户在学 Rust",
        )
        assert "(3天前) 用户聊了 Rust" in result
        assert "(1周前) 用户开始健身" in result
        assert "(截至昨天) 用户在学 Rust" in result

    def test_empty_labels_still_work(self, pm):
        """Empty memory list and None summary work fine."""
        result = pm.build_system_prompt(
            character_name="小樱",
            personality="温柔",
            role="companion",
            archetype="friend",
            retrieved_memories=[],
            conversation_summary=None,
        )
        # No crash, no memory section
        assert "## Memories About The User" not in result
        assert "## Previous Conversation Summary" not in result
