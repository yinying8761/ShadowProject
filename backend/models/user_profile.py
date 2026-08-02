import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class UserProfile(Base):
    """Per-character user profile. character_id=NULL is the default fallback."""

    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    character_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("character_profiles.id", ondelete="CASCADE"),
        nullable=True,
    )  # NULL = default profile (fallback)
    user_name: Mapped[str] = mapped_column(String(50), nullable=False, default="User")
    user_gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    user_occupation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_relationship: Mapped[str] = mapped_column(String(50), nullable=False, default="friend")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
