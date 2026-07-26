"""
Memory service — backward-compatible singleton that delegates to
MemoryStore, MemoryRetriever, and MemoryExtractor.

All three sub-modules receive the same MemoryStore instance via
constructor injection so they share the same storage backend.
"""

import time
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from models.memory import Memory
from services.memory_store import MemoryStore
from services.memory_retriever import MemoryRetriever
from services.memory_extractor import MemoryExtractor

# Reciprocal Rank Fusion constant (used by MemoryRetriever)
RRF_K = 60

# Minimum messages before triggering background extraction (used by MemoryExtractor)
EXTRACTION_MIN_MESSAGES = 10
# Number of recent messages to feed into the extraction prompt
EXTRACTION_MESSAGE_COUNT = 20

# Global notification queue for memory updates from background tasks.
# Format: { conversation_id: [(count, timestamp), ...] }
_memory_notifications: dict[str, list[tuple[int, float]]] = defaultdict(list)


def push_memory_notification(conversation_id: str, count: int):
    """Push a memory-update notification for a conversation. Thread-safe enough
    for single-thread asyncio usage (called from background tasks or tools)."""
    if count > 0:
        _memory_notifications[conversation_id].append((count, time.time()))


def pop_memory_notifications(conversation_id: str) -> int:
    """Pop all pending memory-update counts for a conversation."""
    events = _memory_notifications.pop(conversation_id, [])
    return sum(c for c, _ in events)


async def _ensure_fts5():
    """Create the FTS5 virtual table and triggers if they don't exist.
    Delegates to MemoryStore — kept as module-level proxy for startup compatibility."""
    await MemoryStore.ensure_fts5()


class MemoryService:
    """Backward-compatible singleton that delegates to the three deep modules.

    Storage  → MemoryStore
    Search   → MemoryRetriever(MemoryStore)
    Extract  → MemoryExtractor(MemoryStore)
    """

    def __init__(self):
        self._store = MemoryStore()
        self._retriever = MemoryRetriever(self._store)
        self._extractor = MemoryExtractor(self._store)

    # ---- Search ----

    async def search(
        self,
        session: AsyncSession,
        query: str,
        top_k: int = 3,
        character_id: str | None = None,
    ) -> list[Memory]:
        """Retrieve top-k memories filtered by character (if provided)."""
        return await self._retriever.search(session, query, top_k, character_id)

    # ---- CRUD ----

    async def add_memory(
        self,
        session: AsyncSession,
        content: str,
        memory_type: str = "user_fact",
        importance: int = 5,
        source_conversation_id: str | None = None,
        embedding: list[float] | None = None,
    ) -> Memory:
        """Insert a memory with optional embedding."""
        return await self._store.add(
            session, content,
            memory_type=memory_type,
            importance=importance,
            source_conversation_id=source_conversation_id,
            embedding=embedding,
        )

    # ---- Extraction ----

    async def extract_and_store(
        self,
        conversation_id: str,
        llm_service,
    ) -> list[Memory]:
        """Background task: extract memories from recent conversation messages
        via LLM, deduplicate, and store. Uses its own session."""
        return await self._extractor.extract_and_store(conversation_id, llm_service)

    # ---- Pruning ----

    async def prune(self) -> int:
        """Delete stale low-importance memories. Returns count of deleted rows."""
        return await self._store.prune()


# Singleton
memory_service = MemoryService()
