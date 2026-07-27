"""
MemoryStore — storage adapter for persistent memories.

Handles FTS5 virtual table setup, CRUD, binary embedding packing,
similarity-based dedup, and stale-memory pruning.
"""

import struct
from datetime import datetime, timezone

from sqlalchemy import text, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, engine
from models.memory import Memory, SOURCE_AI_SUMMARIZED

# FTS5 is created once per process lifetime
_fts5_ready = False


class MemoryStore:
    """Storage adapter for Memory CRUD, FTS5, and embedding."""

    # ---- FTS5 setup ---------------------------------------------------

    @staticmethod
    async def ensure_fts5():
        """Create the FTS5 virtual table and triggers if they don't exist."""
        global _fts5_ready
        if _fts5_ready:
            return
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "content, memory_type, content_rowid='rowid',"
                "tokenize='unicode61 remove_diacritics 1'"
                ")"
            ))
            await conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memories BEGIN "
                "INSERT INTO memory_fts(rowid, content, memory_type) "
                "VALUES (new.rowid, new.content, new.memory_type); END"
            ))
            await conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memories BEGIN "
                "INSERT INTO memory_fts(memory_fts, rowid, content, memory_type) "
                "VALUES ('delete', old.rowid, old.content, old.memory_type); END"
            ))
        _fts5_ready = True
        print("[MemoryStore] FTS5 virtual table ready", flush=True)

    # ---- Embedding helpers --------------------------------------------

    @staticmethod
    def pack_embedding(vec: list[float]) -> bytes:
        return struct.pack(f"<{len(vec)}f", *vec)

    @staticmethod
    def unpack_embedding(data: bytes) -> list[float]:
        count = len(data) // 4
        return list(struct.unpack(f"<{count}f", data))

    # ---- CRUD ---------------------------------------------------------

    async def add(
        self,
        session: AsyncSession,
        content: str,
        memory_type: str = "user_fact",
        importance: int = 5,
        source_conversation_id: str | None = None,
        embedding: list[float] | None = None,
        character_id: str | None = None,
        source: str = SOURCE_AI_SUMMARIZED,
    ) -> Memory:
        """Insert a memory with optional embedding."""
        from services.memory_service import push_memory_notification

        mem = Memory(
            content=content,
            memory_type=memory_type,
            importance=importance,
            source_conversation_id=source_conversation_id,
            embedding=self.pack_embedding(embedding) if embedding else None,
            character_id=character_id,
            source=source,
        )
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        if source_conversation_id:
            push_memory_notification(source_conversation_id, 1)
        return mem

    # ---- FTS5 sync (content update during dedup) ---------------------

    @staticmethod
    async def sync_fts5_update(
        mem_id: str,
        old_content: str,
        new_content: str,
        memory_type: str,
    ):
        """Manually sync FTS5 after a content update (no UPDATE trigger)."""
        async with async_session() as s:
            try:
                result = await s.execute(
                    text("SELECT rowid FROM memories WHERE id = :id"),
                    {"id": mem_id},
                )
                row = result.fetchone()
                if not row:
                    return
                rowid = row[0]
                # Delete old entry (may fail if never indexed, ignore)
                try:
                    await s.execute(
                        text(
                            "INSERT INTO memory_fts(memory_fts, rowid, content, memory_type) "
                            "VALUES ('delete', :rowid, :old_content, :mem_type)"
                        ),
                        {"rowid": rowid, "old_content": old_content, "mem_type": memory_type},
                    )
                except Exception:
                    pass
                # Insert updated entry
                try:
                    await s.execute(
                        text(
                            "INSERT INTO memory_fts(rowid, content, memory_type) "
                            "VALUES (:rowid, :new_content, :mem_type)"
                        ),
                        {"rowid": rowid, "new_content": new_content, "mem_type": memory_type},
                    )
                except Exception:
                    pass
                await s.commit()
            except Exception as e:
                print(f"[MemoryStore] sync_fts5_update failed: {e}", flush=True)

    # ---- Similarity-based dedup ---------------------------------------

    async def find_similar(
        self,
        session: AsyncSession,
        content: str,
        threshold: float = 0.85,
    ) -> Memory | None:
        """FTS5 search for near-duplicate content. Returns best match or None."""
        try:
            result = await session.execute(
                text(
                    "SELECT m.id, m.content FROM memory_fts "
                    "JOIN memories m ON memory_fts.rowid = m.rowid "
                    "WHERE memory_fts MATCH :query ORDER BY rank LIMIT 5"
                ),
                {"query": content},
            )
            matches = [(row[0], row[1]) for row in result.fetchall()]
        except Exception:
            return None

        if not matches:
            return None

        # Weighted word overlap for CJK-friendly fuzzy matching
        def overlap_score(a: str, b: str) -> float:
            words_a = set(a)
            words_b = set(b)
            if not words_a or not words_b:
                return 0.0
            intersection = words_a & words_b
            return len(intersection) / min(len(words_a), len(words_b))

        best_id, best_score = None, 0.0
        for mid, existing_content in matches:
            score = overlap_score(content, existing_content)
            if score > best_score:
                best_score = score
                best_id = mid

        if best_id and best_score >= threshold:
            from sqlalchemy import select as _select

            result2 = await session.execute(
                _select(Memory).where(Memory.id == best_id)
            )
            return result2.scalar_one_or_none()
        return None

    # ---- Pruning ------------------------------------------------------

    async def prune(self) -> int:
        """Delete stale low-importance memories. Returns count of deleted rows."""
        async with async_session() as session:
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(days=90)

            result = await session.execute(
                delete(Memory).where(
                    Memory.importance < 3,
                    Memory.access_count < 2,
                    Memory.created_at < cutoff,
                )
            )
            await session.commit()
            count = result.rowcount
            if count:
                print(f"[MemoryStore] pruned {count} stale memories", flush=True)
            return count
