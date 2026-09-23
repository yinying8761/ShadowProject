import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, JSON, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # 无 ondelete=（ADR-0005）：删除走 ORM 的 "all, delete-orphan" 关系级联
    # 与 api/ 里的显式 delete，不依赖 DB 强制的级联。
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 发言角色（群聊用）：该条消息由哪个角色说出。1:1 保持空——
    # 说话人由会话角色派生（见 core/transcript.py）。可空列，走增量迁移。
    # 无 ondelete=（ADR-0005）：角色被删时由 api/character.py 显式置 NULL。
    speaker_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("character_profiles.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )
