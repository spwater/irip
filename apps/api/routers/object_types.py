"""实验对象类型管理路由。"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.standards.object_type_dict import ObjectTypeDict
from packages.standards.object_type_service import ObjectTypeService

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


def _make_service(current_user: CurrentUser) -> ObjectTypeService:
    """构建 ObjectTypeService 实例。"""
    factory: async_sessionmaker[AsyncSession] = _get_session_factory()
    return ObjectTypeService(
        session_factory=factory,
        department_id=current_user.department_id,
        actor_id=current_user.user_id,
    )


@object_types_router.get("", response_model=list[ObjectTypeResponse])
async def list_object_types(
    current_user: ReadUserDep,
) -> list[ObjectTypeResponse]:
    service = _make_service(current_user)
    items = await service.list_object_types()
    return [_to_response(o) for o in items]


@object_types_router.post("", response_model=ObjectTypeResponse, status_code=201)
async def create_object_type(
    body: CreateObjectTypeRequest,
    current_user: WriteUserDep,
) -> ObjectTypeResponse:
    service = _make_service(current_user)
    obj = await service.create_object_type(
        display_name=body.display_name,
        description=body.description,
    )
    return _to_response(obj)


@object_types_router.patch("/{type_id}", response_model=ObjectTypeResponse)
async def update_object_type(
    type_id: UUID,
    body: UpdateObjectTypeRequest,
    current_user: WriteUserDep,
) -> ObjectTypeResponse:
    service = _make_service(current_user)
    obj = await service.update_object_type(
        type_id=type_id,
        display_name=body.display_name,
        description=body.description,
    )
    return _to_response(obj)


@object_types_router.delete("/{type_id}", status_code=204)
async def delete_object_type(
    type_id: UUID,
    current_user: WriteUserDep,
) -> None:
    service = _make_service(current_user)
    await service.delete_object_type(type_id)
