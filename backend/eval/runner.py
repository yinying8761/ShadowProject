#!/usr/bin/env python3
"""
Agent Eval Runner v2 — behaviour cases against the real model.

Usage::

    cd backend && python -m eval.runner                 # 默认：非 live、非群聊
    python -m eval.runner --only memory,tool_selection  # 只跑这些类别
    python -m eval.runner --live --group                # 打开贵/慢的用例
    python -m eval.runner --baseline eval_baseline.json # 与上次基线对比
    python -m eval.runner --dry-run                     # 只列选中用例，不花钱

核心逻辑（评分 / 报告 / 基线 / 用例选择）都在纯函数模块里，这里只做 I/O：
建库、跑 Agent、读 `llm_usage`、写报告与基线。纯函数由 `tests/test_eval_*.py` 锁住。

Outputs (project root): ``eval_results.md``（路径不变，README 引用它）与
``eval_baseline.json``（逐例通过位，供下次 diff）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# ── Fix Windows GBK console encoding so emoji don't crash print() ──
# Must happen BEFORE any other imports that might trigger stdout writes.
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── Database model imports (must happen before create_all) ──────────
from database import Base  # noqa: E402
from models.character import CharacterProfile  # noqa: E402
from models.conversation import Conversation  # noqa: E402
from models.llm_usage import LLMUsage  # noqa: E402
from models.message import Message  # noqa: E402  # noqa: F401
from models.memory import Memory  # noqa: E402  # noqa: F401
from models.user_config import UserConfig  # noqa: E402

from core.agent import Agent  # noqa: E402
from core.tool_registry import ToolRegistry  # noqa: E402
from core.tool_runtime import ToolRuntime  # noqa: E402
from eval.baseline import (  # noqa: E402
    baseline_payload,
    diff_baselines,
    merge_baselines,
    read_baseline,
    write_baseline,
)
from eval.faults import FaultInjectingRuntime, validate_case  # noqa: E402
from eval.report import CaseReport, render_report  # noqa: E402
from eval.scoring import CaseTrace, score_case  # noqa: E402
from eval.selection import INVARIANT_KIND, select_cases  # noqa: E402
from eval.test_cases import EVAL_CASES, EvalCase  # noqa: E402
from eval.scoring import CaseVerdict  # noqa: E402
from services.memory_service import memory_service  # noqa: E402
from services.usage_store import LLMUsageStore  # noqa: E402

EVAL_CHAR_ID = "eval-bot-001"
_REPORT_NAME = "eval_results.md"
_BASELINE_NAME = "eval_baseline.json"


# ═══════════════════════════════════════════════════════════════════════
# In-memory SQLite setup
# ═══════════════════════════════════════════════════════════════════════

def _create_eval_engine():
    """Return an async engine pointed at an in-memory SQLite database."""
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )


async def _setup_eval_db(engine) -> async_sessionmaker[AsyncSession]:
    """Create all tables in the in-memory DB and seed required rows."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    async with session_factory() as session:
        session.add(
            CharacterProfile(
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
        )
        # UserConfig row (id=1) is read by the Agent for location/weather.
        session.add(UserConfig(id=1))
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
# Safe print + auto-approve
# ═══════════════════════════════════════════════════════════════════════

def _safe_print(*args, **kwargs) -> None:
    """``print()`` that never raises ``UnicodeEncodeError``."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        safe = []
        for arg in args:
            text = str(arg)
            try:
                text.encode(sys.stdout.encoding or "utf-8")
            except UnicodeEncodeError:
                text = text.encode("ascii", errors="replace").decode("ascii")
            safe.append(text)
        print(*safe, **kwargs)


# ═══════════════════════════════════════════════════════════════════════
# Running one case
# ═══════════════════════════════════════════════════════════════════════

async def _run_case(
    case: EvalCase,
    session_factory: async_sessionmaker[AsyncSession],
    registry: ToolRegistry,
    *,
    llm_service=None,
) -> CaseTrace:
    """跑一条用例（单轮或多轮），把它**实际发生了什么**采集出来。

    - 工具故障 / 审批拒绝都由**用例数据**声明（`fail_tools` / `deny_tools`），runner 不
      为某条用例写代码；
    - token / 模型调用数从 `llm_usage` 表读（Agent 每轮落一行），不额外埋点；**即使这一轮
      崩了也照读**，否则失败用例的成本会被低报成 0；
    - `llm_service` 可注入（测试用假模型；生产走 Agent 的默认构造）。
    """
    tool_runtime = FaultInjectingRuntime(
        registry=registry,
        faults=case.fail_tools,
        enable_tracing=False,
        enable_sandbox=False,
    )

    async def approve(tool_name: str, arguments: dict) -> bool:
        """审批：`deny_tools` 里的工具一律拒绝（模拟用户点了"拒绝"）。"""
        return tool_name not in case.deny_tools

    tools: list[str] = []
    args: dict[str, dict] = {}
    calls: list[tuple[str, dict]] = []
    #: `tool_result` 里 `is_error=True` 的工具名（"工具失败了"是 trace 上的事实，
    #: 断言的"是否说明未完成"只认它，不认措辞）；`denied` 是其中用户拒绝的那部分
    failed_tools: list[str] = []
    denied_tools: list[str] = []
    turn_replies: list[str] = []
    error: str | None = None
    conversation_id: str | None = None

    # 视觉用例的固定素材（ticket 09）：只在第一轮挂上图。写错的素材名要像别的坏用例
    # 一样**记成失败用例**，而不是把整轮评测带走。
    fixture_images: list[tuple[bytes, str]] | None = None
    if case.image_fixture:
        from eval.fixtures import fixture

        try:
            fixture_images = [fixture(case.image_fixture)]
        except ValueError as exc:
            error = f"invalid case: {exc}"
            _safe_print(f"  [invalid case] {error}")

    try:
        if error is not None:
            raise RuntimeError(error)
        async with session_factory() as session:
            conv = Conversation(character_id=EVAL_CHAR_ID, title=f"Eval: {case.id}")
            session.add(conv)
            await session.commit()
            await session.refresh(conv)
            conversation_id = conv.id

            # usage 也写进这个内存库（否则会落到真实的 data/companion.db）
            agent_kwargs = {
                "tool_registry": tool_runtime,
                "usage_store": LLMUsageStore(session_factory=session_factory),
            }
            if llm_service is not None:
                agent_kwargs["llm_service"] = llm_service
            agent = Agent(**agent_kwargs)

            for turn_index, turn in enumerate(case.user_turns):
                first_turn = turn_index == 0
                turn_text = ""
                _safe_print(f"  [turn] {turn[:80]}")
                try:
                    async for event in agent.run(
                        session=session,
                        user_message=turn,
                        conversation_id=conv.id,
                        character_id=EVAL_CHAR_ID,
                        approval_callback=approve,
                        # 视觉用例：第一轮把固定素材图挂上去（ticket 09）
                        images=fixture_images if first_turn else None,
                    ):
                        if event["type"] == "tool_use":
                            tools.append(event["name"])
                            args[event["name"]] = event.get("arguments", {})
                            calls.append((event["name"], event.get("arguments", {})))
                            _safe_print(
                                f"  [tool_use] {event['name']}("
                                f"{json.dumps(event.get('arguments', {}), ensure_ascii=False)})"
                            )
                        elif event["type"] == "token":
                            turn_text += event["content"]
                        elif event["type"] == "tool_result":
                            marker = " [ERROR]" if event.get("is_error") else ""
                            preview = str(event.get("result", ""))[:100]
                            _safe_print(f"  [tool_result] {event['name']}{marker}: {preview}")
                            if event.get("is_error"):
                                failed_tools.append(event["name"])
                                if event.get("denied"):
                                    denied_tools.append(event["name"])
                        elif event["type"] == "error":
                            error = event.get("message", "Unknown error")
                            _safe_print(f"  [ERROR] {error}")
                        elif event["type"] == "done":
                            _safe_print(f"  [done] message_id={event.get('message_id', '?')}")
                finally:
                    # 一轮崩了也留下（可能为空的）回复，逐轮数据才与 turns 对齐
                    turn_replies.append(turn_text.strip())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _safe_print(f"  [FATAL] {error}")

    rows = await _usage_rows(session_factory, conversation_id)

    return CaseTrace(
        tools=tuple(tools),
        args=args,
        calls=tuple(calls),
        failed_tools=tuple(failed_tools),
        denied_tools=tuple(denied_tools),
        turn_replies=tuple(turn_replies),
        reply="\n".join(r for r in turn_replies if r),
        model_calls=len(rows),
        tokens=sum(int(r.total_tokens or 0) for r in rows),
        error=error,
    )


async def _usage_rows(session_factory, conversation_id: str | None):
    """读这一条用例的 `llm_usage` 行（读不到就空表，不打断评测）。"""
    if not conversation_id:
        return []
    try:
        async with session_factory() as session:
            return list(
                (
                    await session.execute(
                        select(LLMUsage).where(
                            LLMUsage.conversation_id == conversation_id
                        )
                    )
                ).scalars().all()
            )
    except Exception as exc:
        _safe_print(f"  [warn] 读用量失败：{type(exc).__name__}: {exc}")
        return []


async def _run_invariant_case(case: EvalCase) -> CaseReport:
    """群聊不变量用例：脚本化假模型 + 临时文件库（隔离，且能真正并发）。

    零 token、零网络；断言的是调度语义（ADR-0009），不是文本。
    """
    from eval.group_turn_eval import SCENARIOS, check_invariants, run_scenario

    scenario = SCENARIOS.get(case.scenario)
    if scenario is None:
        invalid = CaseTrace(error=f"unknown scenario {case.scenario!r}")
        return CaseReport(
            case=case, trace=invalid, verdict=CaseVerdict(checks={"scenario": False})
        )
    if list(case.user_turns) != list(scenario.user_messages):
        invalid = CaseTrace(
            error=(
                f"case/turns 与场景不一致：{list(case.user_turns)} != "
                f"{list(scenario.user_messages)}"
            )
        )
        return CaseReport(
            case=case, trace=invalid, verdict=CaseVerdict(checks={"scenario": False})
        )

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp) / 'eval-group.db'}")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            observation = await run_scenario(scenario, factory)
        except Exception as exc:      # 场景自身崩了：报成失败用例，不带走整轮评测
            _safe_print(f"  [FATAL] scenario failed: {type(exc).__name__}: {exc}")
            crash = CaseTrace(error=f"{type(exc).__name__}: {exc}")
            return CaseReport(
                case=case, trace=crash, verdict=CaseVerdict(checks={"run": False})
            )
        finally:
            await engine.dispose()

    try:
        checks, messages = check_invariants(case.invariants, observation)
    except ValueError as exc:      # 用例里写了不认识的不变量名：报成失败，不是崩掉整轮
        _safe_print(f"  [invalid case] {exc}")
        invalid = CaseTrace(error=f"invalid case: {exc}")
        return CaseReport(
            case=case, trace=invalid, verdict=CaseVerdict(checks={"scenario": False})
        )

    for message in messages:
        _safe_print(f"  [invariant] {message}")
    _safe_print(
        f"  [observation] rounds={len(observation.rounds)} "
        f"persisted={len(observation.persisted)} events={len(observation.events)}"
    )
    trace = CaseTrace(
        reply=(
            f"{len(observation.rounds)} 次思考 / "
            f"{len([r for r in observation.persisted if r.role == 'assistant'])} 条落库消息"
        ),
        model_calls=0,      # 不变量用例零模型调用
        tokens=0,
        # 失败原因进报告（报告会把它渲染成 Error 那一行）
        error="; ".join(messages) if messages else None,
    )
    return CaseReport(case=case, trace=trace, verdict=CaseVerdict(checks=checks))


async def run_eval(cases: list[EvalCase], *, llm_service=None) -> list[CaseReport]:
    """Run *cases* against the model and return scored results."""
    engine = _create_eval_engine()
    reports: list[CaseReport] = []
    try:
        session_factory = await _setup_eval_db(engine)
        _install_memory_stub()

        # 一个注册表 + 每条用例一个运行时（故障注入按用例数据走）
        registry = ToolRegistry()
        from main import register_tools
        register_tools(ToolRuntime(registry=registry))

        for case in cases:
            _safe_print(f"\n{'=' * 60}")
            _safe_print(f"[Eval] {case.id}: {case.description}")
            if getattr(case, "kind", "") == INVARIANT_KIND:
                _safe_print(f"[Eval] Scenario: {case.scenario}  Invariants: {case.invariants}")
                reports.append(await _run_invariant_case(case))
                continue
            _safe_print(
                f"[Eval] Turns: {len(case.user_turns)}  Category: {case.category}"
                f"  faults={case.fail_tools or '-'}  deny={case.deny_tools or '-'}"
            )
            try:
                # 用例的故障声明必须与真实工具表对得上；对不上就报成失败用例
                # （而不是静默失效、也不是把整轮评测带走）
                validate_case(case, registry)
            except ValueError as exc:
                _safe_print(f"  [invalid case] {exc}")
                invalid = CaseTrace(error=f"invalid case: {exc}")
                reports.append(
                    CaseReport(
                        case=case, trace=invalid, verdict=score_case(case, invalid)
                    )
                )
                continue
            trace = await _run_case(
                case, session_factory, registry, llm_service=llm_service
            )
            verdict = score_case(case, trace)
            label = {"pass": "PASS", "fail": "FAIL", "needs_review": "NEEDS REVIEW"}[
                verdict.status
            ]
            detail = (
                " (" + ", ".join(verdict.reasons) + ")"
                if verdict.status == "fail"
                else ""
            )
            _safe_print(
                f"  [verdict] {label}{detail}"
                f"  calls={trace.model_calls} tokens={trace.tokens}"
            )
            reports.append(CaseReport(case=case, trace=trace, verdict=verdict))
    finally:
        _uninstall_memory_stub()
        await engine.dispose()

    return reports


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m eval.runner",
        description="Agent behaviour eval (real model). Pure core: eval.scoring/report/baseline/selection.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="CATEGORY[,CATEGORY]",
        help="只跑这些类别（可重复，也可用逗号分隔）。默认全跑。",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="包含 live 用例（真外网 / 真截图 / 真搜索；默认不跑）。也可用环境变量 EVAL_LIVE=1。",
    )
    parser.add_argument(
        "--group",
        action="store_true",
        help="包含群聊不变量用例（脚本化假模型，ticket 08 接入）。",
    )
    parser.add_argument(
        "--baseline",
        default="",
        metavar="PATH",
        help="与上次基线对比（默认读取项目根的 eval_baseline.json）。",
    )
    parser.add_argument(
        "--out",
        default="",
        metavar="PATH",
        help="报告输出路径（默认项目根的 eval_results.md）。",
    )
    parser.add_argument(
        "--baseline-out",
        default="",
        metavar="PATH",
        help="本次基线写入路径（默认项目根的 eval_baseline.json）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印这次会跑哪些用例，不建库、不调模型、不写文件。",
    )
    return parser.parse_args(argv)


def _categories(chunks: list[str]) -> list[str]:
    categories: list[str] = []
    for chunk in chunks:
        for part in chunk.split(","):
            part = part.strip()
            if part and part not in categories:
                categories.append(part)
    return categories


_LIVE_VALUES = {"1", "true", "yes", "on"}


def live_enabled(args: argparse.Namespace) -> bool:
    """要不要跑 live 用例：`--live`，或环境变量 `EVAL_LIVE` 取 1/true/yes/on。

    与 MCP live 测试同一约定（环境变量开关），但**只认真值**：`EVAL_LIVE=0` 不算打开 ——
    "显式开关"应该显式。
    """
    if args.live:
        return True
    return os.environ.get("EVAL_LIVE", "").strip().lower() in _LIVE_VALUES


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started_at = datetime.now(timezone.utc)
    root = Path(__file__).resolve().parent.parent.parent

    include_live = live_enabled(args)
    categories = _categories(args.only)
    cases = select_cases(
        EVAL_CASES,
        categories=categories,
        include_live=include_live,
        include_group=args.group,
    )

    if not cases:
        _safe_print(
            f"没有匹配的用例（categories={categories or '全部'}, "
            f"live={include_live}, group={args.group}）"
        )
        return 2

    _safe_print("Agent Eval Runner v2")
    _safe_print(f"  Cases: {len(cases)} / {len(EVAL_CASES)}")
    for case in cases:
        _safe_print(f"    - {case.id} [{case.category}/{case.kind}] live={case.live}")
    if not include_live:
        _safe_print("  (live 用例已跳过；--live 或 EVAL_LIVE=1 打开)")
    if not args.group:
        _safe_print("  (群聊不变量用例已跳过；--group 打开)")

    if args.dry_run:
        _safe_print("  --dry-run：不建库、不调模型、不写文件。")
        return 0

    # ── Resolve model name (load data/config.yaml like main.py does) ──
    from services.llm_config import config_store, runtime_config

    runtime_config.copy_from(config_store.load())
    model = runtime_config.get_model() or "unknown"
    _safe_print(f"  Model: {model}")
    _safe_print(f"  Started: {started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    reports = asyncio.run(run_eval(cases))

    payload = baseline_payload(
        reports, model=model, generated_at=started_at.isoformat(),
    )

    # 基线 diff 用来对比；写入时走**合并**（见下），所以部分运行不会误删用例
    baseline_diff = None
    baseline_path = Path(args.baseline) if args.baseline else root / _BASELINE_NAME
    if args.baseline and not baseline_path.exists():
        _safe_print(f"  [error] 基线文件不存在：{baseline_path}")
        return 2
    if baseline_path.exists():
        try:
            baseline_diff = diff_baselines(read_baseline(baseline_path), payload)
        except (ValueError, json.JSONDecodeError) as exc:
            _safe_print(f"  [error] 基线无法对比：{exc}")
            if args.baseline:
                return 2

    report = render_report(
        reports,
        model=model,
        started_at=started_at,
        baseline_diff=baseline_diff,
        skipped=[],
        note=None if include_live else "live 用例未包含（默认不跑贵用例）",
    )

    output_path = Path(args.out) if args.out else root / _REPORT_NAME
    output_path.write_text(report, encoding="utf-8")
    _safe_print(f"\nReport written to: {output_path}")

    # 基线**合并**写入：本次跑到的用例覆盖，没跑到的保留 —— 这样 `--only/--live/--group`
    # 这类部分运行也安全（不会把没跑的用例当成"已删除"，live 结果也能积累）。
    baseline_out = Path(args.baseline_out) if args.baseline_out else root / _BASELINE_NAME
    merged = payload
    if baseline_out.exists():
        try:
            merged = merge_baselines(read_baseline(baseline_out), payload)
        except (ValueError, json.JSONDecodeError) as exc:
            _safe_print(f"  [warn] 旧基线无法合并，改为覆盖：{exc}")
    write_baseline(baseline_out, merged)
    _safe_print(
        f"Baseline written to: {baseline_out}"
        f"（{merged['summary']['passed']}/{merged['summary']['total']} 条通过位；"
        "needs_review 记为未失败，不进报告的通过率分母）"
    )

    total = sum(1 for r in reports if r.verdict.counted)
    passed = sum(1 for r in reports if r.verdict.status == "pass")
    review = [r.case.id for r in reports if r.verdict.status == "needs_review"]
    _safe_print(f"\n{'=' * 60}")
    _safe_print(f"RESULTS: {passed}/{total} pass ({passed / total * 100 if total else 0:.1f}%)")
    if review:
        _safe_print(
            f"  Needs review (not in the pass-rate denominator): {', '.join(review)}"
        )
    _safe_print(
        f"  Model calls: {payload['summary']['model_calls']}  "
        f"Tokens: {payload['summary']['tokens']}"
    )
    if baseline_diff is not None:
        _safe_print(
            f"  Baseline: new fail={list(baseline_diff.new_fail)} "
            f"new pass={list(baseline_diff.new_pass)} "
            f"still fail={list(baseline_diff.still_fail)}"
        )
        if baseline_diff.has_regressions:
            _safe_print("  ⚠️ 有回归（新挂用例）")

    failed = [r.case.id for r in reports if r.verdict.status == "fail"]
    if failed:
        _safe_print(f"  Failed: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
