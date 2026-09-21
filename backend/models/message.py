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
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 发言角色（群聊用）：该条消息由哪个角色说出。1:1 保持空——
    # 说话人由会话角色派生（见 core/transcript.py）。可空列，走增量迁移。
    speaker_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("character_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )
