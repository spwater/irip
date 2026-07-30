"""JobKindPolicy: 服务端 Job kind 策略注册表。

每个 kind 固定权限、输入 schema、队列、超时、资源预算。
通用 POST /jobs 接口只允许 allow_general_submit=True 的 kind；
特权 kind（backup/restore/audit_export）必须通过专用 API 提交。

使用约定（技术设计文档 S8.4）：
1. 通用 POST /jobs 接口只允许 allow_general_submit=True 的 kind；
2. 特权 kind（backup/restore/audit_export）必须通过专用 API 提交；
3. Worker 执行前必须二次校验 kind、权限快照和 fencing token；
4. 服务端生成 organization_id、actor、backup_id，不接受客户端覆盖；
5. 未知 kind 直接 failed，禁止 echo fallback。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class KindPolicy:
    """单个 Job kind 的策略定义。

    Attributes:
        required_permission: 提交此 kind 所需的权限字符串。
        queue: Celery 队列名。
        timeout_seconds: 作业超时时间（秒）。
        max_retries: 最大重试次数。
        allow_general_submit: 是否允许通过通用 POST /jobs 接口提交。
    """

    required_permission: str
    queue: str = "irip-jobs"
    timeout_seconds: int = 3600
    max_retries: int = 3
    allow_general_submit: bool = False


class JobKindPolicy:
    """Job kind 策略注册表。

    定义每个 kind 的权限、队列、超时、资源预算。
    提供 validate() 方法用于 API 入口和 Worker 二次校验。
    """

    POLICIES: dict[str, KindPolicy] = {
        "flow_execute": KindPolicy(
            required_permission="job:submit",
            queue="irip-jobs",
            timeout_seconds=3600,
            max_retries=3,
            allow_general_submit=True,
        ),
        "flow_resume": KindPolicy(
            required_permission="job:submit",
            queue="irip-jobs",
            timeout_seconds=1800,
            max_retries=2,
            allow_general_submit=True,
        ),
        "ingestion": KindPolicy(
            required_permission="job:submit",
            queue="irip-jobs",
            timeout_seconds=1800,
            max_retries=3,
            allow_general_submit=True,
        ),
        "model_train": KindPolicy(
            required_permission="job:submit",
            queue="irip-jobs",
            timeout_seconds=7200,
            max_retries=3,
            allow_general_submit=True,
        ),
        "model_predict": KindPolicy(
            required_permission="job:submit",
            queue="irip-jobs",
            timeout_seconds=1800,
            max_retries=3,
            allow_general_submit=True,
        ),
        "model_publish": KindPolicy(
            required_permission="job:submit",
            queue="irip-jobs",
            timeout_seconds=1800,
            max_retries=2,
            allow_general_submit=True,
        ),
        "backup": KindPolicy(
            required_permission="system:manage",
            queue="irip-ops",
            timeout_seconds=7200,
            max_retries=0,
            allow_general_submit=False,
        ),
        "restore": KindPolicy(
            required_permission="system:manage",
            queue="irip-ops",
            timeout_seconds=14400,
            max_retries=0,
            allow_general_submit=False,
        ),
        "audit_export": KindPolicy(
            required_permission="system:manage",
            queue="irip-ops",
            timeout_seconds=3600,
            max_retries=0,
            allow_general_submit=False,
        ),
    }

    @classmethod
    def validate(
        cls,
        kind: str,
        user_permissions: set[str],
        *,
        via_general: bool = False,
    ) -> KindPolicy:
        """校验 kind 并返回策略。

        Args:
            kind: 作业类型字符串。
            user_permissions: 当前用户的权限集合。
            via_general: 是否通过通用 POST /jobs 接口提交。
                True 时特权 kind 不允许提交。

        Returns:
            KindPolicy: 匹配的策略。

        Raises:
            ValueError: 当 kind 未知时。
            PermissionError: 当用户缺少权限或特权 kind 通过通用接口提交时。
        """
        if kind not in cls.POLICIES:
            raise ValueError(f"Unknown job kind: {kind}")
        policy = cls.POLICIES[kind]
        if via_general and not policy.allow_general_submit:
            raise PermissionError(
                f"Privileged job kind '{kind}' must be submitted via dedicated API"
            )
        if policy.required_permission not in user_permissions:
            raise PermissionError(
                f"Permission denied for kind '{kind}': "
                f"requires '{policy.required_permission}'"
            )
        return policy

    @classmethod
    def is_general_submit_allowed(cls, kind: str) -> bool:
        """检查 kind 是否允许通过通用接口提交。

        Args:
            kind: 作业类型字符串。

        Returns:
            bool: 允许通用提交返回 True，未知 kind 返回 False。
        """
        policy = cls.POLICIES.get(kind)
        return policy.allow_general_submit if policy else False

    @classmethod
    def get_policy(cls, kind: str) -> KindPolicy | None:
        """获取 kind 的策略（不校验权限）。

        Args:
            kind: 作业类型字符串。

        Returns:
            KindPolicy | None: 匹配的策略，未知 kind 返回 None。
        """
        return cls.POLICIES.get(kind)

    @classmethod
    def registered_kinds(cls) -> list[str]:
        """返回所有已注册的 kind 列表。

        Returns:
            list[str]: 已注册的 kind 字符串列表。
        """
        return list(cls.POLICIES.keys())
