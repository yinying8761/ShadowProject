"""Timestamped transcript renderer — the ONE shared transcript formatter.

Feeds BOTH consumers of conversation history:
  1. LLM context assembly (ConversationManager.get_context_messages)
  2. history API + frontend history view (api/conversation.py::get_messages)

(CONTEXT.md \u00a76 "One transcript renderer" \u2014 never write a second copy of
these formatting rules. Spec: docs/specs/group-chat.md Phase 1.)

Pure functions: no DB, no IO, no clock. Message inputs are duck-typed
(role / content / created_at / speaker_id / tool_calls).
"""

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, NamedTuple

#: Speaker shown for user messages when no profile name is available.
DEFAULT_USER_SPEAKER = "\u7528\u6237"  # 用户

#: Last resort for an assistant message whose character cannot be named — a
#: deleted character, or a group message whose `speaker_id` resolves to no
#: member. The spec defines no name for this case; it exists so that a line
#: always renders rather than blowing up. Callers should prefer to pass
#: `character_name` / `speaker_names` so this never shows.
_FALLBACK_AI_SPEAKER = "AI"


class RenderedLine(NamedTuple):
    """One rendered transcript line — speaker and text resolved together.

    Consumers that need both fields (the history API returns `speaker` *and*
    `transcript` per message) take this instead of calling the renderer twice.
    """

    speaker: str
    text: str  # `YYYY/M/D HH:MM [说话人]: 内容`


def ensure_utc(dt: datetime) -> datetime:
    """Naive datetimes in this project's DB are UTC (see `_to_local`)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def render_role_labels(messages: Iterable[Any]) -> str:
    """1:1 内部提示词用的行格式：`[用户]/[角色]: 内容`（工具消息记成 `(tool)`）。

    这是**唯一**一份这个格式的实现：记忆提取与对话摘要都调它。
    （用户看的历史走上面的时间戳渲染器；这份是给 1:1 的内部提示词用的简版。）
    """
    return "\n".join(
        f"[{'用户' if msg.role == 'user' else '角色'}]: {msg.content or '(tool)'}"
        for msg in messages
    )


def _to_local(dt: datetime, tz) -> datetime:
    """Normalize a stored datetime for display.

    Project-wide DB convention: naive datetimes are UTC
    (see PromptManager.format_relative_date, api/chat.py greeting guard).
    Render in the system-local timezone; \u00a7tz overrides for tests.
    """
    return ensure_utc(dt).astimezone(tz)


def format_message_time(dt: datetime, tz=None) -> str:
    """Format a message timestamp as `YYYY/M/D HH:MM` (system-local time).

    Month/day are deliberately NOT zero-padded; hour/minute are.
    Avoids strftime `%-m`/`%-d` \u2014 not supported on Windows.
    """
    local = _to_local(dt, tz)
    return f"{local.year}/{local.month}/{local.day} {local.hour:02d}:{local.minute:02d}"


def _is_transcript_text(msg: Any) -> bool:
    """Only user/assistant TEXT messages carry the prefix.

    Tool-role results and tool-call carriers (assistant messages bearing
    tool_calls) are excluded \u2014 they are plumbing, not conversation.
    """
    if getattr(msg, "role", None) not in ("user", "assistant"):
        return False
    return not getattr(msg, "tool_calls", None)


def resolve_speaker(
    msg: Any,
    *,
    user_name: str | None = None,
    character_name: str | None = None,
    speaker_names: Mapping[str, str] | None = None,
) -> str | None:
    """Speaker name for one message; None for non-transcript messages.

    - user message  \u2192 user_name (profile) or DEFAULT_USER_SPEAKER
    - assistant     \u2192 speaker_id via speaker_names (group) or character_name (1:1)
    """
    if not _is_transcript_text(msg):
        return None
    if msg.role == "user":
        return user_name or DEFAULT_USER_SPEAKER
    speaker_id = getattr(msg, "speaker_id", None)
    if speaker_id and speaker_names and speaker_id in speaker_names:
        return speaker_names[speaker_id]
    return character_name or _FALLBACK_AI_SPEAKER


def render_lines(
    messages: Iterable[Any],
    *,
    user_name: str | None = None,
    character_name: str | None = None,
    speaker_names: Mapping[str, str] | None = None,
    tz=None,
) -> list[RenderedLine | None]:
    """Render each message once as `YYYY/M/D HH:MM [说话人]: 内容`.

    Returns one `RenderedLine` per message, or None for tool-role messages and
    tool-call carriers (alignment preserved) — callers keep that plumbing in
    their own pass-through form (LLM context: raw content; history API:
    transcript=null). Consumers needing both the speaker and the text (the
    history API) take this and never render twice.
    """
    rendered: list[RenderedLine | None] = []
    for msg in messages:
        speaker = resolve_speaker(
            msg,
            user_name=user_name,
            character_name=character_name,
            speaker_names=speaker_names,
        )
        if speaker is None:
            rendered.append(None)
            continue
        ts = format_message_time(msg.created_at, tz=tz)
        rendered.append(RenderedLine(speaker, f"{ts} [{speaker}]: {msg.content}"))
    return rendered


def render_transcript(
    messages: Iterable[Any],
    *,
    user_name: str | None = None,
    character_name: str | None = None,
    speaker_names: Mapping[str, str] | None = None,
    tz=None,
) -> list[str | None]:
    """Text-only projection of `render_lines` — one `str | None` per message.

    For consumers that need just the line (LLM context assembly). The
    formatting rules live in `render_lines`, never here.
    """
    return [
        line.text if line is not None else None
        for line in render_lines(
            messages,
            user_name=user_name,
            character_name=character_name,
            speaker_names=speaker_names,
            tz=tz,
        )
    ]


def render_transcript_text(
    messages: Iterable[Any],
    *,
    user_name: str | None = None,
    character_name: str | None = None,
    speaker_names: Mapping[str, str] | None = None,
    tz=None,
) -> str:
    """`render_transcript` 渲染成**一段文本**，丢掉工具管线留下的空行。

    群摘要（`ConversationManager.summarize_and_trim`）与群补账（`group_memory`）
    共用 ——「渲染 → 丢空行 → join」只此一份。纯函数：不碰 DB / 时钟，
    说话人名字由调用方解析好传进来（`speaker_names`）。
    """
    return "\n".join(
        line
        for line in render_transcript(
            messages,
            user_name=user_name,
            character_name=character_name,
            speaker_names=speaker_names,
            tz=tz,
        )
        if line
    )


#: 对话记录行的行首形状：`2026/9/27 21:19 [小樱]: `（时间戳可缺，只有 `[说话人]: `）。
#: `MULTILINE` + 全局替换 —— 模型可能只在**中间某一行**续写记录（2026-09-28 实测）。
_TRANSCRIPT_PREFIX = re.compile(
    r"^[ \t]*(?:\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}\s*)?\[[^\]\n]{1,32}\][ \t]*[:：][ \t]*",
    re.MULTILINE,
)


def strip_transcript_prefixes(text: str) -> str:
    """剥掉**每一行行首**的对话记录前缀 —— 模型"跟着记录往下写"时会把它带出来。

    喂给 LLM 的上下文是 `时间 [说话人]: 内容` 形式的对话记录（Phase 1），模型会把这行
    记录的**下一行**当成自己的输出。两次实测（都在群聊）：

        # 第一次：整条就是一行，前缀在开头
        2026/9/27 21:19 [小樱]: 话说今天合肥有34度，还挺热的，大家记得多喝水呀～

        # 第二次：自己先说了一段，然后**另起一行**续写记录
        哎呀哎呀，这话题转得～灰暗脸都红了吧，哈哈～阴影你这是想用撒娇逃避运动嘛。
        （空行）
        2026/9/28 20:52 [小樱]: 不过说真的，喜欢宅也没啥大不了的……

    —— 第二种只出现在中间某一行，所以**逐行**剥，而不是只剥整条的开头。前缀是格式、
    不是内容；正文里出现同样形状（不在行首）不动，`[图片]` 这种没有冒号的不碰。
    """
    return _TRANSCRIPT_PREFIX.sub("", text)
