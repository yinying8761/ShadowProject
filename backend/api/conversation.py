from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from api.user_profile import resolve_user_profile
from core.conversation_manager import ConversationManager
from core.transcript import render_lines
from database import get_session
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

#: 说话人解析与压缩共用一个实例（无状态，只带窗口大小）。
conv_manager = ConversationManager()


class ConversationCreate(BaseModel):
    character_id: str
    title: str = Conversation.DEFAULT_TITLE


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
            # 「还没起过名」由后端判定（默认标题是模型的常量）：前端不再自带一份
            # 'New Conversation' 去比对（审查 S6）。
            "is_default_title": conv_manager.is_default_title(c.title),
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
    return {
        "id": conv.id,
        "character_id": conv.character_id,
        "title": conv.title,
        "is_default_title": conv_manager.is_default_title(conv.title),
    }


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
        # 单条也要带：前端改名后用它刷新本地那条，否则陈旧的 flag 会让列表
        # 一直显示默认标题（复核发现的回归）。
        "is_default_title": conv_manager.is_default_title(conv.title),
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

    # Speaker inputs for the shared transcript renderer — the ONE transcript
    # formatter serves both the LLM context and this history view (CONTEXT.md
    # §6 / spec: group-chat Phase 1). Never format locally.
    character_name = None
    user_name = None
    conv = await session.get(Conversation, conversation_id)
    if conv:
        # A group conversation has no single character: skip the lookup
        # entirely rather than asking for a NULL primary key.
        if conv.character_id:
            char = await session.get(CharacterProfile, conv.character_id)
            character_name = char.name if char else None
        profile = await resolve_user_profile(session, conv.character_id)
        user_name = profile.user_name if profile else None

    # 说话人解析只有一处实现（ConversationManager）：LLM 上下文与历史记录共用同一
    # 份规则 —— 按消息自己的 speaker_id 查名字，而不是按当前群成员资格。
    speaker_names = await conv_manager.resolve_speaker_names(session, messages)

    rendered = render_lines(
        messages,
        user_name=user_name,
        character_name=character_name,
        speaker_names=speaker_names,
    )

    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "tool_calls": m.tool_calls,
            "token_count": m.token_count,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "speaker_id": m.speaker_id,
            "speaker": line.speaker if line else None,
            "transcript": line.text if line else None,
        }
        for m, line in zip(messages, rendered)
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
    from sqlalchemy import update

    from models.memory import Memory

    conv = await session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # 记忆按角色保留，但不再指向已删除的会话：模型声明的语义是 SET NULL，
    # 而 SQLite 不强制 FK（ADR-0005）—— 服务层显式补上，不给悬空引用留口子。
    await session.execute(
        update(Memory)
        .where(Memory.source_conversation_id == conversation_id)
        .values(source_conversation_id=None)
    )
    await session.delete(conv)
    await session.commit()
    return {"status": "deleted"}


class ConversationUpdate(BaseModel):
    title: str


@router.put("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    session: AsyncSession = Depends(get_session),
):
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    stripped = data.title.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    conv.title = stripped
    await session.commit()
    await session.refresh(conv)
    return {
        "id": conv.id,
        "character_id": conv.character_id,
        "title": conv.title,
        # 改名后前端要就地刷新这条：不带这个字段的话，本地那份 `is_default_title`
        # 会一直停在 true，侧栏就显示不回用户刚起的名字（复核发现的回归）。
        "is_default_title": conv_manager.is_default_title(conv.title),
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


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

    from services.llm_service import LLMService

    llm = LLMService()
    result = await conv_manager.summarize_and_trim(
        session, conversation_id,
        keep_count=keep_count,
        llm_service=llm,
    )
    return {
        "conversation_id": conversation_id,
        "deleted": result["deleted"],
        "summary": result["summary"],
    }
