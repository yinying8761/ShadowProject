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
    # 无 ondelete=（ADR-0005）：删角色时由 api/character.py 显式删掉成员资格；
    # 因此变成 0 成员的群会保留下来（可再编辑加人）。删群是显式操作：
    # `DELETE /api/groups/{id}`（api/group.py）会删群、成员资格与它的全部群对话。
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id"),
        nullable=False,
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("character_profiles.id"),
        nullable=False,
    )
    # 发言顺序必填：顺序即群轮发言次序，不允许隐式的全 0 并列默认值。
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    group: Mapped["Group"] = relationship("Group", back_populates="members")
