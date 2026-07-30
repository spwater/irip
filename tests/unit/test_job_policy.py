"""JobKindPolicy 单元测试。

覆盖 T01 新增的 ``packages/common/job_policy.py``：
- 9 个 kind 的策略定义（6 个 allow_general_submit=True，3 个=False）；
- ``validate()`` 方法对所有 kind 的校验逻辑；
- ``via_general=True`` 时特权 kind 被拒绝；
- 未知 kind 抛出 ``ValueError``；
- 缺少权限抛出 ``PermissionError``；
- ``is_general_submit_allowed()`` 辅助方法。

本测试为纯单元测试，不依赖数据库或外部服务。
"""

import pytest

from packages.common.job_policy import JobKindPolicy, KindPolicy


class TestPolicyRegistry:
    """策略注册表完整性。"""

    def test_registered_kinds_count(self) -> None:
        """已注册 9 个 kind。"""
        kinds = JobKindPolicy.registered_kinds()
        assert len(kinds) == 9

    def test_general_submit_kinds(self) -> None:
        """6 个 kind 允许通用提交。"""
        general_kinds = [
            k
            for k in JobKindPolicy.registered_kinds()
            if JobKindPolicy.is_general_submit_allowed(k)
        ]
        assert sorted(general_kinds) == sorted(
            [
                "flow_execute",
                "flow_resume",
                "ingestion",
                "model_train",
                "model_predict",
                "model_publish",
            ]
        )

    def test_privileged_kinds(self) -> None:
        """3 个 kind 不允许通用提交（特权 kind）。"""
        privileged_kinds = [
            k
            for k in JobKindPolicy.registered_kinds()
            if not JobKindPolicy.is_general_submit_allowed(k)
        ]
        assert sorted(privileged_kinds) == sorted(
            [
                "backup",
                "restore",
                "audit_export",
            ]
        )

    def test_privileged_kinds_require_system_manage(self) -> None:
        """特权 kind 的 required_permission 为 system:manage。"""
        for kind in ("backup", "restore", "audit_export"):
            policy = JobKindPolicy.get_policy(kind)
            assert policy is not None
            assert policy.required_permission == "system:manage"

    def test_general_kinds_require_job_submit(self) -> None:
        """通用 kind 的 required_permission 为 job:submit。"""
        for kind in (
            "flow_execute",
            "flow_resume",
            "ingestion",
            "model_train",
            "model_predict",
            "model_publish",
        ):
            policy = JobKindPolicy.get_policy(kind)
            assert policy is not None
            assert policy.required_permission == "job:submit"

    def test_is_general_submit_allowed_unknown_kind(self) -> None:
        """未知 kind 的 is_general_submit_allowed 返回 False。"""
        assert JobKindPolicy.is_general_submit_allowed("nonexistent") is False

    def test_get_policy_unknown_kind_returns_none(self) -> None:
        """未知 kind 的 get_policy 返回 None。"""
        assert JobKindPolicy.get_policy("nonexistent") is None


class TestValidateGeneralKinds:
    """通用 kind 的 validate() 行为。"""

    @pytest.mark.parametrize(
        "kind",
        [
            "flow_execute",
            "flow_resume",
            "ingestion",
            "model_train",
            "model_predict",
            "model_publish",
        ],
    )
    def test_general_kind_with_job_submit_permission(self, kind: str) -> None:
        """拥有 job:submit 权限的用户可提交通用 kind。"""
        policy = JobKindPolicy.validate(kind, {"job:submit"}, via_general=True)
        assert isinstance(policy, KindPolicy)
        assert policy.required_permission == "job:submit"
        assert policy.allow_general_submit is True

    @pytest.mark.parametrize(
        "kind",
        [
            "flow_execute",
            "flow_resume",
            "ingestion",
            "model_train",
            "model_predict",
            "model_publish",
        ],
    )
    def test_general_kind_without_permission(self, kind: str) -> None:
        """缺少 job:submit 权限时抛出 PermissionError。"""
        with pytest.raises(PermissionError, match="Permission denied"):
            JobKindPolicy.validate(kind, set(), via_general=True)

    @pytest.mark.parametrize(
        "kind",
        [
            "flow_execute",
            "flow_resume",
            "ingestion",
            "model_train",
            "model_predict",
            "model_publish",
        ],
    )
    def test_general_kind_via_dedicated_api(self, kind: str) -> None:
        """通用 kind 通过专用 API（via_general=False）也需权限校验。"""
        policy = JobKindPolicy.validate(kind, {"job:submit"}, via_general=False)
        assert policy.required_permission == "job:submit"

    def test_general_kind_with_extra_permissions(self) -> None:
        """拥有额外权限不影响校验结果。"""
        policy = JobKindPolicy.validate(
            "flow_execute",
            {"job:submit", "system:manage", "fact:read"},
            via_general=True,
        )
        assert policy.required_permission == "job:submit"


class TestValidatePrivilegedKinds:
    """特权 kind 的 validate() 行为。"""

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_privileged_kind_via_general_rejected(self, kind: str) -> None:
        """特权 kind 通过通用接口提交被拒绝（即使有 system:manage 权限）。"""
        with pytest.raises(PermissionError, match="must be submitted via dedicated API"):
            JobKindPolicy.validate(kind, {"system:manage"}, via_general=True)

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_privileged_kind_via_dedicated_with_permission(self, kind: str) -> None:
        """特权 kind 通过专用 API（via_general=False）且拥有 system:manage 时成功。"""
        policy = JobKindPolicy.validate(kind, {"system:manage"}, via_general=False)
        assert policy.required_permission == "system:manage"
        assert policy.allow_general_submit is False

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_privileged_kind_via_dedicated_without_permission(self, kind: str) -> None:
        """特权 kind 通过专用 API 但缺少 system:manage 时被拒绝。"""
        with pytest.raises(PermissionError, match="Permission denied"):
            JobKindPolicy.validate(kind, {"job:submit"}, via_general=False)

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_privileged_kind_via_general_no_permission(self, kind: str) -> None:
        """特权 kind 通过通用接口且无权限时，via_general 检查优先于权限检查。"""
        with pytest.raises(PermissionError, match="must be submitted via dedicated API"):
            JobKindPolicy.validate(kind, set(), via_general=True)


class TestValidateUnknownKind:
    """未知 kind 的 validate() 行为。"""

    def test_unknown_kind_raises_value_error(self) -> None:
        """未知 kind 抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unknown job kind"):
            JobKindPolicy.validate("nonexistent", {"job:submit"})

    def test_unknown_kind_via_general_raises_value_error(self) -> None:
        """未知 kind 通过通用接口也抛出 ValueError（而非 PermissionError）。"""
        with pytest.raises(ValueError, match="Unknown job kind"):
            JobKindPolicy.validate("nonexistent", set(), via_general=True)

    def test_unknown_kind_empty_string(self) -> None:
        """空字符串 kind 抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unknown job kind"):
            JobKindPolicy.validate("", {"job:submit"})

    def test_unknown_kind_with_all_permissions(self) -> None:
        """未知 kind 即使拥有所有权限也抛出 ValueError。"""
        all_perms = {
            "job:submit",
            "system:manage",
            "fact:read",
            "fact:write",
        }
        with pytest.raises(ValueError, match="Unknown job kind"):
            JobKindPolicy.validate("malicious_kind", all_perms)


class TestPolicyAttributes:
    """KindPolicy 属性正确性。"""

    def test_backup_policy_attributes(self) -> None:
        """backup 策略属性：irip-ops 队列、0 重试。"""
        policy = JobKindPolicy.get_policy("backup")
        assert policy is not None
        assert policy.queue == "irip-ops"
        assert policy.max_retries == 0
        assert policy.timeout_seconds == 7200

    def test_restore_policy_attributes(self) -> None:
        """restore 策略属性：irip-ops 队列、0 重试、14400 超时。"""
        policy = JobKindPolicy.get_policy("restore")
        assert policy is not None
        assert policy.queue == "irip-ops"
        assert policy.max_retries == 0
        assert policy.timeout_seconds == 14400

    def test_flow_execute_policy_attributes(self) -> None:
        """flow_execute 策略属性：irip-jobs 队列、3 重试。"""
        policy = JobKindPolicy.get_policy("flow_execute")
        assert policy is not None
        assert policy.queue == "irip-jobs"
        assert policy.max_retries == 3
        assert policy.timeout_seconds == 3600

    def test_kind_policy_is_frozen(self) -> None:
        """KindPolicy 是 frozen dataclass，不可修改。"""
        policy = JobKindPolicy.get_policy("flow_execute")
        assert policy is not None
        with pytest.raises(AttributeError):
            policy.required_permission = "system:manage"  # type: ignore[misc]
