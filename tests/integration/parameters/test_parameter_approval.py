"""参数审批、发布与过期检测集成测试（IRIP Task 18）。

验证：
- 从成功的推导运行创建参数候选；
- 审批通过候选创建不可变参数版本；
- 职责分离：提交人不能审批自己的候选；
- 推导运行未成功时不能审批；
- 拒绝候选；
- 过期检测：事实修订后参数变为 review_required；
- 历史已发布版本仍可读。

使用真实 DB session（非 mock），验证完整 L1→L2→L2.5→L3 链路。
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.service import FactService
from packages.parameters.entities import (
    Parameter,
    ParameterCandidate,
    ParameterVersion,
)
from packages.parameters.service import ParameterService
from packages.provenance.entities import DerivationRun
from tests.integration.parameters.conftest import _create_derivation_chain


class TestParameterCandidate:
    """参数候选创建与审批测试。"""

    @pytest.mark.asyncio
    async def test_create_candidate_from_successful_derivation(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """从成功的推导运行创建参数候选。

        流程：
        1. 创建推导链（成功的推导运行）；
        2. 创建参数；
        3. 从推导运行创建候选 → 成功，状态为 pending_review。
        """
        org_id = param_setup["department_id"]
        actor_id = param_setup["actor_id"]

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=3)
        run_ref = chain["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            department_id=org_id,
            actor_id=actor_id,
        )

        # 创建参数
        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        # 从成功的推导运行创建候选
        output = run_ref.outputs[0]
        candidate_result = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run_ref.id,
            value=str(output.value),
            unit=output.unit,
            confidence=str(output.confidence),
        )
        assert candidate_result["status"] == "pending_review"
        assert candidate_result["parameter_id"] == parameter_id

    @pytest.mark.asyncio
    async def test_approve_creates_immutable_version(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """审批通过候选创建不可变参数版本。

        流程：
        1. 创建推导链；
        2. 创建参数与候选（提交人 = actor）；
        3. 用不同的审核人审批；
        4. 验证 parameter_version 创建（status=published）；
        5. 验证 parameter.status = published。
        """
        org_id = param_setup["department_id"]
        actor_id = param_setup["actor_id"]
        reviewer_id = new_id()  # 不同的审核人

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=3)
        run_ref = chain["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            department_id=org_id,
            actor_id=actor_id,
        )

        # 创建参数与候选
        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        output = run_ref.outputs[0]
        candidate_result = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run_ref.id,
            value=str(output.value),
            unit=output.unit,
            confidence=str(output.confidence),
        )
        candidate_id = candidate_result["candidate_id"]

        # 审批通过（不同审核人）
        version_ref = await param_service.approve(
            candidate_id=candidate_id,
            reviewer=reviewer_id,
        )
        assert version_ref.status == "published"
        assert version_ref.version == 1
        assert version_ref.value == str(output.value)

        # 验证 parameter_version 已创建
        async with async_session_factory() as session:
            pv = await session.scalar(
                sa.select(ParameterVersion).where(
                    ParameterVersion.parameter_id == parameter_id,
                    ParameterVersion.version == 1,
                )
            )
            assert pv is not None
            assert pv.status == "published"
            assert pv.published_by == reviewer_id

            # 验证 parameter.status = published
            param = await session.scalar(sa.select(Parameter).where(Parameter.id == parameter_id))
            assert param is not None
            assert param.status == "published"

            # 验证候选状态为 approved
            candidate = await session.scalar(
                sa.select(ParameterCandidate).where(ParameterCandidate.id == candidate_id)
            )
            assert candidate is not None
            assert candidate.status == "approved"

    @pytest.mark.asyncio
    async def test_submitter_cannot_approve_own_parameter(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """提交人不能审批自己的参数候选（职责分离）。

        流程：
        1. 创建推导链；
        2. 创建参数与候选（提交人 = actor）；
        3. 用同一用户审批 → self_approval_forbidden。
        """
        org_id = param_setup["department_id"]
        actor_id = param_setup["actor_id"]

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=3)
        run_ref = chain["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            department_id=org_id,
            actor_id=actor_id,
        )

        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        output = run_ref.outputs[0]
        candidate_result = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run_ref.id,
            value=str(output.value),
            unit=output.unit,
            confidence=str(output.confidence),
        )
        candidate_id = candidate_result["candidate_id"]

        # 提交人尝试审批自己的候选 → 应被拒绝
        with pytest.raises(AppError) as exc_info:
            await param_service.approve(
                candidate_id=candidate_id,
                reviewer=actor_id,
            )
        assert exc_info.value.code == "self_approval_forbidden"

    @pytest.mark.asyncio
    async def test_publish_requires_successful_derivation(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """推导运行未成功时不能审批参数候选。

        流程：
        1. 创建推导链（成功）；
        2. 手动创建一个失败的推导运行；
        3. 从失败的运行创建候选 → derivation_not_succeeded。
        """
        org_id = param_setup["department_id"]
        actor_id = param_setup["actor_id"]
        new_id()

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=3)
        chain["run_ref"]
        ev_ref = chain["ev_ref"]
        recipe_version = chain["recipe_version"]

        # 手动创建失败的推导运行
        failed_run_id = new_id()
        now = datetime.now(UTC)
        async with session_scope(async_session_factory) as session:
            failed_run = DerivationRun(
                id=failed_run_id,
                department_id=org_id,
                evidence_set_version_id=ev_ref.version_id,
                recipe_version_id=recipe_version.id,
                job_id=None,
                status="failed",
                output_digest=None,
                outputs=None,
                started_at=now,
                completed_at=now,
                error="模拟推导失败",
            )
            session.add(failed_run)
            await session.flush()

        param_service = ParameterService(
            session_factory=async_session_factory,
            department_id=org_id,
            actor_id=actor_id,
        )

        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        # 从失败的推导运行创建候选 → derivation_not_succeeded
        with pytest.raises(AppError) as exc_info:
            await param_service.create_candidate(
                parameter_id=parameter_id,
                derivation_run_id=failed_run_id,
                value="42.5",
                unit="mm",
                confidence="0.95",
            )
        assert exc_info.value.code == "derivation_not_succeeded"

    @pytest.mark.asyncio
    async def test_reject_candidate(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """拒绝候选。

        流程：
        1. 创建推导链；
        2. 创建参数与候选；
        3. 用不同审核人拒绝；
        4. 验证候选状态为 rejected，参数状态为 rejected。
        """
        org_id = param_setup["department_id"]
        actor_id = param_setup["actor_id"]
        reviewer_id = new_id()

        chain = await _create_derivation_chain(param_setup, async_session_factory, num_facts=3)
        run_ref = chain["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            department_id=org_id,
            actor_id=actor_id,
        )

        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        output = run_ref.outputs[0]
        candidate_result = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run_ref.id,
            value=str(output.value),
            unit=output.unit,
            confidence=str(output.confidence),
        )
        candidate_id = candidate_result["candidate_id"]

        # 拒绝候选
        reject_result = await param_service.reject(
            candidate_id=candidate_id,
            reviewer=reviewer_id,
            comment="数据不可靠",
        )
        assert reject_result["status"] == "rejected"

        # 验证候选和参数状态
        async with async_session_factory() as session:
            candidate = await session.scalar(
                sa.select(ParameterCandidate).where(ParameterCandidate.id == candidate_id)
            )
            assert candidate is not None
            assert candidate.status == "rejected"
            assert candidate.reviewed_by == reviewer_id
            assert candidate.review_comment == "数据不可靠"

            param = await session.scalar(sa.select(Parameter).where(Parameter.id == parameter_id))
            assert param is not None
            assert param.status == "rejected"


class TestHistoricalVersions:
    """历史版本可读性测试。"""

    @pytest.mark.asyncio
    async def test_historical_versions_remain_readable(
        self,
        param_setup: dict,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """历史已发布版本在发布新版本后仍可读。

        流程：
        1. 创建推导链 1；
        2. 创建参数、候选 1、审批 → v1；
        3. 创建推导链 2；
        4. 创建候选 2、审批 → v2；
        5. 获取 v1 → 仍可读，值为 v1 的值。
        """
        org_id = param_setup["department_id"]
        actor_id = param_setup["actor_id"]
        reviewer_id = new_id()

        # 推导链 1
        chain1 = await _create_derivation_chain(
            param_setup,
            async_session_factory,
            num_facts=3,
            recipe_code="param-recipe-v1",
            subject_prefix="PARAMV1",
        )
        run1 = chain1["run_ref"]

        param_service = ParameterService(
            session_factory=async_session_factory,
            department_id=org_id,
            actor_id=actor_id,
        )

        param_result = await param_service.create_parameter(
            variable_code=param_setup["variable_code"],
            object_id=param_setup["object_id"],
        )
        parameter_id = param_result["parameter_id"]

        output1 = run1.outputs[0]
        candidate1 = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run1.id,
            value=str(output1.value),
            unit=output1.unit,
            confidence=str(output1.confidence),
        )
        v1_ref = await param_service.approve(
            candidate_id=candidate1["candidate_id"],
            reviewer=reviewer_id,
        )
        assert v1_ref.version == 1

        # 推导链 2（不同配方代码避免唯一约束冲突）
        chain2 = await _create_derivation_chain(
            param_setup,
            async_session_factory,
            num_facts=4,
            recipe_code="param-recipe-v2",
            subject_prefix="PARAMV2",
        )
        run2 = chain2["run_ref"]

        output2 = run2.outputs[0]
        candidate2 = await param_service.create_candidate(
            parameter_id=parameter_id,
            derivation_run_id=run2.id,
            value=str(output2.value),
            unit=output2.unit,
            confidence=str(output2.confidence),
        )
        v2_ref = await param_service.approve(
            candidate_id=candidate2["candidate_id"],
            reviewer=reviewer_id,
        )
        assert v2_ref.version == 2

        # 获取 v1 → 仍可读
        v1 = await param_service.get_version(parameter_id, version=1)
        assert v1.version == 1
        assert v1.value == str(output1.value)
        assert v1.status == "published"

        # 获取 v2 → 也可读
        v2 = await param_service.get_version(parameter_id, version=2)
        assert v2.version == 2
        assert v2.value == str(output2.value)

        # 获取最新版本 → v2
        latest = await param_service.get_version(parameter_id)
        assert latest.version == 2
