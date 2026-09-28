"""Timestamped transcript renderer (spec: group-chat Phase 1, ticket #50).

Seam S1 — pure functions, no DB / no IO / no clock:
  - core.transcript.format_message_time: `YYYY/M/D HH:MM` (M/D NOT zero-padded)
  - core.transcript.render_transcript:   `YYYY/M/D HH:MM [说话人]: 内容`
  - core.transcript.render_transcript_text: 上面那些行 → 一段文本（丢空行、换行 join）
    —— 群摘要与群补账共用，别再各写一遍 join

Only user/assistant TEXT messages get the prefix; tool-role messages and
tool-call carriers are skipped (None). Naive datetimes are UTC (project-wide
DB convention, see prompt_manager.format_relative_date) and are rendered in
the system-local timezone; tests pass an explicit fixed-offset `tz` so the
expected wall-clock strings are deterministic on any machine.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.transcript import (
    DEFAULT_USER_SPEAKER,
    format_message_time,
    render_transcript,
    render_transcript_text,
    strip_transcript_prefixes,
)

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


class TestRenderTranscriptText:
    """`render_transcript_text`：渲染 → 丢工具管线的空行 → join（只此一份组装）。"""

    def test_joins_text_lines_and_drops_tool_plumbing(self):
        msgs = [
            _msg("user", "早", datetime(2026, 9, 20, 6, 25)),
            _msg("tool", '{"ok": true}', datetime(2026, 9, 20, 6, 26), tool_calls=None),
            _msg("assistant", "早呀", datetime(2026, 9, 20, 6, 27), speaker_id="char-b"),
        ]
        text = render_transcript_text(
            msgs, user_name="小明", speaker_names={"char-b": "阿B"}, tz=TZ8,
        )
        assert text == (
            "2026/9/20 14:25 [小明]: 早\n"
            "2026/9/20 14:27 [阿B]: 早呀"
        )

    def test_only_tool_plumbing_renders_empty_text(self):
        msgs = [_msg("tool", '{"ok": true}', datetime(2026, 9, 20, 6, 25))]
        assert render_transcript_text(msgs, user_name="小明", tz=TZ8) == ""


class TestStripTranscriptPrefixes:
    """模型"跟着记录往下写"时带出来的行首前缀要剥掉（2026-09-27 / 09-28 两次群聊实测）。"""

    def test_strips_the_whole_line_case(self):
        """第一次（09-27）：整条就是一行记录，前缀在开头。"""
        leaked = "2026/9/27 21:19 [小樱]: 话说今天合肥有34度，还挺热的，大家记得多喝水呀～"
        assert strip_transcript_prefixes(leaked) == (
            "话说今天合肥有34度，还挺热的，大家记得多喝水呀～"
        )

    def test_strips_a_prefix_that_appears_mid_message(self):
        """第二次（09-28）：先说了一段，又另起一行续写记录 —— 只剥开头那种写法兜不住。"""
        leaked = (
            "哎呀哎呀，这话题转得～灰暗脸都红了吧，哈哈～阴影你这是想用撒娇逃避运动嘛。\n"
            "\n"
            "2026/9/28 20:52 [小樱]: 不过说真的，喜欢宅也没啥大不了的，"
            "找点在家也能动一动的乐趣，比被念叨强多啦～"
        )
        assert strip_transcript_prefixes(leaked) == (
            "哎呀哎呀，这话题转得～灰暗脸都红了吧，哈哈～阴影你这是想用撒娇逃避运动嘛。\n"
            "\n"
            "不过说真的，喜欢宅也没啥大不了的，找点在家也能动一动的乐趣，比被念叨强多啦～"
        )

    def test_strips_every_lines_prefix(self):
        """一整个假对话段（多行）也要全剥掉。"""
        leaked = "2026/9/27 21:19 [小樱]: 第一句\n2026/9/27 21:20 [灰暗]: 第二句"
        assert strip_transcript_prefixes(leaked) == "第一句\n第二句"

    def test_strips_a_bare_speaker_prefix(self):
        assert strip_transcript_prefixes("[小柔]: 在的") == "在的"

    def test_leaves_normal_text_alone(self):
        for text in (
            "今天好热呀（叹气）[笑]",
            "我说的是 2026/9/27 21:19 [小樱]: 这种格式",  # 不在行首 → 不动
            "[图片]",
            "",
        ):
            assert strip_transcript_prefixes(text) == text
