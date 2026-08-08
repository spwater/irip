"""作业相关依赖覆盖 provider（F-20）。

注册：
- JobService（作业服务，需当前用户上下文）。

租户标识使用 department_id。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.jobs import get_job_service


def register(ctx: CompositionContext) -> None:
    """注册作业相关依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.dependencies.dept_scope import get_rls_dept_id
    from packages.jobs.service import JobService

    async def _get_job_service(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> JobService:
        """按请求构造作业服务，从 DB 查询当前用户的 department_id。"""
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = JobService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            created_by=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service.set_rls_override(rls_dept_id)
        return service

    ctx.app.dependency_overrides[get_job_service] = _get_job_service
