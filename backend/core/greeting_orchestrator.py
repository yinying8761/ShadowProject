"""
Daily greeting orchestrator — handles the full once-per-day greeting flow.

Receives pre-gathered context (location, weather, memories, days_since_last)
and orchestrates: already-greeted check → agent greeting generation → mark.
All events are yielded through the `send_json` callback; this module has no
direct WebSocket dependency.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.agent import Agent
from models.character import CharacterProfile


class GreetingOrchestrator:
    """Orchestrate the once-per-day greeting for a character."""

    async def run(
        self,
        session: AsyncSession,
        char_id: str,
        conv_id: str,
        agent: Agent,
        send_json,
        *,
        location: dict | None = None,
        weather: dict | None = None,
        days_since_last: int = 0,
        memories: list[str] | None = None,
    ) -> None:
        """
        Run the daily greeting flow.

        Checks whether the character already greeted today; if so, sends a
        skip event and returns.  Otherwise calls the agent to generate a
        greeting and marks the character if any content was produced.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # ── Already-greeted check ──────────────────────────────────
        char = await session.get(CharacterProfile, char_id)
        if char and char.last_daily_greeting_date == today:
            print(
                f"[GreetingOrchestrator] char {char.name} already greeted today, skip",
                flush=True,
            )
            await send_json({"type": "daily_greeting_skip", "reason": "already_greeted"})
            return

        # ── Generate greeting via agent ────────────────────────────
        had_content = False
        async for event in agent.run(
            session=session,
            user_message=None,
            conversation_id=conv_id,
            character_id=char_id,
            mode="greeting",
            extra_context={
                "location": location,
                "weather": weather,
                "days_since_last": days_since_last,
                "memories": memories or [],
            },
        ):
            if event.get("type") == "token":
                had_content = True
            await send_json(event)

        # ── Mark character as greeted today (only if content) ──────
        if had_content:
            char_prof = await session.get(CharacterProfile, char_id)
            if char_prof:
                char_prof.last_daily_greeting_date = today
            await session.commit()
            print(
                f"[GreetingOrchestrator] marked char={char_id} greeted={today}",
                flush=True,
            )
        else:
            print("[GreetingOrchestrator] no content produced, not marking", flush=True)
