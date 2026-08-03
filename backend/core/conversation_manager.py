from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from models.message import Message
from models.conversation import Conversation


class ConversationManager:
    """Manages conversation history with sliding window context."""

    def __init__(self, max_tokens: int = 16000):
        self.max_tokens = max_tokens

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimation: ~4 chars per token for CJK, ~3.5 for mixed."""
        if not text:
            return 0
        cjk_chars = sum(1 for c in text if '一' <= c <= '鿿' or '　' <= c <= '〿')
        other_chars = len(text) - cjk_chars
        return int(cjk_chars * 0.6 + other_chars * 0.25) or 1

    async def get_context_messages(
        self, session: AsyncSession, conversation_id: str, max_tokens: int | None = None
    ) -> list[dict]:
        """Load recent messages, trim oldest to fit within token limit."""
        limit = max_tokens or self.max_tokens

        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
        )
        result = await session.execute(query)
        messages = result.scalars().all()

        context: list[dict] = []
        total_tokens = 0

        for msg in reversed(messages):
            tokens = self.estimate_tokens(msg.content)
            if total_tokens + tokens > limit and context:
                break
            context.append({
                "role": msg.role,
                "content": msg.content,
            })
            total_tokens += tokens

        return context

    async def add_message(
        self,
        session: AsyncSession,
        conversation_id: str,
        role: str,
        content: str,
        tool_calls: dict | None = None,
        tool_call_id: str | None = None,
    ) -> Message:
        """Persist a message and update conversation timestamp."""
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=self.estimate_tokens(content),
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        session.add(msg)

        conv = await session.get(Conversation, conversation_id)
        if conv:
            from sqlalchemy import update
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=func.now())
            )

        await session.commit()
        await session.refresh(msg)
        return msg

    async def get_or_create_conversation(
        self, session: AsyncSession, character_id: str, title: str = "New Conversation"
    ) -> Conversation:
        """Get the most recent conversation or create a new one."""
        query = (
            select(Conversation)
            .where(Conversation.character_id == character_id)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        result = await session.execute(query)
        conv = result.scalar_one_or_none()

        if conv is None:
            conv = Conversation(character_id=character_id, title=title)
            session.add(conv)
            await session.commit()
            await session.refresh(conv)

        return conv

    async def count_messages(
        self, session: AsyncSession, conversation_id: str
    ) -> int:
        """Return the total number of messages in a conversation."""
        result = await session.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id
            )
        )
        return result.scalar() or 0

    async def summarize_and_trim(
        self,
        session: AsyncSession,
        conversation_id: str,
        keep_count: int = 12,
        llm_service=None,
    ):
        """
        Summarize old messages beyond keep_count via LLM, merge with existing
        conversation summary, then delete the summarized messages.
        """
        total = await self.count_messages(session, conversation_id)
        if total <= keep_count:
            return {"deleted": 0, "summary": ""}

        # Get old messages (oldest first, beyond keep_count)
        subquery = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(total - keep_count)
        )
        result = await session.execute(subquery)
        old_messages = result.scalars().all()

        if not old_messages:
            return {"deleted": 0, "summary": ""}

        # Build transcript
        lines = []
        for msg in old_messages:
            role_label = "用户" if msg.role == "user" else "角色"
            lines.append(f"[{role_label}]: {msg.content or '(tool)'}")
        transcript = "\n".join(lines)

        # Load existing summary
        conv = await session.get(Conversation, conversation_id)
        existing_summary = conv.summary or "" if conv else ""

        # Call LLM for summarization (if available)
        new_summary = ""
        if llm_service:
            try:
                summary_prompt = (
                    "把以下对话片段总结成一小段话（3-5句）。"
                    "抓住要点：聊了什么话题、用户分享了什么个人信息、做了什么决定、"
                    "当时的情绪氛围、以及以后继续这段关系时应该记住的事情。"
                    "用和对话一致的语言写，保留对话的温度——不要像写报告一样冷冰冰的。\n\n"
                )
                if existing_summary:
                    summary_prompt += f"之前的摘要（把新内容融进去）：\n{existing_summary}\n\n"
                summary_prompt += f"新的对话片段：\n{transcript}"

                messages = [
                    {"role": "system", "content": "你是对话记录员。用温暖、自然的语言总结对话，保留情感温度。只输出摘要段落。"},
                    {"role": "user", "content": summary_prompt},
                ]
                new_summary = await llm_service.chat_sync(messages, max_tokens=512, temperature=0.3)
            except Exception as e:
                print(f"[ConvManager] summarization failed: {e}", flush=True)
                new_summary = existing_summary

        # Store summary on conversation
        if conv and new_summary:
            conv.summary = new_summary

        # Delete old messages
        old_ids = [m.id for m in old_messages]
        await session.execute(delete(Message).where(Message.id.in_(old_ids)))
        await session.commit()
        print(
            f"[ConvManager] summarized + trimmed {len(old_ids)} messages, "
            f"summary_len={len(new_summary)}",
            flush=True,
        )
        return {"deleted": len(old_ids), "summary": new_summary}
