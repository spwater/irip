"""实验对象类型管理路由。"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.standards.object_type_dict import ObjectTypeDict

import sqlalchemy as sa

object_types_router = APIRouter(prefix="/api/v1/object-types", tags=["object-types"])

WriteUserDep = Annotated[CurrentUser, Depends(require_permission("standard:write"))]
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("standard:read"))]

# 全局 session factory（由 composition 注册）
_session_factory: Any = None


def set_session_factory(factory: Any) -> None:
    global _session_factory
    _session_factory = factory


def _get_session_factory() -> Any:
    if _session_factory is None:
        raise RuntimeError("Session factory not set. Call set_session_factory() first.")
    return _session_factory


class ObjectTypeResponse(BaseModel):
    id: str
    code: str
    display_name: str
    description: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class CreateObjectTypeRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


class UpdateObjectTypeRequest(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)


def _to_response(obj: ObjectTypeDict) -> ObjectTypeResponse:
    return ObjectTypeResponse(
        id=str(obj.id),
        code=obj.code,
        display_name=obj.display_name,
        description=obj.description,
        sort_order=obj.sort_order,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


@object_types_router.get("", response_model=list[ObjectTypeResponse])
async def list_object_types(
    current_user: ReadUserDep,
) -> list[ObjectTypeResponse]:
    async with session_scope(_get_session_factory()) as session:
        result = await session.execute(
            sa.select(ObjectTypeDict).order_by(ObjectTypeDict.sort_order.asc())
        )
        items = result.scalars().all()
        return [_to_response(o) for o in items]


@object_types_router.post("", response_model=ObjectTypeResponse, status_code=201)
async def create_object_type(
    body: CreateObjectTypeRequest,
    current_user: WriteUserDep,
) -> ObjectTypeResponse:
    from packages.common.ids import gen_code
    code = gen_code("obtype")
    async with session_scope(_get_session_factory()) as session:
        existing = await session.execute(
            sa.select(ObjectTypeDict).where(ObjectTypeDict.display_name == body.display_name)
        )
        if existing.scalar_one_or_none() is not None:
            raise AppError(code="conflict", message="类型名称已存在", retryable=False)
        max_order = await session.execute(
            sa.select(sa.func.max(ObjectTypeDict.sort_order))
        )
        sort_order = (max_order.scalar() or 0) + 1
        obj = ObjectTypeDict(
            code=code,
            display_name=body.display_name,
            description=body.description,
            sort_order=sort_order,
        )
        session.add(obj)
        await session.flush()
        return _to_response(obj)


@object_types_router.patch("/{type_id}", response_model=ObjectTypeResponse)
async def update_object_type(
    type_id: UUID,
    body: UpdateObjectTypeRequest,
    current_user: WriteUserDep,
) -> ObjectTypeResponse:
    from datetime import UTC, datetime as dt
    async with session_scope(_get_session_factory()) as session:
        result = await session.execute(
            sa.select(ObjectTypeDict).where(ObjectTypeDict.id == type_id)
        )
        obj = result.scalar_one_or_none()
        if obj is None:
            raise AppError(code="not_found", message="类型不存在", retryable=False)
        if body.display_name is not None:
            obj.display_name = body.display_name
        if body.description is not None:
            obj.description = body.description
        obj.updated_at = dt.now(UTC)
        await session.flush()
        return _to_response(obj)


@object_types_router.delete("/{type_id}", status_code=204)
async def delete_object_type(
    type_id: UUID,
    current_user: WriteUserDep,
) -> None:
    async with session_scope(_get_session_factory()) as session:
        result = await session.execute(
            sa.select(ObjectTypeDict).where(ObjectTypeDict.id == type_id)
        )
        obj = result.scalar_one_or_none()
        if obj is None:
            raise AppError(code="not_found", message="类型不存在", retryable=False)
        # 检查是否有对象在用这个类型
        from packages.standards.objects import IndustrialObject
        count = await session.execute(
            sa.select(sa.func.count())
            .select_from(IndustrialObject)
            .where(IndustrialObject.object_type == obj.code)
        )
        if int(count.scalar() or 0) > 0:
            raise AppError(
                code="conflict",
                message=f"该类型下还有 {count.scalar()} 个实验对象，无法删除",
                retryable=False,
            )
        await session.delete(obj)
