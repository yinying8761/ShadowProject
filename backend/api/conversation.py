from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models.conversation import Conversation
from models.message import Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    character_id: str
    title: str = "New Conversation"


@router.get("")
async def list_conversations(
    character_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    query = select(Conversation).order_by(Conversation.updated_at.desc())
    if character_id:
        query = query.where(Conversation.character_id == character_id)

    result = await session.execute(query)
    convs = result.scalars().all()
    return [
        {
            "id": c.id,
            "character_id": c.character_id,
            "title": c.title,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in convs
    ]


@router.post("")
async def create_conversation(
    data: ConversationCreate,
    session: AsyncSession = Depends(get_session),
):
    conv = Conversation(character_id=data.character_id, title=data.title)
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return {"id": conv.id, "character_id": conv.character_id, "title": conv.title}


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session)
):
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "id": conv.id,
        "character_id": conv.character_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    query = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(query)
    messages = result.scalars().all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "tool_calls": m.tool_calls,
            "token_count": m.token_count,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.delete("/{conversation_id}/messages/{message_id}")
async def delete_message(
    conversation_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_session),
):
    msg = await session.get(Message, message_id)
    if not msg or msg.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    await session.delete(msg)
    await session.commit()
    return {"status": "deleted"}


@router.delete("/{conversation_id}/messages")
async def clear_messages(
    conversation_id: str, session: AsyncSession = Depends(get_session)
):
    """Delete all messages in a conversation + related memories."""
    from sqlalchemy import delete
    from models.memory import Memory

    # Delete messages
    result = await session.execute(
        delete(Message).where(Message.conversation_id == conversation_id)
    )
    msg_count = result.rowcount

    # Delete related memories
    mem_result = await session.execute(
        delete(Memory).where(Memory.source_conversation_id == conversation_id)
    )
    mem_count = mem_result.rowcount

    await session.commit()
    return {"status": "cleared", "messages_deleted": msg_count, "memories_deleted": mem_count}


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session)
):
    """Delete a conversation and its messages (memories persist per character)."""
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await session.delete(conv)
    await session.commit()
    return {"status": "deleted"}


@router.post("/{conversation_id}/compact")
async def compact_conversation(
    conversation_id: str,
    keep_count: int = 12,
    session: AsyncSession = Depends(get_session),
):
    """Summarize and trim old messages, keeping the most recent keep_count.
    Returns the number of deleted messages and the new summary."""
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    from core.conversation_manager import ConversationManager
    from services.llm_service import LLMService

    manager = ConversationManager()
    llm = LLMService()
    result = await manager.summarize_and_trim(
        session, conversation_id,
        keep_count=keep_count,
        llm_service=llm,
    )
    return {
        "conversation_id": conversation_id,
        "deleted": result["deleted"],
        "summary": result["summary"],
    }
