import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from config import settings

DATA_DIR = settings.resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "companion.db"

engine = create_async_engine(
    f"sqlite+aiosqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Lightweight column-level migrations for SQLite.
# SQLAlchemy's create_all() only creates missing tables, never alters
# existing ones. We probe each known column and ALTER TABLE ADD if absent.
ADDITIVE_MIGRATIONS = [
    ("user_config", "show_floating_icon", "BOOLEAN DEFAULT 1"),
    ("user_config", "floating_icon_x", "INTEGER DEFAULT -1"),
    ("user_config", "floating_icon_y", "INTEGER DEFAULT -1"),
    ("user_config", "proactive_chat_level", "VARCHAR(10) DEFAULT 'medium'"),
    ("user_config", "proactive_silent_tool_approval", "BOOLEAN DEFAULT 0"),
    ("user_config", "proactive_auto_see_screen", "BOOLEAN DEFAULT 1"),
    ("user_config", "proactive_daily_limit", "INTEGER DEFAULT 10"),
    ("user_config", "proactive_fixed_schedule_enabled", "BOOLEAN DEFAULT 1"),
    ("user_config", "proactive_system_notification", "BOOLEAN DEFAULT 1"),
    ("user_config", "proactive_daily_count", "INTEGER DEFAULT 0"),
    ("user_config", "proactive_state_date", "VARCHAR(20) DEFAULT ''"),
    ("user_config", "proactive_scheduled_slots", "VARCHAR(100) DEFAULT ''"),
    ("user_config", "language", "VARCHAR(10) DEFAULT 'zh'"),
    ("user_config", "last_daily_greeting_at", "DATETIME"),
    ("user_config", "last_daily_greeting_date", "VARCHAR(20) DEFAULT ''"),
    ("user_config", "location_city", "VARCHAR(100) DEFAULT ''"),
    ("user_config", "location_country", "VARCHAR(100) DEFAULT ''"),
    ("user_config", "location_weather", "VARCHAR(200) DEFAULT ''"),
    ("user_config", "location_lat", "REAL DEFAULT 0.0"),
    ("user_config", "location_lng", "REAL DEFAULT 0.0"),
    ("user_config", "tts_enabled", "BOOLEAN DEFAULT 1"),
    ("user_config", "last_character_id", "VARCHAR(36)"),
    ("character_profiles", "tts_ref_audio", "VARCHAR(500)"),
    ("character_profiles", "tts_prompt_text", "TEXT"),
    ("character_profiles", "last_daily_greeting_date", "VARCHAR(20) DEFAULT ''"),
    ("conversations", "summary", "TEXT"),
    ("memories", "character_id", "VARCHAR(36) REFERENCES character_profiles(id)"),
    ("memories", "source", "VARCHAR(20) DEFAULT 'ai_summarized'"),
    ("tool_runs", "retry_count", "INTEGER DEFAULT 0"),
    # 群聊（spec: group-chat Phase 1）：该条消息的发言角色；1:1 保持 NULL。
    # 不写 ON DELETE：SQLite 上 FK 强制从不开启（ADR-0005），声明级联只是装饰；
    # 置空/删除由服务层显式完成（api/character.py）。
    ("messages", "speaker_id", "VARCHAR(36) REFERENCES character_profiles(id)"),
    # 群聊（spec: group-chat Phase 2）：会话的群归属与"打开时补账"锚点。
    # groups 表由 create_all 先建，故此处的 REFERENCES 成立。
    ("conversations", "group_id", "VARCHAR(36) REFERENCES groups(id)"),
    ("conversations", "last_extract_at", "DATETIME"),
]


async def _apply_additive_migrations(conn) -> None:
    for table, column, decl in ADDITIVE_MIGRATIONS:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = {row[1] for row in result.fetchall()}
        if column not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))
            print(f"[DB] migrated: added {table}.{column}", flush=True)


def _backup_db_before_group_migration() -> None:
    """重建迁移是破坏性的：先对 DB 文件做一次快照备份；备份失败则中止迁移。"""
    if not DB_PATH.exists():
        return  # 内存库或尚无文件，无需备份
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = DB_PATH.with_name(f"{DB_PATH.name}.bak-group-{stamp}")
    shutil.copy2(DB_PATH, backup)
    print(f"[DB] backup before group migration -> {backup}", flush=True)


async def conversations_need_group_rebuild(conn) -> bool:
    """老库探测：conversations.character_id 是否仍为 NOT NULL（需要重建）。"""
    result = await conn.execute(text("PRAGMA table_info(conversations)"))
    rows = result.fetchall()
    if not rows:
        return False  # 表尚不存在（全新库）
    char = next((row for row in rows if row[1] == "character_id"), None)
    return char is not None and bool(char[3])


async def ensure_group_schema(conn) -> bool:
    """conversations.character_id NOT NULL → nullable 的一次性重建迁移（群聊，#51）。

    偏离"只加列"惯例：SQLite 无法去掉 NOT NULL 约束，必须重建表
    （建新表 → 拷数据 → 换名）。决策记录见 docs/adr/0004-conversations-character-id-nullable.md。

    - 幂等：character_id 已可空（或表尚不存在）时不做任何事，返回 False。
    - 必须在 _apply_additive_migrations 之后调用：重建的 INSERT..SELECT
      依赖 additive 已补齐 group_id / last_extract_at。
    - 前置条件：PRAGMA foreign_keys=OFF（FK=ON 时 DROP TABLE 退化为隐式 DELETE，
      触发 messages 的 ON DELETE CASCADE）——检测到开启则拒绝执行。
    - 整个重建在调用方事务里执行，任何一步失败即整体回滚。
    - 备份是 init_db 的职责，且在迁移事务**之外**完成（快照必须是未被本事务
      触碰的干净文件）——这里不提供回调钩子。
    """
    if not await conversations_need_group_rebuild(conn):
        return False

    fk_on = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
    if fk_on:
        raise RuntimeError(
            "conversations rebuild requires PRAGMA foreign_keys=OFF: with FK "
            "enforcement DROP TABLE degrades to a cascading implicit DELETE on "
            "messages (see docs/adr/0004-conversations-character-id-nullable.md)"
        )

    # 新表 DDL 与 models.conversation 逐列一致（列序、NOT NULL、默认值）——
    # test_group_migration 的 parity 测试锁定这一点，改模型必须同步改这里。
    await conn.execute(text(
        "CREATE TABLE conversations_new ("
        " id VARCHAR(36) NOT NULL PRIMARY KEY,"
        " character_id VARCHAR(36) REFERENCES character_profiles(id),"
        " group_id VARCHAR(36) REFERENCES groups(id),"
        " last_extract_at DATETIME,"
        " title VARCHAR(200) NOT NULL,"
        " summary TEXT,"
        " created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,"
        " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
    ))
    await conn.execute(text(
        "INSERT INTO conversations_new"
        " (id, character_id, group_id, last_extract_at, title, summary, created_at, updated_at)"
        " SELECT id, character_id, group_id, last_extract_at, title, summary, created_at, updated_at"
        " FROM conversations"
    ))
    count = (await conn.execute(text("SELECT count(*) FROM conversations_new"))).scalar()
    await conn.execute(text("DROP TABLE conversations"))
    await conn.execute(text("ALTER TABLE conversations_new RENAME TO conversations"))
    print(
        f"[DB] rebuilt conversations: character_id -> nullable, copied {count} rows",
        flush=True,
    )
    return True


async def run_migration_sequence(conn) -> bool:
    """唯一权威的迁移顺序：create_all → additive → 群重建（ADR-0004）。

    init_db、测试与 scripts/check_group_migration.py 都走这里，
    避免迁移顺序被多处复述后漂移。
    """
    await conn.run_sync(Base.metadata.create_all)
    await _apply_additive_migrations(conn)
    return await ensure_group_schema(conn)


async def init_db():
    """Create all tables. Call on startup."""
    from models import character, conversation, group, message, user_config, memory, user_profile, tool_run, llm_usage  # noqa: F401

    # 探测与备份在迁移事务之外：快照必须是未被本事务触碰的干净文件（ADR-0004）。
    async with engine.connect() as conn:
        needs_rebuild = await conversations_need_group_rebuild(conn)
    if needs_rebuild:
        await asyncio.to_thread(_backup_db_before_group_migration)

    async with engine.begin() as conn:
        await run_migration_sequence(conn)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
