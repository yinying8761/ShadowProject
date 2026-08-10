from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models.user_profile import UserProfile

router = APIRouter(prefix="/api/user-profile", tags=["user-profile"])


class UserProfileRequest(BaseModel):
    user_name: str
    user_gender: str | None = None
    user_occupation: str | None = None
    user_bio: str | None = None
    user_relationship: str = "friend"


def _profile_to_dict(profile: UserProfile) -> dict:
    """Serialize a UserProfile ORM object to a JSON-safe dict."""
    return {
        "id": profile.id,
        "character_id": profile.character_id,
        "user_name": profile.user_name,
        "user_gender": profile.user_gender,
        "user_occupation": profile.user_occupation,
        "user_bio": profile.user_bio,
        "user_relationship": profile.user_relationship,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


async def _get_profile(
    session: AsyncSession, character_id: str | None, *, fallback: bool = True
) -> UserProfile | None:
    """Load the latest UserProfile for a character_id, optionally falling
    back to the default (character_id=NULL) when fallback is True."""
    result = await session.execute(
        select(UserProfile)
        .where(UserProfile.character_id == character_id)
        .order_by(desc(UserProfile.updated_at))
        .limit(1)
    )
    profile = result.scalar_one_or_none()
    if not profile and fallback and character_id is not None:
        result = await session.execute(
            select(UserProfile)
            .where(UserProfile.character_id.is_(None))
            .order_by(desc(UserProfile.updated_at))
            .limit(1)
        )
        profile = result.scalar_one_or_none()
    return profile


@router.get("")
async def get_user_profile(
    character_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Get user profile for a character. Falls back to default (character_id=NULL)."""
    profile = await _get_profile(session, character_id)
    if not profile:
        raise HTTPException(status_code=404, detail="No user profile found")
    return _profile_to_dict(profile)


@router.put("")
async def update_user_profile(
    data: UserProfileRequest,
    character_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Create or update the user profile for a character."""
    profile = await _get_profile(session, character_id, fallback=False)

    if profile:
        profile.user_name = data.user_name
        profile.user_gender = data.user_gender
        profile.user_occupation = data.user_occupation
        profile.user_bio = data.user_bio
        profile.user_relationship = data.user_relationship
    else:
        profile = UserProfile(
            character_id=character_id,
            user_name=data.user_name,
            user_gender=data.user_gender,
            user_occupation=data.user_occupation,
            user_bio=data.user_bio,
            user_relationship=data.user_relationship,
        )
        session.add(profile)

    await session.commit()
    await session.refresh(profile)

    # Upsert a user_stated memory in a new, independent session.
    # This way any FTS5 / lock failure cannot break the PUT response.
    if profile.user_bio or profile.user_occupation:
        await _upsert_profile_memory(profile)

    return _profile_to_dict(profile)


async def _upsert_profile_memory(
    profile: UserProfile,
    *,
    _session_factory=None,
) -> None:
    """Create or update a source=user_stated memory from the profile data.

    Opens its own session — completely isolated from the caller's
    transaction so FTS5 or lock issues never affect the PUT endpoint.

    Parameters
    ----------
    _session_factory:
        Session factory to use.  When *None* (the default) resolves from
        ``database.async_session`` at call time so test overrides work.
    """
    if _session_factory is None:
        from database import async_session as _sf
        _session_factory = _sf

    from models.memory import Memory, SOURCE_USER_STATED

    parts = [f"用户告诉我他叫{profile.user_name}"]
    if profile.user_occupation:
        parts.append(f"是一名{profile.user_occupation}")
    if profile.user_bio:
        parts.append(f"他这样描述自己：{profile.user_bio}")
    memory_content = "，".join(parts) + "。"

    try:
        async with _session_factory() as s:
            result = await s.execute(
                select(Memory)
                .where(
                    Memory.source == SOURCE_USER_STATED,
                    Memory.memory_type == "user_fact",
                    Memory.character_id == profile.character_id,
                )
                .order_by(desc(Memory.updated_at))
                .limit(1)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.content = memory_content
                existing.importance = 8
                await s.commit()
            else:
                embedding = None
                try:
                    from services.embedding_service import embed_single
                    vec = await embed_single(memory_content)
                    if vec:
                        from services.memory_store import MemoryStore
                        embedding = MemoryStore.pack_embedding(vec)
                except Exception:
                    pass
                import uuid
                mem = Memory(
                    id=str(uuid.uuid4()),
                    content=memory_content,
                    memory_type="user_fact",
                    source=SOURCE_USER_STATED,
                    importance=8,
                    character_id=profile.character_id,
                    embedding=embedding,
                )
                s.add(mem)
                await s.commit()
    except Exception as e:
        print(f"[UserProfile] memory upsert failed: {e}", flush=True)
