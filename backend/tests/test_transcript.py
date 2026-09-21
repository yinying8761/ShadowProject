"""Timestamped transcript renderer (spec: group-chat Phase 1, ticket #50).

Seam S1 — pure functions, no DB / no IO / no clock:
  - core.transcript.format_message_time: `YYYY/M/D HH:MM` (M/D NOT zero-padded)
  - core.transcript.render_transcript:   `YYYY/M/D HH:MM [说话人]: 内容`

Only user/assistant TEXT messages get the prefix; tool-role messages and
tool-call carriers are skipped (None). Naive datetimes are UTC (project-wide
DB convention, see prompt_manager.format_relative_date) and are rendered in
the system-local timezone; tests pass an explicit fixed-offset `tz` so the
expected wall-clock strings are deterministic on any machine.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.transcript import DEFAULT_USER_SPEAKER, format_message_time, render_transcript

TZ8 = timezone(timedelta(hours=8))  # fixed offset — deterministic everywhere


def _msg(role, content, at, speaker_id=None, tool_calls=None):
    """Minimal message stand-in duck-typed like models.Message."""
    return SimpleNamespace(
        role=role, content=content, created_at=at,
        speaker_id=speaker_id, tool_calls=tool_calls,
    )


class TestFormatMessageTime:
    def test_month_day_not_padded_hour_minute_padded(self):
        """00:05 UTC → 08:05 +8 → '2026/9/5 08:05' (M/D bare, HH:MM padded)."""
        assert format_message_time(datetime(2026, 9, 5, 0, 5), tz=TZ8) == "2026/9/5 08:05"

    def test_naive_is_utc_converted_to_local(self):
        """06:25 naive (UTC) → 14:25 in +8 (system-local convention)."""
        assert format_message_time(datetime(2026, 9, 20, 6, 25), tz=TZ8) == "2026/9/20 14:25"

    def test_cross_day(self):
        """23:59 UTC on 12/31 → 07:59 on 1/1 next year in +8."""
        assert format_message_time(datetime(2026, 12, 31, 23, 59), tz=TZ8) == "2027/1/1 07:59"


class TestRenderTranscript:
    def test_user_message_uses_profile_name(self):
        msgs = [_msg("user", "你好呀", datetime(2026, 9, 20, 6, 25))]
        lines = render_transcript(msgs, user_name="小明", character_name="小柔", tz=TZ8)
        assert lines == ["2026/9/20 14:25 [小明]: 你好呀"]

    def test_user_message_without_name_defaults_to_generic_speaker(self):
        msgs = [_msg("user", "早", datetime(2026, 9, 20, 6, 25))]
        lines = render_transcript(msgs, user_name=None, character_name="小柔", tz=TZ8)
        assert lines == [f"2026/9/20 14:25 [{DEFAULT_USER_SPEAKER}]: 早"]

    def test_assistant_message_uses_character_name(self):
        msgs = [_msg("assistant", "今天天气很好", datetime(2026, 9, 20, 6, 25))]
        lines = render_transcript(msgs, user_name="小明", character_name="小柔", tz=TZ8)
        assert lines == ["2026/9/20 14:25 [小柔]: 今天天气很好"]

    def test_multiple_messages_keep_own_timestamps_across_days(self):
        msgs = [
            _msg("user", "晚安", datetime(2026, 9, 19, 23, 50)),   # UTC 9/19 → 9/20 07:50 +8
            _msg("assistant", "晚安，好梦", datetime(2026, 9, 20, 6, 25)),  # 9/20 14:25 +8
        ]
        lines = render_transcript(msgs, user_name="小明", character_name="小柔", tz=TZ8)
        assert lines == [
            "2026/9/20 07:50 [小明]: 晚安",
            "2026/9/20 14:25 [小柔]: 晚安，好梦",
        ]

    def test_tool_role_message_is_skipped(self):
        msgs = [_msg("tool", '{"status": "ok"}', datetime(2026, 9, 20, 6, 25))]
        lines = render_transcript(msgs, user_name="小明", character_name="小柔", tz=TZ8)
        assert lines == [None]

    def test_assistant_tool_call_carrier_is_skipped(self):
        msgs = [_msg(
            "assistant", "", datetime(2026, 9, 20, 6, 25),
            tool_calls=[{"id": "call_1", "name": "get_current_time", "arguments": {}}],
        )]
        lines = render_transcript(msgs, user_name="小明", character_name="小柔", tz=TZ8)
        assert lines == [None]

    def test_speaker_id_resolves_group_speaker(self):
        """Group-ready: speaker_id picks the speaking character, not the conversation one."""
        msgs = [_msg("assistant", "我不同意", datetime(2026, 9, 20, 6, 25), speaker_id="char-b")]
        lines = render_transcript(
            msgs, user_name="小明", character_name="小柔",
            speaker_names={"char-b": "阿B"}, tz=TZ8,
        )
        assert lines == ["2026/9/20 14:25 [阿B]: 我不同意"]
