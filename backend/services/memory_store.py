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


async def _migrate_fts5_triggers(conn):
    """Replace old delete-marker triggers with DELETE FROM … WHERE rowid=… syntax.

    SQLite ≥3.50 rejects the FTS5 ``'delete'`` marker on tables created
    with ``content_rowid``.  This migration drops the old triggers and
    recreates them, and also strips the now-unnecessary ``content_rowid``
    option from the FTS5 table DDL if present.
    """
    for trig in ("memory_fts_delete", "memory_fts_update"):
        try:
            await conn.execute(text(f"DROP TRIGGER IF EXISTS {trig}"))
        except Exception:
            pass

    try:
        await conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_delete "
            "AFTER DELETE ON memories BEGIN "
            "DELETE FROM memory_fts WHERE rowid = old.rowid; END"
        ))
    except Exception:
        pass

    try:
        await conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_update "
            "AFTER UPDATE ON memories BEGIN "
            "DELETE FROM memory_fts WHERE rowid = old.rowid; "
            "INSERT INTO memory_fts(rowid, content, memory_type) "
            "VALUES (new.rowid, new.content, new.memory_type); END"
        ))
    except Exception:
        pass

    try:
        await conn.execute(text("DROP TRIGGER IF EXISTS memory_fts_insert"))
    except Exception:
        pass
    try:
        await conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_insert "
            "AFTER INSERT ON memories BEGIN "
            "INSERT INTO memory_fts(rowid, content, memory_type) "
            "VALUES (new.rowid, new.content, new.memory_type); END"
        ))
    except Exception:
        pass

    print("[MemoryStore] FTS5 triggers migrated to DELETE FROM syntax", flush=True)


class MemoryStore:
    """Storage adapter for Memory CRUD, FTS5, and embedding."""

    # ---- FTS5 setup ---------------------------------------------------

    @staticmethod
    async def ensure_fts5():
        """Create the FTS5 virtual table and triggers if they don't exist.

        Uses ``DELETE FROM memory_fts WHERE rowid=old.rowid`` instead of
        the FTS5 ``'delete'`` marker because SQLite ≥3.50 rejects the
        marker syntax when ``content_rowid`` was used in the table DDL.
        """
        global _fts5_ready
        if _fts5_ready:
            return
        async with engine.begin() as conn:
            # 1. FTS5 virtual table (no content_rowid — not an external content table)
            await conn.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "content, memory_type,"
                "tokenize='unicode61 remove_diacritics 1'"
                ")"
            ))
            # 2. Insert trigger
            await conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memories BEGIN "
                "INSERT INTO memory_fts(rowid, content, memory_type) "
                "VALUES (new.rowid, new.content, new.memory_type); END"
            ))
            # 3. Delete trigger — use plain DELETE, not the FTS5 'delete' marker
            await conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memories BEGIN "
                "DELETE FROM memory_fts WHERE rowid = old.rowid; END"
            ))
            # 4. Update trigger — delete old + insert new
            await conn.execute(text(
                "CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memories BEGIN "
                "DELETE FROM memory_fts WHERE rowid = old.rowid; "
                "INSERT INTO memory_fts(rowid, content, memory_type) "
                "VALUES (new.rowid, new.content, new.memory_type); END"
            ))

            # 5. Migrate triggers that used the old delete-marker syntax
            await _migrate_fts5_triggers(conn)

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
        created_at: datetime | None = None,
    ) -> Memory:
        """Insert a memory, deduplicating against existing similar content.

        If *created_at* is provided it is used as the record timestamp;
        otherwise the server default (now) applies.  Daily extraction
        passes the window start date so memories reflect the conversation
        date rather than the extraction date.

        **不推送通知**（审查 S3）：一次提取会存 N 条，逐条推会让前端把同一批
        数两遍。推送归调用方 —— `MemoryExtractor.store()` 按批推一次，
        `save_memory` 工具由 Agent 的显式 `memory_updated` 事件负责。
        """
        # Check for near-duplicate before inserting (同一角色范围内)
        existing = await self.find_similar(session, content, character_id=character_id)
        if existing:
            old_content = existing.content
            existing.importance = max(existing.importance, importance)
            existing.content = content  # use newer wording
            if source_conversation_id:
                existing.source_conversation_id = source_conversation_id
            await session.commit()
            await session.refresh(existing)
            await self.sync_fts5_update(
                existing.id, old_content, content, existing.memory_type,
            )
            return existing

        mem_kwargs: dict = {
            "content": content,
            "memory_type": memory_type,
            "importance": importance,
            "source_conversation_id": source_conversation_id,
            "embedding": self.pack_embedding(embedding) if embedding else None,
            "character_id": character_id,
            "source": source,
        }
        if created_at is not None:
            mem_kwargs["created_at"] = created_at
        mem = Memory(**mem_kwargs)
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        return mem

    # ---- FTS5 sync (content update during dedup) ---------------------

    @staticmethod
    async def sync_fts5_update(
        mem_id: str,
        old_content: str,
        new_content: str,
        memory_type: str,
    ):
        """Manually sync FTS5 after a content-only update.

        Needed when content is changed via ORM attribute mutation +
        commit (which fires the UPDATE trigger and handles FTS5 sync
        automatically for most cases).  This is a fallback for edge
        cases where the trigger might not cover the update path.
        """
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
                await s.execute(
                    text("DELETE FROM memory_fts WHERE rowid = :rowid"),
                    {"rowid": rowid},
                )
                await s.execute(
                    text(
                        "INSERT INTO memory_fts(rowid, content, memory_type) "
                        "VALUES (:rowid, :new_content, :mem_type)"
                    ),
                    {"rowid": rowid, "new_content": new_content, "mem_type": memory_type},
                )
                await s.commit()
            except Exception as e:
                print(f"[MemoryStore] sync_fts5_update failed: {e}", flush=True)

    # ---- Similarity-based dedup ---------------------------------------

    async def find_similar(
        self,
        session: AsyncSession,
        content: str,
        threshold: float = 0.85,
        character_id: str | None = None,
    ) -> Memory | None:
        """FTS5 search for near-duplicate content. Returns best match or None.

        `character_id` 给了就只在**这个角色的**记忆里找重复：记忆本来就是按角色
        一份，跨角色去重会让 A 记过的事进不了 B 的记忆（ticket #55 的"提取一次 →
        每人一份"就靠它）。
        """
        try:
            result = await session.execute(
                text(
                    "SELECT m.id, m.content FROM memory_fts "
                    "JOIN memories m ON memory_fts.rowid = m.rowid "
                    "WHERE memory_fts MATCH :query "
                    "AND (m.character_id IS :character_id) "
                    "ORDER BY rank LIMIT 5"
                ),
                {"query": content, "character_id": character_id},
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
