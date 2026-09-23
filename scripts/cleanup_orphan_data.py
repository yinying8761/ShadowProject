"""一次性清理历史孤儿数据（群聊审查 §B4）。

取证与成因见 `docs/analysis/group-chat-review-and-orphan-data.md` §B2：聊天接口曾
接受不存在的 `conversation_id`，删除角色又从不级联（模型里的 `ondelete=` 在 SQLite 上
无效 —— ADR-0005），于是留下孤儿会话/消息与悬空 memories。产生它们的路径已经修好，
这里清存量。

用法：
    python scripts/cleanup_orphan_data.py --dry-run   # 只报告，最后回滚
    python scripts/cleanup_orphan_data.py             # 真删（先备份）

- 真删前先把 `data/companion.db` 复制成 `companion.db.bak-orphans-<ts>`。
- 全部步骤在**一个事务**里执行，结尾用 `PRAGMA foreign_key_check` 自证（期望 0 行）。
- `memory_fts` 由 `memories` 上的 AFTER DELETE 触发器同步（`memory_store.ensure_fts5`
  建立），脚本额外断言索引里没有指向已删行的残留 rowid。
- 刻意不动 `llm_usage.conversation_id`：它是按会话记账的历史日志，删会话不该抹掉
  用量记录（ADR-0005）。
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "companion.db"

# (说明, 需要存在的表, SQL)。顺序有意：先删父行，再清指向"已不存在的父行"的子行。
CLEANUP_STEPS: list[tuple[str, str, str]] = [
    (
        "孤儿会话（character_id 指向已不存在的角色）",
        "conversations",
        "DELETE FROM conversations WHERE character_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM character_profiles p WHERE p.id = conversations.character_id)",
    ),
    (
        "孤儿消息（conversation_id 指向已不存在的会话）",
        "messages",
        "DELETE FROM messages WHERE NOT EXISTS ("
        "  SELECT 1 FROM conversations c WHERE c.id = messages.conversation_id)",
    ),
    (
        "memories 的来源会话已不存在 → SET NULL（内容保留）",
        "memories",
        "UPDATE memories SET source_conversation_id = NULL"
        " WHERE source_conversation_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM conversations c WHERE c.id = memories.source_conversation_id)",
    ),
    (
        "角色已不存在时的悬空 memories → 删除",
        "memories",
        "DELETE FROM memories WHERE character_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM character_profiles p WHERE p.id = memories.character_id)",
    ),
    (
        "角色已不存在时的 messages.speaker_id → NULL",
        "messages",
        "UPDATE messages SET speaker_id = NULL"
        " WHERE speaker_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM character_profiles p WHERE p.id = messages.speaker_id)",
    ),
    (
        "角色已不存在时的悬空群成员资格 → 删除",
        "group_members",
        "DELETE FROM group_members WHERE NOT EXISTS ("
        "  SELECT 1 FROM character_profiles p WHERE p.id = group_members.character_id)",
    ),
    (
        "memory_fts 里指向已删 memories 的残留索引行 → 删除",
        "memory_fts",
        "DELETE FROM memory_fts WHERE rowid NOT IN (SELECT rowid FROM memories)",
    ),
    (
        "角色已不存在时的悬空专属画像 → 删除",
        "user_profiles",
        "DELETE FROM user_profiles WHERE character_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM character_profiles p WHERE p.id = user_profiles.character_id)",
    ),
]

ORPHAN_CHECKS: list[tuple[str, str]] = [
    ("orphan_conversations", "SELECT count(*) FROM conversations WHERE character_id IS NOT NULL AND NOT EXISTS ("
                             "  SELECT 1 FROM character_profiles p WHERE p.id = conversations.character_id)"),
    ("orphan_messages", "SELECT count(*) FROM messages WHERE NOT EXISTS ("
                        "  SELECT 1 FROM conversations c WHERE c.id = messages.conversation_id)"),
    ("dangling_memories", "SELECT count(*) FROM memories WHERE character_id IS NOT NULL AND NOT EXISTS ("
                          "  SELECT 1 FROM character_profiles p WHERE p.id = memories.character_id)"),
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _unmigrated_columns(conn: sqlite3.Connection) -> list[str]:
    """本脚本假定库已经跑过群聊迁移；否则先让它跑（`init_db`，即启动一次后端）。"""
    return [
        f"{table}.{column}"
        for table, column in (("messages", "speaker_id"), ("conversations", "group_id"))
        if _table_exists(conn, table) and not _has_column(conn, table, column)
    ]


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def snapshot(conn: sqlite3.Connection) -> dict:
    return {
        "conversations": _count(conn, "SELECT count(*) FROM conversations"),
        "messages": _count(conn, "SELECT count(*) FROM messages"),
        "memories": _count(conn, "SELECT count(*) FROM memories"),
        "foreign_key_check": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        **{name: _count(conn, sql) for name, sql in ORPHAN_CHECKS},
    }


def _print_snapshot(label: str, snap: dict) -> None:
    print(f"[cleanup] {label}: " + ", ".join(f"{k}={v}" for k, v in snap.items()))


def _fts_residue(conn: sqlite3.Connection) -> int | None:
    """memory_fts 里指向已删 memories 行的 rowid 数（None = 没有 FTS 表）。"""
    if not _table_exists(conn, "memory_fts"):
        return None
    return _count(
        conn,
        "SELECT count(*) FROM memory_fts WHERE rowid NOT IN (SELECT rowid FROM memories)",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="清理历史孤儿数据（一次性的存量修复）")
    parser.add_argument("--dry-run", action="store_true", help="只报告改动并回滚，不落盘")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"no database at {DB_PATH} — nothing to clean")
        return 0

    if not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = DB_PATH.with_name(f"{DB_PATH.name}.bak-orphans-{stamp}")
        shutil.copy2(DB_PATH, backup)
        print(f"[cleanup] backup -> {backup}")

    conn = sqlite3.connect(DB_PATH)
    try:
        # 从不开启 FK 强制（ADR-0005），这里显式声明一次，免得被"外键会拦"误导。
        conn.execute("PRAGMA foreign_keys=OFF")

        unmigrated = _unmigrated_columns(conn)
        if unmigrated:
            print(
                "[cleanup] 数据库尚未跑群聊迁移（缺 "
                + ", ".join(unmigrated)
                + "）：先启动一次后端让 init_db 迁移，再运行本脚本。"
            )
            return 1

        before = snapshot(conn)
        _print_snapshot("before", before)
        residues_before = _fts_residue(conn)

        for label, table, sql in CLEANUP_STEPS:
            if not _table_exists(conn, table):
                print(f"  skip (no {table} table): {label}")
                continue
            print(f"  {label}: {conn.execute(sql).rowcount} 行")

        after = snapshot(conn)
        residues_after = _fts_residue(conn)
        _print_snapshot("after ", after)
        if residues_after is not None:
            print(f"[cleanup] memory_fts 残留 rowid: {residues_before} -> {residues_after}")

        clean = after["foreign_key_check"] == 0 and all(
            after[name] == 0 for name, _ in ORPHAN_CHECKS
        )
        if residues_after not in (None, 0):
            clean = False

        if args.dry_run:
            conn.rollback()
            print("[cleanup] dry-run：已回滚，未改动数据库")
        else:
            conn.commit()
        print("PASS" if clean else "FAIL")
        return 0 if clean else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
