"""群实体管理 API（ticket #52, spec: group-chat Phase 2 · 群实体与数据模型）。

群 = 固定成员集合（成员顺序即群轮发言次序）；一个群可开多条群对话。
"编辑群"是替换式的管理操作——一次 PUT 带完整成员列表即覆盖增、删、调序，
不是聊天中的实时进出。错误语义与现有 REST 一致：对象不存在 → 404；
非法输入 → 400 带明确 detail。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupMemberIn(BaseModel):
    character_id: str


class GroupCreate(BaseModel):
    name: str
    members: list[GroupMemberIn]


class GroupUpdate(BaseModel):
    name: str | None = None
    members: list[GroupMemberIn] | None = None


def _clean_group_name(name: str) -> str:
    """非法输入 → 400：空白或超过 Group.name 的 String(100) 上限。"""
    stripped = (name or "").strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")
    if len(stripped) > 100:
        raise HTTPException(status_code=400, detail="Group name too long (max 100 characters)")
    return stripped


async def _validate_members(session: AsyncSession, members: list[GroupMemberIn]) -> None:
    """非法输入 → 400：至少 1 人、不重复、角色必须存在。"""
    if not members:
        raise HTTPException(status_code=400, detail="Group needs at least one member")
    seen: set[str] = set()
    for m in members:
        if m.character_id in seen:
            raise HTTPException(status_code=400, detail=f"duplicate member: {m.character_id}")
        seen.add(m.character_id)
    found = (await session.execute(
        select(CharacterProfile.id).where(CharacterProfile.id.in_(seen))
    )).scalars().all()
    unknown = sorted(seen - set(found))
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown character_id: {', '.join(unknown)}"
        )


async def _write_members(session: AsyncSession, group_id: str, members: list[GroupMemberIn]) -> None:
    """替换全部成员；数组顺序即发言顺序（position = 下标）。"""
    await session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
    for position, m in enumerate(members):
        session.add(GroupMember(group_id=group_id, character_id=m.character_id, position=position))


async def _group_payload(group: Group, session: AsyncSession) -> dict:
    members = (await session.execute(
        select(GroupMember)
        .where(GroupMember.group_id == group.id)
        .order_by(GroupMember.position)
    )).scalars().all()
    names: dict[str, str] = {}
    if members:
        chars = (await session.execute(
            select(CharacterProfile).where(
                CharacterProfile.id.in_([m.character_id for m in members])
            )
        )).scalars().all()
        names = {c.id: c.name for c in chars}
    conversations = (await session.execute(
        select(Conversation)
        .where(Conversation.group_id == group.id)
        .order_by(Conversation.updated_at.desc())
    )).scalars().all()
    return {
        "id": group.id,
        "name": group.name,
        "created_at": group.created_at.isoformat() if group.created_at else None,
        "members": [
            {
                "character_id": m.character_id,
                "character_name": names.get(m.character_id),
                "position": m.position,
            }
            for m in members
        ],
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in conversations
        ],
    }


async def _get_group_or_404(group_id: str, session: AsyncSession) -> Group:
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.post("")
async def create_group(data: GroupCreate, session: AsyncSession = Depends(get_session)):
    """建群：名称 + 成员列表（数组顺序即发言顺序）。"""
    name = _clean_group_name(data.name)
    await _validate_members(session, data.members)

    group = Group(name=name)
    session.add(group)
    await session.flush()  # 先拿 group.id 再写成员
    await _write_members(session, group.id, data.members)
    await session.commit()
    await session.refresh(group)
    return await _group_payload(group, session)


@router.get("")
async def list_groups(session: AsyncSession = Depends(get_session)):
    """群列表：含成员（带角色名与顺序）与对话概要。"""
    groups = (await session.execute(
        select(Group).order_by(Group.created_at.desc())
    )).scalars().all()
    return [await _group_payload(g, session) for g in groups]


@router.get("/{group_id}")
async def get_group(group_id: str, session: AsyncSession = Depends(get_session)):
    group = await _get_group_or_404(group_id, session)
    return await _group_payload(group, session)


@router.put("/{group_id}")
async def update_group(
    group_id: str, data: GroupUpdate, session: AsyncSession = Depends(get_session)
):
    """编辑群：改名和/或替换成员（管理操作，覆盖增、删、调序）。"""
    group = await _get_group_or_404(group_id, session)
    if data.name is not None:
        group.name = _clean_group_name(data.name)
    if data.members is not None:
        await _validate_members(session, data.members)
        await _write_members(session, group.id, data.members)
    await session.commit()
    await session.refresh(group)
    return await _group_payload(group, session)


@router.post("/{group_id}/conversations")
async def create_group_conversation(
    group_id: str, session: AsyncSession = Depends(get_session)
):
    """为群创建一条群对话（一群可开多条，对应前端"新对话"）。"""
    group = await _get_group_or_404(group_id, session)
    conv = Conversation(character_id=None, group_id=group.id)
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return {
        "id": conv.id,
        "group_id": conv.group_id,
        "character_id": conv.character_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }
