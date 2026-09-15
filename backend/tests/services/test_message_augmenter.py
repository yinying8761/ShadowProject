"""
MessageAugmenter — a keyword hit injects a hint, it never executes a search.

ADR-0003 (search decision rights): the keyword router is advisory only, so a
HARD/SOFT trigger match must append a lightweight hint and leave the search
decision to the main LLM. The cost of a mis-trigger has to stay at "one hint
string" — neither the web search nor the independent summary LLM call that the
old auto-search path spent (docs/specs/search-augmenter-degrade.md).

Seam: services.message_augmenter.augment(content) -> str
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import database
import services.location_service as location_service
import tools.search_tools as search_tools
from database import Base
from models.user_config import UserConfig
from services.llm_service import LLMService
from services.message_augmenter import augment


class _Recorder:
    """Records a boundary crossing and fails loudly on it."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(
            f"{self.label} was called — a keyword hit must not cost this"
        )


@pytest.fixture
def search_calls(monkeypatch) -> _Recorder:
    """The web-search backends: a hint must cost zero searches."""
    recorder = _Recorder("web search")
    monkeypatch.setattr(search_tools, "_do_search", recorder)
    return recorder


@pytest.fixture
def llm_calls(monkeypatch) -> _Recorder:
    """The LLM: the old path spent a summary call here, the hint path must not."""
    recorder = _Recorder("LLM call")
    monkeypatch.setattr(LLMService, "chat_sync", recorder)
    monkeypatch.setattr(LLMService, "stream_chat", recorder)
    return recorder


async def _session_factory(lat: float = 0.0, lng: float = 0.0):
    """In-memory user_config, so the POI stage reads a location of our choosing."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(UserConfig(id=1, location_lat=lat, location_lng=lng))
        await session.commit()
    return engine, factory


class TestSearchHint:
    @pytest.mark.asyncio
    async def test_hard_trigger_injects_hint(self, search_calls, llm_calls):
        content = "今天天气怎么样"

        result = await augment(content)

        assert result.startswith(content)       # the user's words are preserved
        assert "research" in result             # the LLM is pointed at the tool
        assert "搜索参考资料" not in result      # ...but not as if results existed
        assert search_calls.calls == []
        assert llm_calls.calls == []

    @pytest.mark.asyncio
    async def test_soft_trigger_injects_hint(self, search_calls, llm_calls):
        content = "我在学 Python 的装饰器"

        result = await augment(content)

        assert result.startswith(content)
        assert "research" in result
        assert search_calls.calls == []
        assert llm_calls.calls == []


class TestWithoutTrigger:
    @pytest.mark.asyncio
    async def test_plain_message_is_untouched(self, search_calls, llm_calls):
        content = "今天好累啊，陪我聊聊天吧"

        result = await augment(content)

        assert result == content
        assert search_calls.calls == []
        assert llm_calls.calls == []


class TestPoiStageUnchanged:
    @pytest.mark.asyncio
    async def test_food_message_still_injects_nearby_places(
        self, monkeypatch, search_calls
    ):
        engine, factory = await _session_factory(lat=39.9, lng=116.4)
        monkeypatch.setattr(database, "async_session", factory)

        async def fake_nearby(lat, lng, keywords="", radius=3000, limit=8):
            return [{
                "name": "老王面馆", "type": "餐饮", "address": "幸福路1号",
                "distance": "300", "rating": "4.6", "cost": "25",
            }]

        monkeypatch.setattr(location_service, "search_nearby_places", fake_nearby)

        try:
            result = await augment("附近有什么好吃的推荐一下")
        finally:
            await engine.dispose()

        assert "老王面馆" in result
        assert search_calls.calls == []

    @pytest.mark.asyncio
    async def test_location_lookup_failure_degrades_gracefully(self, monkeypatch):
        engine, factory = await _session_factory(lat=39.9, lng=116.4)
        monkeypatch.setattr(database, "async_session", factory)

        async def boom(*args, **kwargs):
            raise RuntimeError("amap unreachable")

        monkeypatch.setattr(location_service, "search_nearby_places", boom)

        try:
            result = await augment("晚上吃什么好呢")
        finally:
            await engine.dispose()

        assert result == "晚上吃什么好呢"
