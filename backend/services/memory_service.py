"""
Memory service: CRUD, FTS5 full-text search, hybrid retrieval,
background extraction orchestration, deduplication, and pruning.
"""

import asyncio
import json
import math
import struct
import time
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, engine
from models.memory import Memory

# Reciprocal Rank Fusion constant
RRF_K = 60

# Minimum messages before triggering background extraction
EXTRACTION_MIN_MESSAGES = 10
# Number of recent messages to feed into the extraction prompt
EXTRACTION_MESSAGE_COUNT = 20

# FTS5 is created once per process lifetime
_fts5_initialized = False

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
    """Create the FTS5 virtual table and triggers if they don't exist."""
    global _fts5_initialized
    if _fts5_initialized:
        return
    async with engine.begin() as conn:
        # Virtual table
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "content, memory_type, content_rowid='rowid',"
            "tokenize='unicode61 remove_diacritics 1'"
            ")"
        ))
        # Triggers: keep FTS5 in sync with the memories table (INSERT/DELETE only;
        # UPDATE is handled manually in _sync_fts5 to avoid trigger conflicts).
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
    _fts5_initialized = True
    print("[Memory] FTS5 virtual table ready", flush=True)


class MemoryService:
    """Core memory logic: search, store, extract, prune."""

    @staticmethod
    def _pack_embedding(vec: list[float]) -> bytes:
        return struct.pack(f"<{len(vec)}f", *vec)

    @staticmethod
    def _unpack_embedding(data: bytes) -> list[float]:
        count = len(data) // 4
        return list(struct.unpack(f"<{count}f", data))

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _sanitize_fts5(query: str) -> str:
        """Make a user search string safe for SQLite FTS5 MATCH by stripping
        special characters and wrapping as a phrase query."""
        import re
        # Escape double quotes and truncate to reasonable length
        safe = query.replace('"', '').replace("'", '')
        safe = safe[:300]
        # FTS5 special chars that break query parsing
        safe = re.sub(r'[\(\)\[\]\{\}\^\~\:\*\-\+\=\/\\]', ' ', safe)
        safe = re.sub(r'\s+', ' ', safe).strip()
        if not safe:
            safe = 'unknown'
        # Wrap in double quotes so FTS5 treats content as a literal phrase
        return f'"{safe}"'

    # ---- Search ----

    async def search(
        self,
        session: AsyncSession,
        query: str,
        top_k: int = 3,
        character_id: str | None = None,
    ) -> list[Memory]:
        """Retrieve top-k memories filtered by character (if provided)."""
        if not query or not query.strip():
            # No query: return important recent memories
            from sqlalchemy import select as _select
            result = await session.execute(
                _select(Memory)
                .order_by(Memory.importance.desc(), Memory.last_accessed_at.desc())
                .limit(top_k)
            )
            return list(result.scalars().all())

        # 1. FTS5 search (sanitize query to avoid FTS5 syntax errors)
        fts_matches: list[tuple[str, float]] = []
        fts_query = self._sanitize_fts5(query)
        try:
            fts_result = await session.execute(
                text(
                    "SELECT m.id, rank FROM memory_fts "
                    "JOIN memories m ON memory_fts.rowid = m.rowid "
                    "WHERE memory_fts MATCH :query "
                    "ORDER BY rank LIMIT :limit"
                ),
                {"query": fts_query, "limit": top_k * 3},
            )
            fts_matches = [(row[0], row[1]) for row in fts_result.fetchall()]
        except Exception as e:
            print(f"[Memory] FTS5 search failed: {e}", flush=True)

        memory_ids = [mid for mid, _ in fts_matches]

        # 2. Load Memory objects
        from sqlalchemy import select as _select

        if memory_ids:
            result = await session.execute(
                _select(Memory).where(Memory.id.in_(memory_ids))
            )
            memories_by_id = {m.id: m for m in result.scalars().all()}
        else:
            memories_by_id = {}

        # 3. Hybrid ranking (RRF) if embeddings are available
        query_emb = None
        try:
            from services.embedding_service import embed_single
            query_emb = await embed_single(query)
        except Exception:
            pass

        if query_emb and memories_by_id:
            # Compute cosine scores for FTS5-matched memories
            cosine_scores: dict[str, float] = {}
            for mid, mem in memories_by_id.items():
                if mem.embedding:
                    try:
                        mem_emb = self._unpack_embedding(mem.embedding)
                        cosine_scores[mid] = self._cosine_similarity(query_emb, mem_emb)
                    except Exception:
                        pass

            # RRF merge: FTS5 rank + cosine rank
            fts_rank = {mid: i + 1 for i, (mid, _) in enumerate(fts_matches)}
            cosine_sorted = sorted(cosine_scores.items(), key=lambda x: x[1], reverse=True)
            cosine_rank = {mid: i + 1 for i, (mid, _) in enumerate(cosine_sorted)}

            all_ids = set(fts_rank) | set(cosine_rank)
            rrf_scores = {}
            for mid in all_ids:
                score = 0.0
                if mid in fts_rank:
                    score += 1.0 / (RRF_K + fts_rank[mid])
                if mid in cosine_rank:
                    score += 1.0 / (RRF_K + cosine_rank[mid])
                rrf_scores[mid] = score

            sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:top_k]
        elif memories_by_id:
            # FTS5-only: sort by BM25 rank
            fts_rank = {mid: i for i, (mid, _) in enumerate(fts_matches)}
            sorted_ids = sorted(fts_rank, key=fts_rank.get)[:top_k]
        else:
            # Fallback: no FTS5 results, return recent important memories
            sorted_ids = []
            result = await session.execute(
                _select(Memory)
                .order_by(Memory.importance.desc(), Memory.last_accessed_at.desc())
                .limit(top_k)
            )
            for mem in result.scalars().all():
                sorted_ids.append(mem.id)

        # 4. Apply importance boost and return in order
        scored: list[tuple[Memory, float]] = []
        now = datetime.now(timezone.utc)
        for mid in sorted_ids:
            mem = memories_by_id.get(mid)
            if not mem:
                continue
            # Importance boost
            importance_boost = 1.0 + 0.15 * (mem.importance - 5)
            # Recency decay
            if mem.last_accessed_at:
                days = (now - mem.last_accessed_at.replace(tzinfo=timezone.utc)).days
            else:
                days = (now - mem.created_at.replace(tzinfo=timezone.utc)).days
            recency_boost = 1.0 / (1.0 + 0.05 * max(days, 0))
            scored.append((mem, importance_boost * recency_boost))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = [mem for mem, _ in scored[:top_k]]

        # Filter by character_id if provided
        if character_id:
            from models.conversation import Conversation
            conv_ids = {m.source_conversation_id for m in results if m.source_conversation_id}
            if conv_ids:
                conv_result = await session.execute(
                    _select(Conversation).where(Conversation.id.in_(conv_ids))
                )
                conv_char_map = {c.id: c.character_id for c in conv_result.scalars().all()}
                results = [m for m in results if conv_char_map.get(m.source_conversation_id) == character_id]

        # Update access metadata
        for mem in results:
            mem.access_count += 1
            mem.last_accessed_at = datetime.now(timezone.utc)
        await session.commit()

        return results

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
        mem = Memory(
            content=content,
            memory_type=memory_type,
            importance=importance,
            source_conversation_id=source_conversation_id,
            embedding=self._pack_embedding(embedding) if embedding else None,
        )
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        if source_conversation_id:
            push_memory_notification(source_conversation_id, 1)
        return mem

    # ---- Deduplication ----

    @staticmethod
    async def _sync_fts5_update(mem_id: str, old_content: str, new_content: str, memory_type: str):
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
                        text("INSERT INTO memory_fts(memory_fts, rowid, content, memory_type) "
                             "VALUES ('delete', :rowid, :old_content, :mem_type)"),
                        {"rowid": rowid, "old_content": old_content, "mem_type": memory_type},
                    )
                except Exception:
                    pass
                # Insert updated entry
                try:
                    await s.execute(
                        text("INSERT INTO memory_fts(rowid, content, memory_type) "
                             "VALUES (:rowid, :new_content, :mem_type)"),
                        {"rowid": rowid, "new_content": new_content, "mem_type": memory_type},
                    )
                except Exception:
                    pass
                await s.commit()
            except Exception as e:
                print(f"[Memory] _sync_fts5_update failed: {e}", flush=True)

    async def _find_similar(
        self, session: AsyncSession, content: str, threshold: float = 0.85
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
            result2 = await session.execute(_select(Memory).where(Memory.id == best_id))
            return result2.scalar_one_or_none()
        return None

    # ---- Extraction ----

    async def extract_and_store(
        self,
        conversation_id: str,
        llm_service,
    ) -> list[Memory]:
        """
        Background task: extract memories from recent conversation messages
        via LLM, deduplicate, and store. Uses its own session.
        """
        async with async_session() as session:
            # 1. Load recent messages for this conversation
            from sqlalchemy import select as _select
            from models.message import Message

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
                # Non-streaming call using the LLM service's underlying clients
                extracted = await self._call_llm_for_extraction(llm_service, extraction_prompt)
            except Exception as e:
                print(f"[Memory] extraction LLM call failed: {e}", flush=True)
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
                existing = await self._find_similar(session, content)
                if existing:
                    old_content = existing.content
                    existing.importance = max(existing.importance, item.get("importance", 5))
                    existing.content = content  # use newer wording
                    await session.commit()
                    await self._sync_fts5_update(
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

                mem = await self.add_memory(
                    session,
                    content=content,
                    memory_type=item.get("memory_type", "user_fact"),
                    importance=item.get("importance", 5),
                    source_conversation_id=conversation_id,
                    embedding=emb,
                )
                stored.append(mem)

            if stored:
                print(f"[Memory] extracted {len(stored)} new memories", flush=True)
                push_memory_notification(conversation_id, len(stored))
            return stored

    async def _call_llm_for_extraction(self, llm_service, prompt: str) -> str:
        """Call the LLM non-streaming for memory extraction."""
        messages = [
            {"role": "system", "content": "你是一个记忆记录员。用第一人称日记体记录对话中的重要信息。只输出有效的 JSON 数组。"},
            {"role": "user", "content": prompt},
        ]
        return await llm_service.chat_sync(messages, max_tokens=1024, temperature=0.3)

    # ---- Pruning ----

    async def prune(self) -> int:
        """Delete stale low-importance memories. Returns count of deleted rows."""
        async with async_session() as session:
            from sqlalchemy import delete, select
            cutoff = datetime.now(timezone.utc)
            # Delete memories older than 90 days with importance < 3 and access_count < 2
            result = await session.execute(
                select(Memory.id).where(
                    Memory.created_at < "1970-01-01"  # placeholder, we filter in Python
                )
            )
            all_ids = [row[0] for row in result.fetchall()]

            to_delete = []
            result2 = await session.execute(select(Memory))
            for mem in result2.scalars().all():
                age_days = (cutoff.replace(tzinfo=None) - mem.created_at.replace(tzinfo=None)).days
                if age_days > 90 and mem.importance < 3 and mem.access_count < 2:
                    to_delete.append(mem.id)

            if to_delete:
                await session.execute(delete(Memory).where(Memory.id.in_(to_delete)))
                await session.commit()
                print(f"[Memory] pruned {len(to_delete)} stale memories", flush=True)

            return len(to_delete)


# Singleton
memory_service = MemoryService()
