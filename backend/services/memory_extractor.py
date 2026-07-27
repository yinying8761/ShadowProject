"""
MemoryExtractor — LLM-driven memory extraction from conversations.

Loads recent messages, calls the LLM for diary-style memory extraction,
parses the JSON response, deduplicates against existing memories,
computes embeddings, and stores new memories.
"""

import json

from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.memory import Memory, SOURCE_AI_SUMMARIZED
from services.memory_store import MemoryStore


class MemoryExtractor:
    """LLM-driven memory extraction, dedup, embed, and store."""

    def __init__(self, store: MemoryStore):
        self._store = store

    # ---- Extraction ---------------------------------------------------

    async def extract_and_store(
        self,
        conversation_id: str,
        llm_service,
        character_id: str | None = None,
        since_date: str | None = None,
    ) -> list[Memory]:
        """
        Extract memories from conversation messages via LLM, deduplicate,
        and store. Uses its own session.

        If *since_date* is provided (ISO date string like "2026-07-27"),
        only messages created on or after that date are considered.  This
        replaces the fixed EXTRACTION_MESSAGE_COUNT window with a natural
        time boundary — ideal for once-per-day extraction before greeting.
        """
        from services.memory_service import (
            EXTRACTION_MIN_MESSAGES,
            EXTRACTION_MESSAGE_COUNT,
            push_memory_notification,
        )

        async with async_session() as session:
            # 1. Load recent messages for this conversation
            from models.message import Message
            from datetime import date as _date

            if since_date:
                boundary = _date.fromisoformat(since_date)
                result = await session.execute(
                    _select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.created_at >= boundary,
                    )
                    .order_by(Message.created_at.desc())
                )
            else:
                result = await session.execute(
                    _select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(EXTRACTION_MESSAGE_COUNT)
                )
            messages = list(result.scalars().all())

            if len(messages) < EXTRACTION_MIN_MESSAGES:
                return []

            # Build conversation transcript
            lines = []
            for msg in reversed(messages):  # chronological order
                role_label = "用户" if msg.role == "user" else "角色"
                lines.append(f"[{role_label}]: {msg.content or '(tool)'}")
            transcript = "\n".join(lines)

            # 2. Call LLM for extraction (diary-style first-person narrative)
            extraction_prompt = (
                "你是一个记忆记录员。看了下面的对话后，用写日记的方式记录下你（AI 角色）"
                "在这次对话中了解到的事情。输出格式为严格的 JSON 数组。\n\n"
                "每条记录包含：\n"
                '- "content": 一小段日记（1-3句话），用第一人称叙述。例如：'
                '"今天和用户聊了他最近在学的 Rust，他似乎遇到了一些所有权概念的困惑，我帮他梳理了一下。" '
                '或 "主人今天心情不太好，说工作压力很大，我陪他聊了一会儿。"\n'
                '- "memory_type": "user_fact" / "user_preference" / "important_event"\n'
                '- "importance": 1-10（10=非常个人化、情感上重要、以后很可能需要回忆起）\n\n'
                "规则：\n"
                '- 用 AI 角色（你自己）的视角写，称自己为「我」。\n'
                "- 对用户的称呼根据对话氛围自然选择——用户、主人、他/她、对方的名字等都行。\n"
                "- 重点记录你了解到的关于用户的事情、发生了什么、用户的情绪状态。\n"
                "- 不要记录琐碎的问候和闲聊。\n"
                "- 如果没有值得记录的内容，输出空数组 []。\n\n"
                "对话内容：\n"
                f"{transcript}"
            )

            try:
                extracted = await self._call_llm_for_extraction(
                    llm_service, extraction_prompt
                )
            except Exception as e:
                print(
                    f"[MemoryExtractor] extraction LLM call failed: {e}",
                    flush=True,
                )
                return []

            if not extracted:
                return []

            # 3. Parse JSON
            try:
                items = json.loads(extracted)
                if not isinstance(items, list):
                    return []
            except json.JSONDecodeError:
                # Try to extract JSON array from response
                start = extracted.find("[")
                end = extracted.rfind("]") + 1
                if start >= 0 and end > start:
                    try:
                        items = json.loads(extracted[start:end])
                    except json.JSONDecodeError:
                        return []
                else:
                    return []

            # 4. Deduplicate, embed, and store
            stored = []
            for item in items:
                content = item.get("content", "").strip()
                if not content:
                    continue

                # Dedup
                existing = await self._store.find_similar(session, content)
                if existing:
                    old_content = existing.content
                    existing.importance = max(
                        existing.importance, item.get("importance", 5)
                    )
                    existing.content = content  # use newer wording
                    await session.commit()
                    await MemoryStore.sync_fts5_update(
                        existing.id, old_content, content, existing.memory_type
                    )
                    stored.append(existing)
                    continue

                # Compute embedding
                emb = None
                try:
                    from services.embedding_service import embed_single

                    emb = await embed_single(content)
                except Exception:
                    pass

                mem = await self._store.add(
                    session,
                    content=content,
                    memory_type=item.get("memory_type", "user_fact"),
                    importance=item.get("importance", 5),
                    source_conversation_id=conversation_id,
                    embedding=emb,
                    character_id=character_id,
                    source=SOURCE_AI_SUMMARIZED,
                )
                stored.append(mem)

            if stored:
                print(
                    f"[MemoryExtractor] extracted {len(stored)} new memories",
                    flush=True,
                )
                push_memory_notification(conversation_id, len(stored))
            return stored

    async def _call_llm_for_extraction(self, llm_service, prompt: str) -> str:
        """Call the LLM non-streaming for memory extraction."""
        messages = [
            {
                "role": "system",
                "content": "你是一个记忆记录员。用第一人称日记体记录对话中的重要信息。只输出有效的 JSON 数组。",
            },
            {"role": "user", "content": prompt},
        ]
        return await llm_service.chat_sync(
            messages, max_tokens=1024, temperature=0.3
        )
