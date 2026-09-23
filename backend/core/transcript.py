"""Timestamped transcript renderer — the ONE shared transcript formatter.

Feeds BOTH consumers of conversation history:
  1. LLM context assembly (ConversationManager.get_context_messages)
  2. history API + frontend history view (api/conversation.py::get_messages)

(CONTEXT.md \u00a76 "One transcript renderer" \u2014 never write a second copy of
these formatting rules. Spec: docs/specs/group-chat.md Phase 1.)

Pure functions: no DB, no IO, no clock. Message inputs are duck-typed
(role / content / created_at / speaker_id / tool_calls).
"""

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


def _to_local(dt: datetime, tz) -> datetime:
    """Normalize a stored datetime for display.

    Project-wide DB convention: naive datetimes are UTC
    (see PromptManager.format_relative_date, api/chat.py greeting guard).
    Render in the system-local timezone; \u00a7tz overrides for tests.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


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
