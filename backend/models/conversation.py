import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base
from models.message import Message


class Conversation(Base):
    __tablename__ = "conversations"

    #: 默认标题的**唯一来源**：列默认值、`ConversationManager.is_default_title`
    #: 与 API 下发的 `is_default_title` 判定都取它（审查 S6）。
    DEFAULT_TITLE = "New Conversation"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # 1:1 会话指向其角色；群对话没有单一角色（NULL，见 ADR-0004）。
    # 刻意不写 ondelete=：SQLite 上 FK 强制从不开启（ADR-0005），声明级联只会
    # 骗人 —— 引用完整性由服务层显式清理（api/character.py）。
    character_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("character_profiles.id"), nullable=True,
    )
    # 群对话归属：有值即群对话（1:1 为 NULL，行为不变）。
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("groups.id"), nullable=True,
    )
    # "打开时补账"锚点：群记忆提取最后覆盖到的消息时间。
    last_extract_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default=DEFAULT_TITLE)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan",
        # 与全项目**同一个**排序键（`Message.chronological_order()`）：序号优先、created_at
        # 只作无序号老行的兜底。这里绝不能再手写一份（比如 "Message.seq, Message.created_at"）——
        # 第二份副本正是"同一段对话在两处顺序不一致"的来源（CONTEXT.md §6 / ADR-0009；
        # 2026-10-10 审查 S3）。
        order_by=Message.chronological_order(),
    )
