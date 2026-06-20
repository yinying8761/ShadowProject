import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class CharacterProfile(Base):
    __tablename__ = "character_profiles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    personality: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(200), default="companion")
    archetype: Mapped[str] = mapped_column(String(200), default="friend")
    voice_style: Mapped[str | None] = mapped_column(String(100), nullable=True)
    system_prompt_template: Mapped[str] = mapped_column(Text, default="")
    avatar_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
