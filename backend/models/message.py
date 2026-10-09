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
    # 会话内**单调递增序号**（ticket #56 / ADR-0009）：`created_at` 是 SQLite 秒级
    # 精度，并发写入同秒的多条消息会并列，"谁先说的"因此不确定 —— 顺序以本列为准。
    # 可空只为增量迁移；写入一律由 ConversationManager.add_message 分配（写锁下取
    # max+1），老库由 database.backfill_message_sequence 回填。
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )

    @staticmethod
    def chronological_order(*, newest_first: bool = False):
        """读路径**唯一**的排序键：先序号，再 `created_at`。

        顺序以序号为准、**不再**用时间戳（ADR-0009）；时间戳只作同一批之内的兜底，
        给整表都没有序号的行（迁移前的库、手写的测试夹具）退回改造前的按时间排。
        无序号行视为 0 号（"所有有序行之前"）—— 正常库里一条都不会有：写入一律在
        `add_message` 里分配，启动时的回填也保证老库不留未编号的行。
        """
        key = func.coalesce(Message.seq, 0)
        if newest_first:
            return (key.desc(), Message.created_at.desc())
        return (key.asc(), Message.created_at.asc())
