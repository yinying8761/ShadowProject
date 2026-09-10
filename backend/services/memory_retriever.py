"""
MemoryRetriever — hybrid memory search pipeline.

Combines FTS5 full-text search, embedding cosine similarity,
Reciprocal Rank Fusion (RRF), and importance/recency boosting.
"""

import math
from datetime import datetime, timezone

from sqlalchemy import text, select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from models.memory import Memory
from services.memory_store import MemoryStore


class MemoryRetriever:
    """Hybrid search: FTS5 → embedding cosine → RRF → importance/recency."""

    def __init__(self, store: MemoryStore):
        self._store = store

    # ---- Static helpers -----------------------------------------------

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
        """Reduce a user search string to plain FTS5 tokens.

        Whitelist approach: keep only word characters (CJK, letters, digits,
        underscore) and whitespace — every other character (punctuation,
        quotes, FTS5 operators) becomes a space. The old character blacklist
        kept getting missed (e.g. the dot in a filename like "agent岗.md"
        broke MATCH syntax).
        """
        import re

        safe = re.sub(r'[^\w\s]', ' ', query)
        # FTS5 treats uppercase AND/OR/NOT/NEAR as query operators — a bare
        # trailing "AND" alone is a syntax error. Drop them from user text.
        safe = re.sub(r'\b(AND|OR|NOT|NEAR)\b', ' ', safe)
        safe = re.sub(r'\s+', ' ', safe).strip()
        return safe or 'unknown'

    # ---- Search -------------------------------------------------------

    async def search(
        self,
        session: AsyncSession,
        query: str,
        top_k: int = 3,
        character_id: str | None = None,
    ) -> list[Memory]:
        """Retrieve top-k memories filtered by character (if provided)."""
        from services.memory_service import RRF_K

        if not query or not query.strip():
            # No query: return important recent memories
            if character_id:
                result = await session.execute(
                    _select(Memory)
                    .where(Memory.character_id == character_id)
                    .order_by(Memory.importance.desc(), Memory.last_accessed_at.desc())
                    .limit(top_k)
                )
            else:
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
            print(f"[MemoryRetriever] FTS5 search failed: {e}", flush=True)

        memory_ids = [mid for mid, _ in fts_matches]

        # 2. Load Memory objects
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
                        mem_emb = MemoryStore.unpack_embedding(mem.embedding)
                        cosine_scores[mid] = self._cosine_similarity(
                            query_emb, mem_emb
                        )
                    except Exception:
                        pass

            # RRF merge: FTS5 rank + cosine rank
            fts_rank = {mid: i + 1 for i, (mid, _) in enumerate(fts_matches)}
            cosine_sorted = sorted(
                cosine_scores.items(), key=lambda x: x[1], reverse=True
            )
            cosine_rank = {
                mid: i + 1 for i, (mid, _) in enumerate(cosine_sorted)
            }

            all_ids = set(fts_rank) | set(cosine_rank)
            rrf_scores = {}
            for mid in all_ids:
                score = 0.0
                if mid in fts_rank:
                    score += 1.0 / (RRF_K + fts_rank[mid])
                if mid in cosine_rank:
                    score += 1.0 / (RRF_K + cosine_rank[mid])
                rrf_scores[mid] = score

            sorted_ids = sorted(
                rrf_scores, key=rrf_scores.get, reverse=True
            )[:top_k]
        elif memories_by_id:
            # FTS5-only: sort by BM25 rank
            fts_rank = {mid: i for i, (mid, _) in enumerate(fts_matches)}
            sorted_ids = sorted(fts_rank, key=fts_rank.get)[:top_k]
        else:
            # Fallback: no FTS5 results, return recent important memories
            sorted_ids = []
            result = await session.execute(
                _select(Memory)
                .order_by(
                    Memory.importance.desc(), Memory.last_accessed_at.desc()
                )
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
                days = (
                    now - mem.last_accessed_at.replace(tzinfo=timezone.utc)
                ).days
            else:
                days = (
                    now - mem.created_at.replace(tzinfo=timezone.utc)
                ).days
            recency_boost = 1.0 / (1.0 + 0.05 * max(days, 0))
            scored.append((mem, importance_boost * recency_boost))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = [mem for mem, _ in scored[:top_k]]

        # Filter by character_id if provided (direct column, not via conversation)
        if character_id:
            results = [m for m in results if m.character_id == character_id]

        # Update access metadata
        for mem in results:
            mem.access_count += 1
            mem.last_accessed_at = datetime.now(timezone.utc)
        await session.commit()

        return results
