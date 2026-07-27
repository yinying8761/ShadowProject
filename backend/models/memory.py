import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, LargeBinary, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base

# Source constants — where a memory came from
SOURCE_USER_STATED = "user_stated"    # user explicitly stated or asked to remember
SOURCE_AI_SUMMARIZED = "ai_summarized"  # AI extracted or decided to save


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    memory_type: Mapped[str] = mapped_column(
        String(30), default="user_fact"
    )  # user_fact | user_preference | important_event
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    importance: Mapped[int] = mapped_column(Integer, default=5)
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    character_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("character_profiles.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(20), default=SOURCE_AI_SUMMARIZED
    )  # SOURCE_USER_STATED | SOURCE_AI_SUMMARIZED
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
