# 02 — llm_usage 落表 + Agent 每轮写入 + GET /api/token-usage

**What to build:** 新增 `LLMUsage` 模型/表 + `LLMUsageStore` 异步写入；`Agent` 每轮
`stream_chat` 前预计算、结束后落库；新增 `GET /api/token-usage` 端点（逐轮 + 聚合）。

**Blocked by:** #01（需要 `estimate_prompt_tokens()` 和 `usage` 事件）

**Status:** ready-for-agent

**关键语义：**

- `llm_usage` 是**新表**，靠 `init_db()` 导入模型后 `create_all` 建表，**不要**加
  `ADDITIVE_MIGRATIONS`（那是给已有表加列用的）。
- 只统计 Agent 主循环（`stream_chat`）；`chat_sync`（后台摘要/记忆提取）不统计。
- 落库在每轮流结束后、工具执行前进行；`usage` 事件缺失（流中断）时该轮不落库。

## Checklist

- [ ] 新建 `backend/models/llm_usage.py`：

```python
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    round_num: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

- [ ] `database.py` 的 `init_db()` 导入列表加 `llm_usage`（`from models import ... llm_usage`）
- [ ] 新建 `backend/services/usage_store.py`（`session_factory` 可注入，仿 `ToolTraceStore`）：

```python
class LLMUsageStore:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    async def _get_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from database import async_session
        return async_session()

    async def save(self, *, conversation_id, round_num, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   estimated_prompt_tokens):
        from models.llm_usage import LLMUsage
        row = LLMUsage(
            conversation_id=conversation_id, round_num=round_num, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total_tokens, estimated_prompt_tokens=estimated_prompt_tokens,
        )
        async with await self._get_session() as session:
            session.add(row)
            await session.commit()
        return row
```

- [ ] `core/agent.py` 构造函数新增 `usage_store=None` 注入缝 + 懒创建 helper（`_get_usage_store()`）
- [ ] `Agent.run()` 的 chat 循环里，每轮：先 `estimated = await self.llm_service.estimate_prompt_tokens(messages)`，
      循环中加 `elif event["type"] == "usage": usage = event`，流结束后（在 `full_response += round_text`
      之前）若 `usage` 非空则 `await self._get_usage_store().save(...)`
- [ ] 新建 `backend/api/token_usage.py`：`GET /api/token-usage?conversation_id=xxx&limit=50&offset=0`
      → `{"total", "usage": [...], "summary": {"rounds","prompt_tokens","completion_tokens","total_tokens"}}`
      （`usage` 按 `created_at` 倒序；`summary` 为该会话**全部**记录的聚合求和，不受分页影响）
- [ ] `main.py` 注册 token_usage 路由
- [ ] 手动冒烟：跑一次真实对话 → `curl "http://localhost:8722/api/token-usage?conversation_id=xxx"`
      返回逐轮记录 + 正确的 summary 聚合
