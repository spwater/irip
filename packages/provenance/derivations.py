"""推导运行服务（IRIP Task 17）。

DerivationService 提供推导运行的创建、回放、查询与列表。

核心不变量：
1. deterministic: 相同证据集版本 + 相同配方版本 → 相同 output_digest。
2. replay_equality: 回放产生的运行与原运行具有相同 output_digest，但不同 id。
3. provenance_edges: 每次推导运行创建溯源边，连接到所用的事实修订。

依赖注入 session_factory（事务管理）、organization_id（当前组织）、
actor_id（操作人）。
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.facts.entities import NormalizedObservation
from packages.provenance.algorithms import (
    ParameterCandidateOutput,
    get_executor,
)
from packages.provenance.entities import (
    DerivationRun,
    EvidenceSetVersion,
    ProvenanceEdge,
    TransformationRecipeVersion,
)


@dataclass(frozen=True)
class DerivationRunRef:
    """推导运行引用（不可变值对象）。

    Attributes:
        id: 运行 UUID。
        status: 状态（pending / running / succeeded / failed）。
        output_digest: 输出 SHA-256 摘要。
        outputs: 输出候选元组。
    """

    id: UUID
    status: str
    output_digest: str
    outputs: tuple[ParameterCandidateOutput, ...]


def _compute_output_digest(
    outputs: tuple[ParameterCandidateOutput, ...],
) -> str:
    """计算输出的 SHA-256 摘要（确定性，按 key 排序）。

    Args:
        outputs: 输出候选元组。

    Returns:
        str: SHA-256 十六进制摘要。
    """
    serialized: list[dict] = []
    for out in outputs:
        serialized.append(
            {
                "variable_code": out.variable_code,
                "value": str(out.value),
                "unit": out.unit,
                "confidence": out.confidence,
                "exclusion_reasons": list(out.exclusion_reasons),
            }
        )
    # 按变量代码排序保证确定性
    serialized.sort(key=lambda x: x["variable_code"])
    json_str: str = json.dumps(
        serialized, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


def _output_to_dict(out: ParameterCandidateOutput) -> dict:
    """将 ParameterCandidateOutput 序列化为 JSONB 可存储的字典。"""
    return {
        "variable_code": out.variable_code,
        "value": str(out.value),
        "unit": out.unit,
        "confidence": out.confidence,
        "exclusion_reasons": list(out.exclusion_reasons),
    }


def _output_from_dict(d: dict) -> ParameterCandidateOutput:
    """从字典反序列化 ParameterCandidateOutput。"""
    return ParameterCandidateOutput(
        variable_code=d["variable_code"],
        value=Decimal(str(d["value"])),
        unit=d.get("unit"),
        confidence=float(d["confidence"]),
        exclusion_reasons=tuple(d.get("exclusion_reasons", [])),
    )


class DerivationService:
    """推导运行业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    actor_id（操作人）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化推导运行服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_run(
        self,
        evidence_set_version_id: UUID,
        recipe_version_id: UUID,
    ) -> DerivationRunRef:
        """创建推导运行并执行。

        流程：
        1. 加载证据集版本成员；
        2. 加载配方版本；
        3. 按 (component_name, component_version) 查找执行器；
        4. 未找到 → raise AppError(code="component_unavailable")；
        5. 从成员的事实修订中提取标准化观察值；
        6. 执行算法；
        7. 计算 output_digest = SHA-256(outputs JSON, sorted keys)；
        8. 创建 derivation_run 记录；
        9. 创建溯源边（derivation_run → selected_from → fact_revisions）；
        10. 返回 DerivationRunRef。

        Args:
            evidence_set_version_id: 证据集版本 UUID。
            recipe_version_id: 配方版本 UUID。

        Returns:
            DerivationRunRef: 推导运行引用。

        Raises:
            AppError: code="not_found"，当证据集版本或配方版本不存在时。
            AppError: code="component_unavailable"，当执行器未找到时。
            AppError: code="recipe_not_published"，当配方版本未发布时。
        """
        async with session_scope(self._factory) as session:
            # 1. 加载证据集版本
            ev_version = await session.scalar(
                sa.select(EvidenceSetVersion).where(
                    EvidenceSetVersion.id == evidence_set_version_id,
                )
            )
            if ev_version is None:
                raise AppError(
                    code="not_found",
                    message=f"证据集版本不存在: {evidence_set_version_id}",
                    retryable=False,
                    fields={"evidence_set_version_id": str(evidence_set_version_id)},
                )

            # 2. 加载配方版本
            recipe_version = await session.scalar(
                sa.select(TransformationRecipeVersion).where(
                    TransformationRecipeVersion.id == recipe_version_id,
                )
            )
            if recipe_version is None:
                raise AppError(
                    code="not_found",
                    message=f"配方版本不存在: {recipe_version_id}",
                    retryable=False,
                    fields={"recipe_version_id": str(recipe_version_id)},
                )

            if recipe_version.status != "published":
                raise AppError(
                    code="recipe_not_published",
                    message="配方版本未发布",
                    retryable=False,
                    fields={"recipe_version_id": str(recipe_version_id)},
                )

            # 3. 查找执行器
            executor = get_executor(
                recipe_version.component_name,
                recipe_version.component_version,
            )
            if executor is None:
                raise AppError(
                    code="component_unavailable",
                    message=(
                        f"推导执行器不可用: "
                        f"{recipe_version.component_name}@"
                        f"{recipe_version.component_version}"
                    ),
                    retryable=False,
                    fields={
                        "component_name": recipe_version.component_name,
                        "component_version": recipe_version.component_version,
                    },
                )

            # 4. 从成员的事实修订中提取标准化观察值
            members_list: list = ev_version.members or []
            fact_revision_ids: list[UUID] = [
                UUID(str(m["fact_revision_id"]))
                for m in members_list
                if m.get("decision") == "included" and m.get("fact_revision_id")
            ]

            # 查询标准化观察值
            values: list[Decimal] = []
            if fact_revision_ids:
                norm_result = await session.execute(
                    sa.select(NormalizedObservation)
                    .where(
                        NormalizedObservation.fact_revision_id.in_(
                            fact_revision_ids
                        )
                    )
                    .order_by(NormalizedObservation.fact_revision_id)
                )
                norm_observations = norm_result.scalars().all()
                for norm in norm_observations:
                    try:
                        values.append(Decimal(norm.value))
                    except Exception:
                        # 跳过无法转换为 Decimal 的值
                        continue

            # 5. 执行算法
            # 合并配方参数和随机种子
            algo_params: dict[str, object] = dict(recipe_version.parameters or {})
            algo_params["random_seed"] = recipe_version.random_seed

            output_defs: tuple[str, ...] = tuple(
                recipe_version.output_definitions or []
            )

            outputs_list: list[ParameterCandidateOutput] = []
            if output_defs:
                # 为每个输出定义运行执行器
                for out_def in output_defs:
                    result = executor.execute(values, algo_params)
                    # 覆盖 variable_code 为输出定义
                    outputs_list.append(
                        ParameterCandidateOutput(
                            variable_code=out_def,
                            value=result.value,
                            unit=result.unit,
                            confidence=result.confidence,
                            exclusion_reasons=result.exclusion_reasons,
                        )
                    )
            else:
                # 无输出定义时，使用执行器默认输出
                result = executor.execute(values, algo_params)
                outputs_list.append(result)

            outputs: tuple[ParameterCandidateOutput, ...] = tuple(outputs_list)

            # 6. 计算 output_digest
            output_digest: str = _compute_output_digest(outputs)

            # 7. 创建 derivation_run 记录
            run_id: UUID = new_id()
            now: datetime = datetime.now(UTC)
            run = DerivationRun(
                id=run_id,
                organization_id=self._org_id,
                evidence_set_version_id=evidence_set_version_id,
                recipe_version_id=recipe_version_id,
                job_id=None,
                status="succeeded",
                output_digest=output_digest,
                outputs=[_output_to_dict(o) for o in outputs],
                started_at=now,
                completed_at=now,
                error=None,
            )
            session.add(run)

            # 8. 创建溯源边（derivation_run → selected_from → fact_revisions）
            for fr_id in fact_revision_ids:
                edge = ProvenanceEdge(
                    id=new_id(),
                    organization_id=self._org_id,
                    derivation_run_id=run_id,
                    source_type="derivation_run",
                    source_id=run_id,
                    target_type="fact_revision",
                    target_id=fr_id,
                    edge_type="selected_from",
                    metadata_=None,
                )
                session.add(edge)

            await session.flush()

            return DerivationRunRef(
                id=run_id,
                status="succeeded",
                output_digest=output_digest,
                outputs=outputs,
            )

    async def replay(self, run_id: UUID) -> DerivationRunRef:
        """回放推导运行：用相同输入和配方重新执行，产生新的运行。

        流程：
        1. 加载原始运行；
        2. 用相同的 evidence_set_version_id 和 recipe_version_id 创建新运行；
        3. 执行相同算法（相同参数和种子）；
        4. 验证 output_digest 与原始运行一致；
        5. 返回新的 DerivationRunRef（不同 id，相同 output_digest）。

        Args:
            run_id: 原始运行 UUID。

        Returns:
            DerivationRunRef: 新的推导运行引用。

        Raises:
            AppError: code="not_found"，当运行不存在时。
        """
        async with self._factory() as session:
            original_run = await session.scalar(
                sa.select(DerivationRun).where(DerivationRun.id == run_id)
            )
            if original_run is None:
                raise AppError(
                    code="not_found",
                    message=f"推导运行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

        # 用相同的证据集版本和配方版本创建新运行
        new_ref = await self.create_run(
            evidence_set_version_id=original_run.evidence_set_version_id,
            recipe_version_id=original_run.recipe_version_id,
        )

        # 验证 output_digest 一致（确定性保证）
        if new_ref.output_digest != original_run.output_digest:
            raise AppError(
                code="validation_failed",
                message=(
                    f"回放输出摘要不匹配: "
                    f"original={original_run.output_digest}, "
                    f"replay={new_ref.output_digest}"
                ),
                retryable=False,
                fields={
                    "original_digest": original_run.output_digest,
                    "replay_digest": new_ref.output_digest,
                },
            )

        return new_ref

    async def get_run(self, run_id: UUID) -> DerivationRunRef:
        """获取推导运行详情。

        Args:
            run_id: 运行 UUID。

        Returns:
            DerivationRunRef: 推导运行引用。

        Raises:
            AppError: code="not_found"，当运行不存在时。
        """
        async with self._factory() as session:
            run = await session.scalar(
                sa.select(DerivationRun).where(
                    DerivationRun.id == run_id,
                    DerivationRun.organization_id == self._org_id,
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"推导运行不存在: {run_id}",
                    retryable=False,
                    fields={"run_id": str(run_id)},
                )

            outputs_list: list = run.outputs or []
            outputs: tuple[ParameterCandidateOutput, ...] = tuple(
                _output_from_dict(o) for o in outputs_list
            )
            return DerivationRunRef(
                id=run.id,
                status=run.status,
                output_digest=run.output_digest or "",
                outputs=outputs,
            )

    async def list_runs(
        self,
        cursor: str | None,
        page_size: int = 20,
    ) -> tuple[list[DerivationRunRef], str | None]:
        """分页列出推导运行。

        Args:
            cursor: 分页游标（None 表示第一页）。
            page_size: 每页数量。

        Returns:
            tuple[list[DerivationRunRef], str | None]:
            (推导运行引用列表, 下一页游标)。
        """
        async with self._factory() as session:
            stmt = (
                sa.select(DerivationRun)
                .where(DerivationRun.organization_id == self._org_id)
                .order_by(DerivationRun.created_at, DerivationRun.id)
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
                            DerivationRun.created_at > cursor_time,
                            sa.and_(
                                DerivationRun.created_at == cursor_time,
                                DerivationRun.id > cursor_id,
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
            runs = result.scalars().all()

            next_cursor: str | None = None
            if len(runs) > page_size:
                last = runs[page_size - 1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {"v": last.created_at.isoformat(), "id": str(last.id)},
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).decode("ascii")

            refs: list[DerivationRunRef] = []
            for run in runs[:page_size]:
                outputs_list: list = run.outputs or []
                outputs: tuple[ParameterCandidateOutput, ...] = tuple(
                    _output_from_dict(o) for o in outputs_list
                )
                refs.append(
                    DerivationRunRef(
                        id=run.id,
                        status=run.status,
                        output_digest=run.output_digest or "",
                        outputs=outputs,
                    )
                )
            return refs, next_cursor
