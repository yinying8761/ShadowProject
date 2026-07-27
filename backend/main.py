"""
AI Companion — Backend Entry Point.

FastAPI server providing:
- WebSocket endpoint for streaming chat
- REST API for characters, conversations, config
- LLM agent orchestration with tool calling
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dotenv import load_dotenv

# Check backend/.env first, then project root .env
backend_dir = Path(__file__).parent
for env_path in [backend_dir / ".env", backend_dir.parent / ".env"]:
    if env_path.exists():
        load_dotenv(env_path)
        break

from config import settings
from database import init_db, async_session
from models.user_config import UserConfig
from models.character import CharacterProfile
from sqlalchemy import select


# ---- Tool Registration ----

def register_tools(registry=None):
    """Register all available tools for the Agent.

    Parameters
    ----------
    registry:
        Optional ToolRegistry instance.  When *None* the module-level
        singleton is used — this is the production path.  Tests may pass
        an isolated instance to avoid mutating global state.
    """
    if registry is None:
        from core.tool_registry import tool_registry as registry
    from tools.file_tools import read_file, write_file, list_directory, search_files
    from tools.screen_tools import see_screen
    from tools.time_tools import get_current_time

    registry.register(
        name="read_file",
        description="Read the contents of a file on the user's computer.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path"],
        },
        handler=read_file,
        require_approval=False,
    )

    registry.register(
        name="write_file",
        description="Write content to a file. Creates parent directories if needed.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path where to write"},
                "content": {"type": "string", "description": "Content to write"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        require_approval=True,
    )

    registry.register(
        name="list_directory",
        description="List files and subdirectories in a directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list", "default": "."},
            },
            "required": [],
        },
        handler=list_directory,
        require_approval=False,
    )

    registry.register(
        name="search_files",
        description="Search for files by name glob pattern recursively.",
        parameters={
            "type": "object",
            "properties": {
                "root_path": {"type": "string", "description": "Root directory to search"},
                "pattern": {"type": "string", "description": "File pattern, e.g. '*.py'"},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["root_path", "pattern"],
        },
        handler=search_files,
        require_approval=False,
    )

    registry.register(
        name="get_current_time",
        description="Get the current local date, time, timezone, and weekday.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=get_current_time,
        require_approval=False,
    )

    registry.register(
        name="see_screen",
        description=(
            "Capture the user's screen and get a short text description of what's "
            "currently displayed. Use this when the user asks you to look at their "
            "screen, refers to something they're doing, or you need visual context. "
            "Returns a brief description — you do not see the image directly."
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": (
                        "Optional aspect to focus on, e.g. '用户在玩什么游戏', "
                        "'屏幕上有报错吗', '当前打开的是什么文件'. Leave empty for a general description."
                    ),
                },
                "monitor": {
                    "type": "integer",
                    "description": "Monitor index: 0 = all monitors combined, 1 = primary, 2+ = others.",
                    "default": 0,
                },
            },
            "required": [],
        },
        handler=see_screen,
        require_approval=True,
    )

    from tools.search_tools import fetch_url, research
    from tools.memory_tools import search_memory, save_memory

    registry.register(
        name="fetch_url",
        description=(
            "Fetch and extract text content from a URL. Use this when the user "
            "sends a link and asks you to read it, or when you need to get the "
            "full content of a specific web page."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch, e.g. 'https://example.com/article'",
                },
                "extract_text": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, extract readable text from HTML. Set to false for raw content.",
                },
            },
            "required": ["url"],
        },
        handler=fetch_url,
        require_approval=False,
    )

    registry.register(
        name="research",
        description=(
            "Search the web and get a concise AI-summarized answer with source citations. "
            "Use this when the user asks a question you don't know the answer to, "
            "or when you need current information beyond your knowledge cutoff. "
            "Returns a structured answer with sources and confidence level."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, e.g. 'Python 3.14 new features'",
                },
            },
            "required": ["query"],
        },
        handler=research,
        require_approval=False,
    )

    registry.register(
        name="search_memory",
        description=(
            "Search your long-term memory for facts and information about the user. "
            "Use this when you need to recall something the user told you before, "
            "or when the user asks if you remember something about them."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for, e.g. 'user job', 'pet name', 'favorite food'",
                },
                "limit": {
                    "type": "integer",
                    "default": 3,
                    "description": "Max number of memories to return",
                },
            },
            "required": ["query"],
        },
        handler=search_memory,
        require_approval=False,
    )

    registry.register(
        name="save_memory",
        description=(
            "以第一人称日记体记录关于用户的重要信息。当用户明确要求记住某事，"
            "或当你了解到关于他们的高度个人化或重要的信息时使用。"
            "用你的视角写，对用户的称呼根据对话氛围自然选择。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "日记体记忆内容，例如 '今天用户告诉我他的猫叫麻薯，他说它特别爱吃鸡肉条'",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["user_fact", "user_preference", "important_event"],
                    "default": "user_fact",
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                    "description": "1=琐碎, 10=高度个人化/情感上重要",
                },
            },
            "required": ["content"],
        },
        handler=save_memory,
        require_approval=False,
    )

    return registry


register_tools()


# ---- Lifespan & Seed ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Start GPT-SoVITS TTS API (spawn, don't block startup) ──
    from services.tts_service import start_api
    if settings.tts_ref_base:
        start_api()

    await init_db()
    from services.memory_service import _ensure_fts5, memory_service
    await _ensure_fts5()
    try:
        pruned = await memory_service.prune()
        if pruned:
            print(f"[Startup] pruned {pruned} stale memories", flush=True)
    except Exception as e:
        print(f"[Startup] memory prune failed: {e}", flush=True)
    await seed_default_data()
    yield
    from services.tts_service import stop_api
    stop_api()


async def seed_default_data():
    """Create default character and config on first launch."""
    from core.prompt_manager import PromptManager

    async with async_session() as session:
        config = await session.get(UserConfig, 1)
        if config is None:
            config = UserConfig(id=1)
            session.add(config)

        result = await session.execute(select(CharacterProfile).limit(1))
        if result.scalar_one_or_none() is None:
            pm = PromptManager()
            for char_data in pm.default_characters():
                char = CharacterProfile(
                    name=char_data["name"],
                    gender=char_data.get("gender"),
                    personality=char_data["personality"],
                    role=char_data["role"],
                    archetype=char_data["archetype"],
                    voice_style=char_data.get("voice_style"),
                )
                session.add(char)

        await session.commit()


# ---- FastAPI App ----

app = FastAPI(
    title="AI Companion",
    description="Your desktop AI companion — chat, tools, and more.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:16173", "http://localhost:8711"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

data_dir = settings.resolve_data_dir()
app.mount("/data", StaticFiles(directory=str(data_dir)), name="data")

from api.chat import router as chat_router
from api.character import router as character_router
from api.conversation import router as conversation_router
from api.config import router as config_router
from api.tts import router as tts_router

app.include_router(chat_router)
app.include_router(character_router)
app.include_router(conversation_router)
app.include_router(config_router)
app.include_router(tts_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
