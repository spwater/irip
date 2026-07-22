"""标准变量版本状态机单元测试。

验证（IRIP Task 10）：
- 创建变量 → status=draft, version_count=0；
- 提交审核 → 创建 version 1, status=in_review；
- 发布 → version status=published, published_at 已设置, 版本不可变；
- 修改已发布版本抛出 AppError(published_version_immutable)；
- 弃用 → status=deprecated；
- 拒绝 → status=rejected, 含拒绝原因；
- 重提交 → status=in_review, 创建新版本；
- 非法转换（published→draft）抛出 AppError(invalid_transition)；
- 乐观锁冲突抛出 AppError(conflict)。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL）。
"""

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.standards.repository import StandardsRepository
from packages.standards.service import StandardService
from packages.standards.state_machine import assert_transition
from packages.standards.variables import Variable, VariableVersion


class TestStateMachine:
    """状态机纯函数测试（无需数据库）。"""

    def test_draft_to_in_review(self) -> None:
        """draft → in_review 合法。"""
        assert_transition("draft", "in_review")

    def test_in_review_to_published(self) -> None:
        """in_review → published 合法。"""
        assert_transition("in_review", "published")

    def test_in_review_to_rejected(self) -> None:
        """in_review → rejected 合法。"""
        assert_transition("in_review", "rejected")

    def test_published_to_deprecated(self) -> None:
        """published → deprecated 合法。"""
        assert_transition("published", "deprecated")

    def test_rejected_to_draft(self) -> None:
        """rejected → draft 合法。"""
        assert_transition("rejected", "draft")

    def test_published_to_draft_invalid(self) -> None:
        """published → draft 非法，抛出 AppError(invalid_transition)。"""
        with pytest.raises(AppError) as exc_info:
            assert_transition("published", "draft")
        assert exc_info.value.code == "invalid_transition"

    def test_draft_to_published_invalid(self) -> None:
        """draft → published 非法（需先经过 in_review）。"""
        with pytest.raises(AppError) as exc_info:
            assert_transition("draft", "published")
        assert exc_info.value.code == "invalid_transition"

    def test_deprecated_to_anything_invalid(self) -> None:
        """deprecated → 任何状态均非法。"""
        with pytest.raises(AppError) as exc_info:
            assert_transition("deprecated", "draft")
        assert exc_info.value.code == "invalid_transition"


class TestVariableLifecycle:
    """标准变量生命周期测试（需数据库）。"""

    @pytest.mark.asyncio
    async def test_create_variable_draft(
        self, standard_service: StandardService
    ) -> None:
        """创建变量 → status=draft, version_count=0。"""
        variable = await standard_service.create_variable(
            code="particle_size_d50",
            display_name="粒度 D50",
            data_type="number",
            canonical_unit="mm",
            quantity_kind="length",
        )
        assert variable.status == "draft"
        assert variable.version_count == 0
        assert variable.code == "particle_size_d50"
        assert variable.display_name == "粒度 D50"
        assert variable.canonical_unit == "mm"
        assert variable.quantity_kind == "length"

    @pytest.mark.asyncio
    async def test_create_variable_with_valid_range(
        self, standard_service: StandardService
    ) -> None:
        """创建变量带有效范围。"""
        variable = await standard_service.create_variable(
            code="temperature_range",
            display_name="温度范围",
            data_type="number",
            canonical_unit="°C",
            quantity_kind="temperature",
            valid_range=(Decimal("0"), Decimal("100")),
        )
        assert variable.status == "draft"
        assert variable.valid_range is not None
        assert len(variable.valid_range) == 2

    @pytest.mark.asyncio
    async def test_create_duplicate_code_conflict(
        self, standard_service: StandardService
    ) -> None:
        """重复编码抛出 AppError(conflict)。"""
        await standard_service.create_variable(
            code="dup_code",
            display_name="重复编码测试",
            data_type="number",
        )
        with pytest.raises(AppError) as exc_info:
            await standard_service.create_variable(
                code="dup_code",
                display_name="另一个",
                data_type="number",
            )
        assert exc_info.value.code == "conflict"

    @pytest.mark.asyncio
    async def test_submit_creates_version(
        self, standard_service: StandardService
    ) -> None:
        """提交审核 → 创建 version 1, status=in_review。"""
        variable = await standard_service.create_variable(
            code="submit_test",
            display_name="提交测试",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        assert version.version == 1
        assert version.status == "in_review"
        assert version.variable_id == variable.id
        assert version.code == "submit_test"

        # 验证 variable 已更新
        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "in_review"
        assert detail["version_count"] == 1
        assert detail["latest_version"] is not None
        assert detail["latest_version"]["version"] == 1
        assert detail["latest_version"]["status"] == "in_review"

    @pytest.mark.asyncio
    async def test_publish_sets_published_at(
        self, standard_service: StandardService
    ) -> None:
        """发布 → version status=published, published_at 已设置。"""
        variable = await standard_service.create_variable(
            code="publish_test",
            display_name="发布测试",
            data_type="number",
        )
        await standard_service.submit_for_review(variable.id)
        version = await standard_service.publish_variable(variable.id)

        assert version.status == "published"
        assert version.published_at is not None
        assert version.published_by is not None

        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "published"
        assert detail["latest_version"]["status"] == "published"
        assert detail["latest_version"]["published_at"] is not None

    @pytest.mark.asyncio
    async def test_published_version_immutable(
        self,
        standard_service: StandardService,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """修改已发布版本抛出 AppError(published_version_immutable)。"""
        variable = await standard_service.create_variable(
            code="immutable_test",
            display_name="不可变测试",
            data_type="number",
        )
        await standard_service.submit_for_review(variable.id)
        version = await standard_service.publish_variable(variable.id)

        # 尝试将已发布版本改回 in_review → 应抛出 published_version_immutable
        async with session_scope(async_session_factory) as session:
            with pytest.raises(AppError) as exc_info:
                await StandardsRepository.update_version_status(
                    session,
                    version_id=version.id,
                    new_status="in_review",
                    lock_version=version.lock_version,
                )
            assert exc_info.value.code == "published_version_immutable"

    @pytest.mark.asyncio
    async def test_deprecate_published_variable(
        self, standard_service: StandardService
    ) -> None:
        """弃用已发布变量 → status=deprecated。"""
        variable = await standard_service.create_variable(
            code="deprecate_test",
            display_name="弃用测试",
            data_type="number",
        )
        await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)
        version = await standard_service.deprecate_variable(variable.id)

        assert version.status == "deprecated"
        assert version.deprecated_at is not None
        assert version.deprecated_by is not None

        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "deprecated"

    @pytest.mark.asyncio
    async def test_reject_sets_reason(
        self, standard_service: StandardService
    ) -> None:
        """拒绝 → status=rejected, 含拒绝原因。"""
        variable = await standard_service.create_variable(
            code="reject_test",
            display_name="拒绝测试",
            data_type="number",
        )
        await standard_service.submit_for_review(variable.id)
        version = await standard_service.reject_variable(
            variable.id, reason="数据类型不正确"
        )

        assert version.status == "rejected"
        assert version.rejection_reason == "数据类型不正确"

        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "rejected"
        assert detail["latest_version"]["rejection_reason"] == "数据类型不正确"

    @pytest.mark.asyncio
    async def test_resubmit_creates_new_version(
        self, standard_service: StandardService
    ) -> None:
        """重提交 → status=in_review, 创建新版本。"""
        variable = await standard_service.create_variable(
            code="resubmit_test",
            display_name="重提交测试",
            data_type="number",
        )
        # 提交 → 拒绝 → 重提交
        await standard_service.submit_for_review(variable.id)
        await standard_service.reject_variable(variable.id, reason="需修改")
        version = await standard_service.resubmit(variable.id)

        assert version.version == 2
        assert version.status == "in_review"

        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "in_review"
        assert detail["version_count"] == 2
        assert detail["latest_version"]["version"] == 2

    @pytest.mark.asyncio
    async def test_invalid_transition_published_to_draft(
        self, standard_service: StandardService
    ) -> None:
        """非法转换：published → draft 抛出 AppError(invalid_transition)。"""
        variable = await standard_service.create_variable(
            code="invalid_trans_test",
            display_name="非法转换测试",
            data_type="number",
        )
        await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 尝试从 published 直接 submit → 应抛出 invalid_transition
        with pytest.raises(AppError) as exc_info:
            await standard_service.submit_for_review(variable.id)
        assert exc_info.value.code == "invalid_transition"

    @pytest.mark.asyncio
    async def test_submit_non_draft_invalid(
        self, standard_service: StandardService
    ) -> None:
        """非 draft 状态提交抛出 AppError(invalid_transition)。"""
        variable = await standard_service.create_variable(
            code="non_draft_submit",
            display_name="非草稿提交测试",
            data_type="number",
        )
        await standard_service.submit_for_review(variable.id)

        # 已在 in_review 状态，再次 submit → invalid_transition
        with pytest.raises(AppError) as exc_info:
            await standard_service.submit_for_review(variable.id)
        assert exc_info.value.code == "invalid_transition"

    @pytest.mark.asyncio
    async def test_get_variable_not_found(
        self, standard_service: StandardService
    ) -> None:
        """查询不存在的变量抛出 AppError(not_found)。"""
        from packages.common.ids import new_id

        with pytest.raises(AppError) as exc_info:
            await standard_service.get_variable(new_id())
        assert exc_info.value.code == "not_found"

    @pytest.mark.asyncio
    async def test_add_alias_and_find(
        self, standard_service: StandardService
    ) -> None:
        """添加别名并通过别名查找。"""
        variable = await standard_service.create_variable(
            code="alias_test",
            display_name="别名测试",
            data_type="number",
        )
        alias = await standard_service.add_alias(
            variable.id, alias="粒径D50", language="zh"
        )
        assert alias.alias == "粒径D50"
        assert alias.language == "zh"

        detail = await standard_service.get_variable(variable.id)
        assert len(detail["aliases"]) == 1
        assert detail["aliases"][0]["alias"] == "粒径D50"

    @pytest.mark.asyncio
    async def test_add_duplicate_alias_conflict(
        self, standard_service: StandardService
    ) -> None:
        """重复别名抛出 AppError(conflict)。"""
        variable = await standard_service.create_variable(
            code="dup_alias_test",
            display_name="重复别名测试",
            data_type="number",
        )
        await standard_service.add_alias(variable.id, alias="重复别名")
        with pytest.raises(AppError) as exc_info:
            await standard_service.add_alias(variable.id, alias="重复别名")
        assert exc_info.value.code == "conflict"

    @pytest.mark.asyncio
    async def test_list_variables_pagination(
        self, standard_service: StandardService
    ) -> None:
        """分页查询变量列表。"""
        for i in range(5):
            await standard_service.create_variable(
                code=f"list_test_{i}",
                display_name=f"列表测试{i}",
                data_type="number",
            )

        items, next_cursor = await standard_service.list_variables(
            page_size=3
        )
        assert len(items) == 3
        assert next_cursor is not None

        items2, next_cursor2 = await standard_service.list_variables(
            cursor=next_cursor, page_size=3
        )
        assert len(items2) == 2
        assert next_cursor2 is None

    @pytest.mark.asyncio
    async def test_full_lifecycle(
        self, standard_service: StandardService
    ) -> None:
        """完整生命周期：创建 → 提交 → 发布 → 弃用。"""
        variable = await standard_service.create_variable(
            code="lifecycle_test",
            display_name="生命周期测试",
            data_type="number",
            canonical_unit="mm",
            quantity_kind="length",
            valid_range=(Decimal("0"), Decimal("100")),
        )
        assert variable.status == "draft"

        v1 = await standard_service.submit_for_review(variable.id)
        assert v1.status == "in_review"
        assert v1.version == 1

        v1_pub = await standard_service.publish_variable(variable.id)
        assert v1_pub.status == "published"
        assert v1_pub.published_at is not None

        v1_dep = await standard_service.deprecate_variable(variable.id)
        assert v1_dep.status == "deprecated"
        assert v1_dep.deprecated_at is not None

        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "deprecated"
        assert detail["version_count"] == 1

    @pytest.mark.asyncio
    async def test_reject_then_resubmit_then_publish(
        self, standard_service: StandardService
    ) -> None:
        """拒绝后重提交再发布：version 1 rejected, version 2 published。"""
        variable = await standard_service.create_variable(
            code="reject_resubmit_test",
            display_name="拒绝重提交测试",
            data_type="number",
        )
        v1 = await standard_service.submit_for_review(variable.id)
        assert v1.version == 1

        v1_rej = await standard_service.reject_variable(
            variable.id, reason="需补充描述"
        )
        assert v1_rej.status == "rejected"

        v2 = await standard_service.resubmit(variable.id)
        assert v2.version == 2
        assert v2.status == "in_review"

        v2_pub = await standard_service.publish_variable(variable.id)
        assert v2_pub.status == "published"
        assert v2_pub.version == 2

        detail = await standard_service.get_variable(variable.id)
        assert detail["status"] == "published"
        assert detail["version_count"] == 2
        assert detail["latest_version"]["version"] == 2
        assert detail["latest_version"]["status"] == "published"


class TestOptimisticLock:
    """乐观锁冲突测试。"""

    @pytest.mark.asyncio
    async def test_repository_optimistic_lock_returns_none(
        self,
        standard_service: StandardService,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Repository 层乐观锁：过期 lock_version 返回 None。"""
        variable = await standard_service.create_variable(
            code="lock_test",
            display_name="乐观锁测试",
            data_type="number",
        )

        # 正确 lock_version=0 更新成功
        async with session_scope(async_session_factory) as session:
            updated = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable.id,
                new_status="in_review",
                lock_version=0,
                increment_version_count=True,
            )
            assert updated is not None
            assert updated.lock_version == 1

        # 过期 lock_version=0 更新返回 None（DB 已是 1）
        async with session_scope(async_session_factory) as session:
            result = await StandardsRepository.update_variable_status(
                session,
                variable_id=variable.id,
                new_status="published",
                lock_version=0,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_concurrent_submit_one_succeeds(
        self,
        standard_service: StandardService,
        async_session_factory: async_sessionmaker[AsyncSession],
        test_user: object,
    ) -> None:
        """并发提交：两个请求同时提交同一变量，仅一个成功。"""
        variable = await standard_service.create_variable(
            code="concurrent_test",
            display_name="并发测试",
            data_type="number",
        )

        service2 = StandardService(
            session_factory=async_session_factory,
            organization_id=test_user.organization_id,  # type: ignore[attr-defined]
            actor_id=test_user.user_id,  # type: ignore[attr-defined]
        )

        results = await asyncio.gather(
            standard_service.submit_for_review(variable.id),
            service2.submit_for_review(variable.id),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
        assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
        assert isinstance(failures[0], AppError)
        # 失败可能是 conflict（乐观锁）或 invalid_transition（状态已变）
        assert failures[0].code in ("conflict", "invalid_transition")
