import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Conversation(Base):
    __tablename__ = "conversations"

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
    title: Mapped[str] = mapped_column(String(200), default="New Conversation")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
