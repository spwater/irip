"""推导配方服务（IRIP Task 17）。

RecipeService 提供推导配方的创建、发布版本、查询与列表。

核心不变量：
1. published_immutable: 发布后的配方版本不可修改，保证确定性回放。
2. org_unique_code: 配方代码在部门内唯一。
3. version_increment: 配方版本号在配方范围内递增。

依赖注入 session_factory（事务管理）、department_id（当前部门）、
actor_id（操作人）。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.dept_visibility import compute_visible_dept_ids
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.provenance.entities import (
    TransformationRecipe,
    TransformationRecipeVersion,
)


@dataclass(frozen=True)
class RecipeVersion:
    """配方版本（不可变值对象）。

    Attributes:
        id: 版本 UUID。
        recipe_id: 配方 UUID。
        version: 版本号。
        component_name: 执行器组件名称。
        component_version: 执行器组件版本。
        parameters: 算法参数字典。
        random_seed: 随机种子。
        output_definitions: 输出定义元组。
        status: 状态（published）。
    """

    id: UUID
    recipe_id: UUID
    version: int
    component_name: str
    component_version: str
    parameters: dict[str, object]
    random_seed: int
    output_definitions: tuple[str, ...]
    status: str


class RecipeService:
    """推导配方业务编排服务。

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化推导配方服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID（可选）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id

    async def create_recipe(self, code: str, display_name: str) -> dict:
        """创建配方（draft 状态）。

        Args:
            code: 配方代码（部门内唯一）。
            display_name: 显示名称。

        Returns:
            dict: 包含 recipe_id, code, display_name, status 的字典。

        Raises:
            AppError: code="validation_failed"，当 code 或 display_name 为空时。
            AppError: code="conflict"，当 code 在部门内已存在时。
        """
        if not code or not code.strip():
            raise AppError(
                code="validation_failed",
                message="配方代码不能为空",
                retryable=False,
                fields={"code": "required"},
            )
        if not display_name or not display_name.strip():
            raise AppError(
                code="validation_failed",
                message="配方显示名称不能为空",
                retryable=False,
                fields={"display_name": "required"},
            )

        async with session_scope(self._factory) as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            # 检查 code 唯一性
            existing = await session.scalar(
                sa.select(TransformationRecipe).where(
                    TransformationRecipe.department_id.in_(visible_ids),
                    TransformationRecipe.code == code.strip(),
                )
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message=f"配方代码已存在: {code}",
                    retryable=False,
                    fields={"code": code},
                )

            recipe = TransformationRecipe(
                id=new_id(),
                department_id=self._dept_id,
                owner_user_id=self._actor_id,
                visibility_scope="tree",
                code=code.strip(),
                display_name=display_name.strip(),
                status="draft",
                lock_version=0,
            )
            session.add(recipe)
            await session.flush()
            return {
                "recipe_id": recipe.id,
                "code": recipe.code,
                "display_name": recipe.display_name,
                "status": recipe.status,
            }

    async def publish_version(
        self,
        recipe_id: UUID,
        component_name: str,
        component_version: str,
        parameters: dict,
        random_seed: int,
        output_definitions: tuple[str, ...],
    ) -> RecipeVersion:
        """发布配方版本（不可变）。

        流程：
        1. 加载配方（必须存在）；
        2. 计算新版本号（当前最大版本 + 1）；
        3. 创建 transformation_recipe_version（不可变）；
        4. 更新配方 status 为 published；
        5. 返回 RecipeVersion。

        Args:
            recipe_id: 配方 UUID。
            component_name: 执行器组件名称。
            component_version: 执行器组件版本。
            parameters: 算法参数字典。
            random_seed: 随机种子。
            output_definitions: 输出定义元组。

        Returns:
            RecipeVersion: 配方版本引用。

        Raises:
            AppError: code="not_found"，当配方不存在时。
            AppError: code="validation_failed"，当参数无效时。
        """
        if not component_name or not component_name.strip():
            raise AppError(
                code="validation_failed",
                message="组件名称不能为空",
                retryable=False,
                fields={"component_name": "required"},
            )
        if not component_version or not component_version.strip():
            raise AppError(
                code="validation_failed",
                message="组件版本不能为空",
                retryable=False,
                fields={"component_version": "required"},
            )

        async with session_scope(self._factory) as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            # 1. 加载配方
            recipe = await session.scalar(
                sa.select(TransformationRecipe).where(
                    TransformationRecipe.id == recipe_id,
                    TransformationRecipe.department_id.in_(visible_ids),
                )
            )
            if recipe is None:
                raise AppError(
                    code="not_found",
                    message=f"配方不存在: {recipe_id}",
                    retryable=False,
                    fields={"recipe_id": str(recipe_id)},
                )

            # 2. 计算新版本号
            latest_version_row = await session.scalar(
                sa.select(TransformationRecipeVersion)
                .where(TransformationRecipeVersion.recipe_id == recipe_id)
                .order_by(TransformationRecipeVersion.version.desc())
                .limit(1)
            )
            new_version: int = latest_version_row.version + 1 if latest_version_row else 1

            # 3. 创建配方版本（不可变）
            recipe_version = TransformationRecipeVersion(
                id=new_id(),
                recipe_id=recipe_id,
                version=new_version,
                component_name=component_name.strip(),
                component_version=component_version.strip(),
                parameters=parameters,
                random_seed=random_seed,
                output_definitions=list(output_definitions),
                status="published",
                published_at=datetime.now(UTC),
            )
            session.add(recipe_version)

            # 4. 更新配方 status 为 published
            await session.execute(
                sa.update(TransformationRecipe)
                .values(
                    status="published",
                    updated_at=sa.func.now(),
                    lock_version=TransformationRecipe.lock_version + 1,
                )
                .where(TransformationRecipe.id == recipe_id)
            )

            await session.flush()

            return RecipeVersion(
                id=recipe_version.id,
                recipe_id=recipe_id,
                version=new_version,
                component_name=component_name.strip(),
                component_version=component_version.strip(),
                parameters=parameters,
                random_seed=random_seed,
                output_definitions=output_definitions,
                status="published",
            )

    async def get_recipe(self, recipe_id: UUID) -> dict:
        """获取配方详情（含最新版本信息）。

        Args:
            recipe_id: 配方 UUID。

        Returns:
            dict: 配方详情。

        Raises:
            AppError: code="not_found"，当配方不存在时。
        """
        async with self._factory() as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            recipe = await session.scalar(
                sa.select(TransformationRecipe).where(
                    TransformationRecipe.id == recipe_id,
                    TransformationRecipe.department_id.in_(visible_ids),
                )
            )
            if recipe is None:
                raise AppError(
                    code="not_found",
                    message=f"配方不存在: {recipe_id}",
                    retryable=False,
                    fields={"recipe_id": str(recipe_id)},
                )

            latest_version = await session.scalar(
                sa.select(TransformationRecipeVersion)
                .where(TransformationRecipeVersion.recipe_id == recipe_id)
                .order_by(TransformationRecipeVersion.version.desc())
                .limit(1)
            )

            result: dict = {
                "recipe_id": recipe_id,
                "code": recipe.code,
                "display_name": recipe.display_name,
                "status": recipe.status,
            }
            if latest_version is not None:
                result["version"] = latest_version.version
                result["version_id"] = latest_version.id
                result["component_name"] = latest_version.component_name
                result["component_version"] = latest_version.component_version
                result["parameters"] = latest_version.parameters
                result["random_seed"] = latest_version.random_seed
                result["output_definitions"] = latest_version.output_definitions
            else:
                result["version"] = 0
            return result

    async def list_recipes(
        self,
        cursor: str | None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页列出配方。

        Args:
            cursor: 分页游标（None 表示第一页）。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]: (配方列表, 下一页游标)。
        """
        async with self._factory() as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id, self._actor_id)
            stmt = (
                sa.select(TransformationRecipe)
                .where(TransformationRecipe.department_id.in_(visible_ids))
                .order_by(TransformationRecipe.created_at, TransformationRecipe.id)
                .limit(page_size + 1)
            )

            # 游标分页
            if cursor is not None:
                try:
                    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
                    payload = json.loads(raw)
                    cursor_time = datetime.fromisoformat(str(payload["v"]))
                    cursor_id = UUID(str(payload["id"]))
                    stmt = stmt.where(
                        sa.or_(
                            TransformationRecipe.created_at > cursor_time,
                            sa.and_(
                                TransformationRecipe.created_at == cursor_time,
                                TransformationRecipe.id > cursor_id,
                            ),
                        )
                    )
                except Exception as exc:
                    raise AppError(
                        code="invalid_cursor",
                        message=f"分页游标无效: {exc}",
                        retryable=False,
                        fields={"cursor": cursor},
                    ) from exc

            result = await session.execute(stmt)
            recipes = result.scalars().all()

            next_cursor: str | None = None
            if len(recipes) > page_size:
                last = recipes[page_size - 1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {"v": last.created_at.isoformat(), "id": str(last.id)},
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).decode("ascii")

            items: list[dict] = [
                {
                    "recipe_id": r.id,
                    "code": r.code,
                    "display_name": r.display_name,
                    "status": r.status,
                }
                for r in recipes[:page_size]
            ]
            return items, next_cursor
