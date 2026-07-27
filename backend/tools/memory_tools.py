"""
Memory tools: search_memory (implicit recall) and save_memory (explicit save).
Both call the singleton MemoryService.
"""

import json
from services.memory_service import memory_service
from database import async_session
from models.memory import SOURCE_AI_SUMMARIZED


async def search_memory(query: str, limit: int = 3, character_id: str | None = None) -> str:
    """Search the AI's long-term memory about the user. Returns JSON array of matches."""
    async with async_session() as session:
        memories = await memory_service.search(
            session, query=query, top_k=limit, character_id=character_id
        )
        results = [
            {
                "id": m.id,
                "content": m.content,
                "type": m.memory_type,
                "importance": m.importance,
            }
            for m in memories
        ]
        return json.dumps(results, ensure_ascii=False)


async def save_memory(
    content: str,
    memory_type: str = "user_fact",
    importance: int = 5,
    character_id: str | None = None,
    source: str = SOURCE_AI_SUMMARIZED,
) -> str:
    """Explicitly save a fact about the user to persistent memory."""
    if importance < 1:
        importance = 1
    if importance > 10:
        importance = 10
    if memory_type not in ("user_fact", "user_preference", "important_event"):
        memory_type = "user_fact"

    # Compute embedding
    embedding = None
    try:
        from services.embedding_service import embed_single
        embedding = await embed_single(content)
    except Exception:
        pass

    async with async_session() as session:
        mem = await memory_service.add_memory(
            session,
            content=content,
            memory_type=memory_type,
            importance=importance,
            embedding=embedding,
            character_id=character_id,
            source=source,
        )
        return json.dumps(
            {"id": mem.id, "content": mem.content, "type": mem.memory_type},
            ensure_ascii=False,
        )
