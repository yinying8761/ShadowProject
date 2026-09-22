import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Group(Base):
    """群：固定角色集合 + 共享对话流；一个群可开多条群对话（spec: group-chat）。"""

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    members: Mapped[list["GroupMember"]] = relationship(
        "GroupMember", back_populates="group", cascade="all, delete-orphan",
        order_by="GroupMember.position",
    )


class GroupMember(Base):
    """群成员：发言顺序（position）即群轮发言次序；固定成员集合（唯一约束）。"""

    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "character_id", name="uq_group_member"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("character_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 发言顺序必填：顺序即群轮发言次序，不允许隐式的全 0 并列默认值。
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    group: Mapped["Group"] = relationship("Group", back_populates="members")
