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
]


async def _apply_additive_migrations(conn) -> None:
    for table, column, decl in ADDITIVE_MIGRATIONS:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = {row[1] for row in result.fetchall()}
        if column not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))
            print(f"[DB] migrated: added {table}.{column}", flush=True)


async def init_db():
    """Create all tables. Call on startup."""
    from models import character, conversation, message, user_config, memory, user_profile, tool_run  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_additive_migrations(conn)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
