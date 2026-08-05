from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models.tool_run import ToolRun

router = APIRouter(prefix="/api/tool-runs", tags=["tool-runs"])


def _run_to_dict(run: ToolRun) -> dict:
    """Serialize a ToolRun ORM object to a JSON-safe dict."""
    import json

    args = None
    if run.arguments:
        try:
            args = json.loads(run.arguments)
        except (json.JSONDecodeError, TypeError):
            args = run.arguments

    return {
        "id": run.id,
        "call_id": run.call_id,
        "tool_name": run.tool_name,
        "arguments": args,
        "result_summary": run.result_summary,
        "elapsed_ms": run.elapsed_ms,
        "success": run.success,
        "error_message": run.error_message,
        "conversation_id": run.conversation_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("")
async def list_tool_runs(
    tool_name: str | None = Query(None, description="Filter by exact tool name"),
    success: str | None = Query(None, description="Filter by success: 'true' or 'false'"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_session),
):
    """List tool-call trace records, newest first.

    Supports optional filtering by *tool_name* and *success* status.
    """
    # Base query
    stmt = select(ToolRun)

    # Apply filters
    if tool_name:
        stmt = stmt.where(ToolRun.tool_name == tool_name)

    if success is not None:
        if success.lower() == "true":
            stmt = stmt.where(ToolRun.success == True)  # noqa: E712
        elif success.lower() == "false":
            stmt = stmt.where(ToolRun.success == False)  # noqa: E712

    # Count total before pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    # Fetch page, newest first
    stmt = stmt.order_by(desc(ToolRun.created_at)).offset(offset).limit(limit)
    runs = (await session.execute(stmt)).scalars().all()

    return {
        "total": total,
        "runs": [_run_to_dict(r) for r in runs],
    }
