"""实验室管理依赖注入占位。

提供 FastAPI Depends 依赖占位函数：
- get_department_service: 返回 DepartmentService 实例（DI 覆盖）；
- get_user_department_service: 返回 UserDepartmentService 实例（DI 覆盖）。

生产环境通过 main.py lifespan 中的 dependency_overrides 注入按请求构造的实例。
测试环境通过 dependency_overrides 注入测试 fixture。
"""

from packages.departments.service import DepartmentService
from packages.departments.user_departments import UserDepartmentService


def get_department_service() -> DepartmentService:
    """获取 DepartmentService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError("get_department_service must be overridden via dependency_overrides")


def get_user_department_service() -> UserDepartmentService:
    """获取 UserDepartmentService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError(
        "get_user_department_service must be overridden via dependency_overrides"
    )
