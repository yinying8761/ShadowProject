"""Acceptance (ticket #51): run the group migration sequence against a COPY
of data/companion.db and verify conversation/message counts are unchanged and
conversations.character_id becomes nullable. Read-only on the real DB."""

import asyncio
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

SOURCE = ROOT / "data" / "companion.db"


def snapshot(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        conversations = conn.execute("SELECT count(*) FROM conversations").fetchone()[0]
        messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        memories = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        info = conn.execute("PRAGMA table_info(conversations)").fetchall()
        character_notnull = next((row[3] for row in info if row[1] == "character_id"), None)
        return {
            "conversations": conversations,
            "messages": messages,
            "memories": memories,
            "character_id_notnull": character_notnull,
        }
    finally:
        conn.close()


async def migrate(copy: Path) -> bool:
    from sqlalchemy.ext.asyncio import create_async_engine

    from database import run_migration_sequence
    from models import character, conversation, group, llm_usage, memory, message, tool_run, user_config, user_profile  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")
    try:
        async with engine.begin() as conn:
            # The copy itself is the backup — no extra file snapshot needed.
            return await run_migration_sequence(conn, before_rebuild=lambda: None)
    finally:
        await engine.dispose()


def main() -> int:
    if not SOURCE.exists():
        print(f"no database at {SOURCE} — nothing to check")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="group-migration-check-"))
    copy = tmp / SOURCE.name
    shutil.copy2(SOURCE, copy)

    before = snapshot(copy)
    print("before:", before)
    ran = asyncio.run(migrate(copy))
    after = snapshot(copy)
    print("after: ", after)
    print("rebuild ran:", ran)

    ok = (
        after["conversations"] == before["conversations"]
        and after["messages"] == before["messages"]
        and after["memories"] == before["memories"]
        and after["character_id_notnull"] == 0
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
