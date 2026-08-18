from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models.llm_usage import LLMUsage

router = APIRouter(prefix="/api/token-usage", tags=["token-usage"])


def _usage_to_dict(u: LLMUsage) -> dict:
    """Serialize an LLMUsage ORM object to a JSON-safe dict."""
    return {
        "id": u.id,
        "conversation_id": u.conversation_id,
        "round_num": u.round_num,
        "model": u.model,
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "total_tokens": u.total_tokens,
        "estimated_prompt_tokens": u.estimated_prompt_tokens,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("")
async def list_token_usage(
    conversation_id: str = Query(..., description="Conversation to aggregate"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_session),
):
    """Per-round token usage for a conversation, newest first.

    ``summary`` aggregates ALL records for the conversation (unaffected by
    pagination); ``usage`` is the requested page, newest round first.
    """
    # Summary over the conversation's full history — no pagination applied.
    total, sum_prompt, sum_completion, sum_total = (
        await session.execute(
            select(
                func.count(LLMUsage.id),
                func.coalesce(func.sum(LLMUsage.prompt_tokens), 0),
                func.coalesce(func.sum(LLMUsage.completion_tokens), 0),
                func.coalesce(func.sum(LLMUsage.total_tokens), 0),
            ).where(LLMUsage.conversation_id == conversation_id)
        )
    ).one()
    summary = {
        "rounds": total,
        "prompt_tokens": sum_prompt,
        "completion_tokens": sum_completion,
        "total_tokens": sum_total,
    }

    stmt = (
        select(LLMUsage)
        .where(LLMUsage.conversation_id == conversation_id)
        # created_at has second granularity in SQLite — round_num breaks ties
        # so two rounds in the same second stay in a deterministic order.
        .order_by(desc(LLMUsage.created_at), desc(LLMUsage.round_num))
        .offset(offset)
        .limit(limit)
    )
    usage = (await session.execute(stmt)).scalars().all()

    return {
        "total": total,
        "usage": [_usage_to_dict(u) for u in usage],
        "summary": summary,
    }
