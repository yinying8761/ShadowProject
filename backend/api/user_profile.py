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
    return _profile_to_dict(profile)
