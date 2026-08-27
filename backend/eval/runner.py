#!/usr/bin/env python3
"""
Agent Eval Runner — automated tool-calling accuracy benchmark.

Usage::

    cd backend && python -m eval.runner

Outputs ``eval_results.md`` in the project root with per-case details
and summary accuracy metrics.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# ── Fix Windows GBK console encoding so emoji don't crash print() ──
# Must happen BEFORE any other imports that might trigger stdout writes.
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Reconfigure stdout/stderr for UTF-8 if they're still using a
    # legacy codec (e.g. GBK on zh-CN Windows).
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name)
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

# Ensure the backend directory is on sys.path so imports resolve
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── Database model imports (must happen before create_all) ──────────
from database import Base  # noqa: E402
from models.character import CharacterProfile  # noqa: E402
from models.conversation import Conversation  # noqa: E402
from models.message import Message  # noqa: E402  # noqa: F401
from models.user_config import UserConfig  # noqa: E402
from models.memory import Memory  # noqa: E402  # noqa: F401

from core.agent import Agent  # noqa: E402
from core.tool_runtime import ToolRuntime  # noqa: E402
from core.tool_registry import ToolRegistry  # noqa: E402
from services.memory_service import memory_service  # noqa: E402

from eval.test_cases import EVAL_CASES, EvalCase  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# Per-case result record
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CaseResult:
    case: EvalCase
    actual_tools: list[str] = field(default_factory=list)
    actual_args: dict[str, dict] = field(default_factory=dict)
    final_response: str = ""
    tool_selection_pass: bool = False
    argument_pass: bool = True   # trivially true when no arg checks defined
    answer_pass: bool = False
    error: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# In-memory SQLite setup
# ═══════════════════════════════════════════════════════════════════════

EVAL_CHAR_ID = "eval-bot-001"


def _create_eval_engine():
    """Return an async engine pointed at an in-memory SQLite database."""
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )


async def _setup_eval_db(engine) -> async_sessionmaker[AsyncSession]:
    """Create all tables in the in-memory DB and seed required rows.

    Returns an ``async_sessionmaker`` bound to *engine*.
    """
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    async with session_factory() as session:
        # ── Test character ─────────────────────────────────────────
        char = CharacterProfile(
            id=EVAL_CHAR_ID,
            name="EvalBot",
            gender=None,
            personality=(
                "You are a helpful AI assistant being evaluated for "
                "tool-calling accuracy. Follow user instructions precisely. "
                "When the user asks you to read/write/list files, USE THE "
                "TOOLS — don't just talk about doing it."
            ),
            role="evaluator",
            archetype="tool",
            voice_style="concise",
        )
        session.add(char)

        # ── UserConfig row (id=1, needed by Agent for location) ────
        config = UserConfig(id=1)
        session.add(config)

        await session.commit()

    return session_factory


# ═══════════════════════════════════════════════════════════════════════
# Memory service stub — avoids FTS5 dependency & production DB pollution
# ═══════════════════════════════════════════════════════════════════════

_original_search = memory_service.search
_original_add_memory = memory_service.add_memory


async def _stub_search(session, query, top_k=3, character_id=None):
    """Return empty list — no memories in eval environment."""
    return []


async def _stub_add_memory(
    session, content, memory_type="user_fact", importance=5,
    source_conversation_id=None, embedding=None, character_id=None,
    source=None,
):
    """Return a fake Memory-like object without touching the disk DB."""
    return SimpleNamespace(
        id="eval-fake-mem-001",
        content=content,
        memory_type=memory_type,
        importance=importance,
    )


def _install_memory_stub():
    memory_service.search = _stub_search      # type: ignore[method-assign]
    memory_service.add_memory = _stub_add_memory  # type: ignore[method-assign]


def _uninstall_memory_stub():
    memory_service.search = _original_search      # type: ignore[method-assign]
    memory_service.add_memory = _original_add_memory  # type: ignore[method-assign]


# ═══════════════════════════════════════════════════════════════════════
# Safe print — prevents Windows GBK encoding crashes
# ═══════════════════════════════════════════════════════════════════════

def _safe_print(*args, **kwargs) -> None:
    """``print()`` that never raises ``UnicodeEncodeError``."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        safe = []
        for a in args:
            s = str(a)
            try:
                s.encode(sys.stdout.encoding or "utf-8")
            except UnicodeEncodeError:
                s = s.encode("ascii", errors="replace").decode("ascii")
            safe.append(s)
        print(*safe, **kwargs)


# ═══════════════════════════════════════════════════════════════════════
# Auto-approve callback
# ═══════════════════════════════════════════════════════════════════════

async def _auto_approve(tool_name: str, arguments: dict) -> bool:
    """Approve every tool call — eval runs headless with no user."""
    return True


# ═══════════════════════════════════════════════════════════════════════
# Evaluation logic
# ═══════════════════════════════════════════════════════════════════════

def _evaluate_case(case: EvalCase, result: CaseResult) -> None:
    """Mutate *result* in-place with pass/fail verdicts."""

    # ── Tool selection ──────────────────────────────────────────────
    if case.expected_tools:
        expected_set = set(case.expected_tools)
        actual_set = set(result.actual_tools)
        # For multi-tool chains we care about the set (order is best-effort
        # because the LLM might batch calls differently).
        result.tool_selection_pass = expected_set == actual_set
    else:
        # Case expects NO tools → agent must call none
        result.tool_selection_pass = len(result.actual_tools) == 0

    # ── Argument accuracy ───────────────────────────────────────────
    if case.expected_args_contain:
        checks: list[bool] = []
        for tool_name, expected_substrs in case.expected_args_contain.items():
            actual_args = result.actual_args.get(tool_name, {})
            # Serialize all arg values to a single searchable string
            args_str = json.dumps(actual_args, ensure_ascii=False)
            for substr in expected_substrs:
                checks.append(substr in args_str)
        result.argument_pass = all(checks)
    else:
        result.argument_pass = True  # no arg checks → trivially pass

    # ── Answer quality ──────────────────────────────────────────────
    text = result.final_response.lower() if result.final_response else ""
    has_contains = (
        bool(case.answer_should_contain)
        or bool(case.answer_should_contain_any)
        or bool(case.answer_should_not_contain)
    )
    if has_contains:
        # AND: every keyword must appear.
        contains_ok = all(
            kw.lower() in text for kw in case.answer_should_contain
        )
        # OR: at least one keyword must appear; empty list = always true.
        contains_any_ok = (not case.answer_should_contain_any) or any(
            kw.lower() in text for kw in case.answer_should_contain_any
        )
        # NOT: every keyword must be absent.
        not_contains_ok = all(
            kw.lower() not in text for kw in case.answer_should_not_contain
        )
        result.answer_pass = contains_ok and contains_any_ok and not_contains_ok
    else:
        # No answer checks defined — pass if we got non-empty response
        result.answer_pass = len(text.strip()) > 0


# ═══════════════════════════════════════════════════════════════════════
# Main runner
# ═══════════════════════════════════════════════════════════════════════

async def run_eval() -> list[CaseResult]:
    """Run all eval cases against the real LLM and return results."""

    engine = _create_eval_engine()
    session_factory = await _setup_eval_db(engine)
    _install_memory_stub()

    # ── Isolated tool registry (wrapped in ToolRuntime, tracing disabled) ─
    inner_registry = ToolRegistry()
    tool_runtime = ToolRuntime(
        registry=inner_registry,
        enable_tracing=False,
        enable_sandbox=False,
    )
    from main import register_tools
    register_tools(tool_runtime)

    results: list[CaseResult] = []

    for case in EVAL_CASES:
        result = CaseResult(case=case)
        _safe_print(f"\n{'='*60}")
        _safe_print(f"[Eval] {case.id}: {case.description}")
        _safe_print(f"[Eval] Query: {case.query[:80]}...")
        _safe_print(f"[Eval] Expected tools: {case.expected_tools or '(none)'}")

        try:
            async with session_factory() as session:
                # ── Fresh conversation per case ─────────────────────
                conv = Conversation(
                    character_id=EVAL_CHAR_ID,
                    title=f"Eval: {case.id}",
                )
                session.add(conv)
                await session.commit()
                await session.refresh(conv)

                # ── Agent ───────────────────────────────────────────
                agent = Agent(tool_registry=tool_runtime)

                tool_use_events: list[dict] = []
                final_text = ""

                async for event in agent.run(
                    session=session,
                    user_message=case.query,
                    conversation_id=conv.id,
                    character_id=EVAL_CHAR_ID,
                    approval_callback=_auto_approve,
                ):
                    if event["type"] == "tool_use":
                        tool_use_events.append(event)
                        result.actual_tools.append(event["name"])
                        result.actual_args[event["name"]] = event.get("arguments", {})
                        _safe_print(f"  [tool_use] {event['name']}({json.dumps(event.get('arguments', {}), ensure_ascii=False)})")
                    elif event["type"] == "token":
                        final_text += event["content"]
                    elif event["type"] == "tool_result":
                        is_err = event.get("is_error", False)
                        marker = " [ERROR]" if is_err else ""
                        preview = str(event.get("result", ""))[:100]
                        _safe_print(f"  [tool_result] {event['name']}{marker}: {preview}")
                    elif event["type"] == "error":
                        result.error = event.get("message", "Unknown error")
                        _safe_print(f"  [ERROR] {result.error}")
                    elif event["type"] == "done":
                        _safe_print(f"  [done] message_id={event.get('message_id', '?')}")
                    elif event["type"] == "memory_updated":
                        _safe_print(f"  [memory_updated] count={event.get('count', 0)}")

                result.final_response = final_text.strip()

                # ── Evaluate (always runs, even if prints crash) ─────
                _evaluate_case(case, result)

                _safe_print(f"  [verdict] tool_sel={result.tool_selection_pass} "
                            f"arg={result.argument_pass} "
                            f"answer={result.answer_pass}")
                if result.final_response:
                    preview = result.final_response[:120].replace("\n", " ")
                    _safe_print(f"  [response] {preview}...")

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            _safe_print(f"  [FATAL] {result.error}")
            # Evaluate whatever we captured before the crash
            if not result.final_response and not result.actual_tools:
                pass  # nothing to evaluate
            else:
                try:
                    _evaluate_case(case, result)
                except Exception as eval_exc:
                    result.error += f" | eval: {eval_exc}"

        results.append(result)

    _uninstall_memory_stub()
    await engine.dispose()

    return results


# ═══════════════════════════════════════════════════════════════════════
# Report generation
# ═══════════════════════════════════════════════════════════════════════

def _check_mark(value: bool) -> str:
    return "✅" if value else "❌"


def _render_keyword_checks(
    lines: list[str], header: str, keywords: list[str], text: str, *, negate: bool = False
) -> None:
    """Append a per-keyword ✓/✗ block to *lines* (no-op when *keywords* empty)."""
    if not keywords:
        return
    lines.append(header)
    for kw in keywords:
        found = kw.lower() in text
        lines.append(f"- `{kw}`: {_check_mark(not found if negate else found)}")
    lines.append("")


def _combined_pass(r: CaseResult) -> bool:
    return r.tool_selection_pass and r.argument_pass and r.answer_pass


def _generate_report(results: list[CaseResult], model: str, started_at: datetime) -> str:
    """Build the full ``eval_results.md`` as a string."""

    total = len(results)
    tool_pass = sum(1 for r in results if r.tool_selection_pass)
    arg_pass = sum(1 for r in results if r.argument_pass)
    answer_pass = sum(1 for r in results if r.answer_pass)
    combined = sum(1 for r in results if _combined_pass(r))

    lines: list[str] = []

    lines.append("# Agent Eval Results\n")
    lines.append(f"**Model**: `{model}`  ")
    lines.append(f"**Date**: {started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}  ")
    lines.append(f"**Cases**: {total}  \n")
    lines.append("---\n")

    # ── Summary ─────────────────────────────────────────────────────
    lines.append("## Summary\n")
    lines.append("| Metric | Pass | Total | Rate |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Tool Selection | {tool_pass} | {total} | "
        f"{tool_pass / total * 100:.1f}% |"
    )
    lines.append(
        f"| Argument Accuracy | {arg_pass} | {total} | "
        f"{arg_pass / total * 100:.1f}% |"
    )
    lines.append(
        f"| Answer Quality | {answer_pass} | {total} | "
        f"{answer_pass / total * 100:.1f}% |"
    )
    lines.append(
        f"| **Combined** | **{combined}** | **{total}** | "
        f"**{combined / total * 100:.1f}%** |"
    )
    lines.append("")

    # ── Results by category ─────────────────────────────────────────
    category_order = [
        "tool_selection", "argument_accuracy", "answer_quality",
        "edge_case", "memory",
    ]
    category_labels = {
        "tool_selection": "Tool Selection",
        "argument_accuracy": "Argument Accuracy",
        "answer_quality": "Answer Quality",
        "edge_case": "Edge Cases",
        "memory": "Memory Operations",
    }

    for cat in category_order:
        cat_results = [r for r in results if r.case.category == cat]
        if not cat_results:
            continue

        lines.append(f"## {category_labels.get(cat, cat)}\n")

        for r in cat_results:
            c = r.case

            lines.append(f"### {c.id} — {c.description}\n")
            lines.append(f"**Query:** {c.query}\n")

            # Expected vs actual tools
            exp_tools = ", ".join(f"`{t}`" for t in c.expected_tools) or "(none)"
            act_tools = ", ".join(f"`{t}`" for t in r.actual_tools) or "(none)"
            lines.append(f"| | Tools |")
            lines.append(f"|---|---|")
            lines.append(f"| Expected | {exp_tools} |")
            lines.append(f"| Actual | {act_tools} |")
            lines.append("")

            # Expected vs actual arguments (only for cases with arg checks)
            if c.expected_args_contain:
                lines.append("**Argument checks:**")
                for tool_name, substrs in c.expected_args_contain.items():
                    actual = r.actual_args.get(tool_name, {})
                    actual_str = json.dumps(actual, ensure_ascii=False)
                    for s in substrs:
                        found = s in actual_str
                        lines.append(
                            f"- `{tool_name}` arg contains `{s}`: "
                            f"{_check_mark(found)}"
                        )
                lines.append("")

            # Answer checks
            text = (r.final_response or "").lower()
            _render_keyword_checks(
                lines, "**Answer checks (should contain):**",
                c.answer_should_contain, text,
            )
            if c.answer_should_contain_any:
                any_ok = any(kw.lower() in text for kw in c.answer_should_contain_any)
                lines.append("**Answer checks (should contain ANY):**")
                lines.append(f"- any keyword present: {_check_mark(any_ok)}")
                for kw in c.answer_should_contain_any:
                    found = kw.lower() in text
                    lines.append(f"- `{kw}`: {_check_mark(found)}")
                lines.append("")
            _render_keyword_checks(
                lines, "**Answer checks (should NOT contain):**",
                c.answer_should_not_contain, text, negate=True,
            )

            # Verdict row
            lines.append(
                f"| Tool Sel | Args | Answer | Combined |"
            )
            lines.append(
                f"|---|---|---|---|"
            )
            lines.append(
                f"| {_check_mark(r.tool_selection_pass)} "
                f"| {_check_mark(r.argument_pass)} "
                f"| {_check_mark(r.answer_pass)} "
                f"| {_check_mark(_combined_pass(r))} |"
            )
            lines.append("")

            # Response preview
            if r.final_response:
                resp = r.final_response[:300]
                lines.append(f"<details>\n<summary>Response (first 300 chars)</summary>\n\n```\n{resp}\n```\n</details>\n")

            if r.error:
                lines.append(f"⚠️ **Error:** `{r.error}`\n")

            lines.append("---\n")

    # ── Failed cases summary ────────────────────────────────────────
    failed = [r for r in results if not _combined_pass(r)]
    if failed:
        lines.append("## Failed Cases\n")
        for r in failed:
            reasons = []
            if not r.tool_selection_pass:
                reasons.append("tool selection")
            if not r.argument_pass:
                reasons.append("argument accuracy")
            if not r.answer_pass:
                reasons.append("answer quality")
            lines.append(
                f"- **{r.case.id}** ({r.case.category}): "
                f"{', '.join(reasons)}"
            )
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════

def main():
    started_at = datetime.now(timezone.utc)

    # ── Resolve model name ──────────────────────────────────────────
    from services.llm_config import runtime_config
    model = runtime_config.get_model() or "unknown"

    print(f"Agent Eval Runner")
    print(f"  Model: {model}")
    print(f"  Started: {started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  Cases: {len(EVAL_CASES)}")

    results = asyncio.run(run_eval())

    # ── Write report ────────────────────────────────────────────────
    report = _generate_report(results, model, started_at)
    output_path = Path(__file__).resolve().parent.parent.parent / "eval_results.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to: {output_path}")

    # ── Quick summary to stdout ─────────────────────────────────────
    total = len(results)
    combined = sum(1 for r in results if _combined_pass(r))
    print(f"\n{'='*60}")
    print(f"RESULTS: {combined}/{total} combined pass ({combined/total*100:.1f}%)")
    tool_p = sum(1 for r in results if r.tool_selection_pass)
    arg_p = sum(1 for r in results if r.argument_pass)
    ans_p = sum(1 for r in results if r.answer_pass)
    print(f"  Tool Selection:  {tool_p}/{total} ({tool_p/total*100:.1f}%)")
    print(f"  Argument Acc:    {arg_p}/{total} ({arg_p/total*100:.1f}%)")
    print(f"  Answer Quality:  {ans_p}/{total} ({ans_p/total*100:.1f}%)")

    failed = [r for r in results if not _combined_pass(r)]
    if failed:
        print(f"\nFailed: {', '.join(r.case.id for r in failed)}")


if __name__ == "__main__":
    main()
