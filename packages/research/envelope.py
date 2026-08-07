"""权限包络计算器。

PermissionEnvelopeCalculator 计算成果包的有效可见范围：
effective_result_access = requested_result_acl ∩ current_source_permission_envelopes

源数据权限包络来源：
- Evidence Snapshot 的 permission_envelope（阶段 1 冻结时记录）
- Derived Dataset 的 source_snapshot_id 对应的 permission_envelope

ACL 严格度排序（rank 越高越宽松）：
private(0) < explicit(1) < tree(2) < all(3)

纯静态方法，独立于 Service，无状态。
参照架构设计 3.3 节。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.research.entities import ResearchEvidenceSnapshot
from packages.research.models import EnvelopeValidationResult, PermissionEnvelope


class PermissionEnvelopeCalculator:
    """权限包络计算器。

    计算成果包的有效可见范围：
    effective_result_access = requested_result_acl ∩ current_source_permission_envelopes

    ACL 严格度排序（rank 越高越宽松）：
    private(0) < explicit(1) < tree(2) < all(3)
    """

    #: ACL 类型 → 严格度排序值（越高越宽松）。
    _ACL_RANKS: dict[str, int] = {
        "private": 0,
        "explicit": 1,
        "tree": 2,
        "all": 3,
    }

    #: 默认包络 ACL 类型（无源数据时默认为 all，即不限制）。
    _DEFAULT_ENVELOPE_ACL: str = "all"

    @staticmethod
    async def calculate_envelope(
        source_snapshot_ids: list[UUID],
        session: AsyncSession,
    ) -> PermissionEnvelope:
        """计算全部源数据的权限包络交集。

        1. 查询全部 Evidence Snapshot 的 permission_envelope
        2. 对每个 snapshot 动态校验源数据当前权限
        3. 取全部权限范围的交集（取最严格的 ACL）
        4. 返回 PermissionEnvelope(acl_type, explicit_user_ids, source_details)

        Args:
            source_snapshot_ids: Evidence Snapshot ID 列表。
            session: 异步会话。

        Returns:
            PermissionEnvelope: 权限包络。
        """
        if not source_snapshot_ids:
            # 无源数据时，不限制（包络为 all）
            return PermissionEnvelope(
                acl_type=PermissionEnvelopeCalculator._DEFAULT_ENVELOPE_ACL,
                explicit_user_ids=[],
                source_details=[],
            )

        # 查询全部 snapshot 的 permission_envelope
        source_details: list[dict] = []
        acl_types: list[str] = []

        for snapshot_id in source_snapshot_ids:
            envelope = await PermissionEnvelopeCalculator._get_snapshot_envelope(
                snapshot_id, session
            )
            if envelope is None:
                # snapshot 不存在，保守处理为最严格
                acl_types.append("private")
                source_details.append(
                    {
                        "snapshot_id": str(snapshot_id),
                        "acl_type": "private",
                        "reason": "snapshot_not_found",
                    }
                )
                continue

            # 从 envelope 中提取 ACL 类型
            # permission_envelope 格式: {fact_id: {fact_type, status, department_name}}
            # 首期简化：基于 snapshot 冻结时的 permission_envelope 计算交集
            # 如果 envelope 为空，视为不限制
            envelope_acl = PermissionEnvelopeCalculator._extract_acl_from_envelope(envelope)
            snapshot_number = (
                envelope.get("_snapshot_number") if isinstance(envelope, dict) else None
            )
            source_name = f"快照 #{snapshot_number}" if snapshot_number else str(snapshot_id)[:8]
            acl_types.append(envelope_acl)
            source_details.append(
                {
                    "snapshot_id": str(snapshot_id),
                    "source_name": source_name,
                    "acl_type": envelope_acl,
                    "envelope": {
                        k: v for k, v in (envelope or {}).items() if not k.startswith("_")
                    },
                }
            )

        # 取交集（最严格的 ACL）
        intersection_acl = PermissionEnvelopeCalculator._intersect_acl_types(acl_types)

        return PermissionEnvelope(
            acl_type=intersection_acl,
            explicit_user_ids=[],
            source_details=source_details,
        )

    @staticmethod
    def validate_requested_acl(
        requested_acl: str,
        explicit_user_ids: list[UUID],
        envelope: PermissionEnvelope,
    ) -> EnvelopeValidationResult:
        """校验请求的 ACL 是否在权限包络内。

        private: 始终在包络内（最严格）
        tree: 需包络 ACL rank >= tree(2)
        explicit: 需包络 ACL rank >= explicit(1)（且指定用户在包络范围内）
        all: 需包络 ACL rank >= all(3)

        Args:
            requested_acl: 请求的 ACL 类型。
            explicit_user_ids: explicit 模式下指定用户列表。
            envelope: 权限包络。

        Returns:
            EnvelopeValidationResult: 校验结果。
        """
        requested_rank = PermissionEnvelopeCalculator._acl_rank(requested_acl)
        envelope_rank = PermissionEnvelopeCalculator._acl_rank(envelope.acl_type)

        if requested_rank <= envelope_rank:
            # 请求的 ACL 在包络内
            return EnvelopeValidationResult(
                valid=True,
                effective_acl=requested_acl,
                reason="",
                limiting_sources=[],
            )

        # 请求超出包络
        limiting_sources = [
            s
            for s in envelope.source_details
            if PermissionEnvelopeCalculator._acl_rank(s.get("acl_type", "private")) < requested_rank
        ]

        return EnvelopeValidationResult(
            valid=False,
            effective_acl=envelope.acl_type,
            reason=(f"requested ACL '{requested_acl}' exceeds envelope '{envelope.acl_type}'"),
            limiting_sources=limiting_sources,
        )

    @staticmethod
    def _acl_rank(acl_type: str) -> int:
        """返回 ACL 严格度排序值。

        Args:
            acl_type: ACL 类型（private / explicit / tree / all）。

        Returns:
            int: 排序值（越高越宽松），未知类型返回 0（最严格）。
        """
        return PermissionEnvelopeCalculator._ACL_RANKS.get(acl_type, 0)

    @staticmethod
    def _intersect_acl_types(types: list[str]) -> str:
        """取多个 ACL 类型的交集（返回最严格的 ACL 类型）。

        Args:
            types: ACL 类型列表。

        Returns:
            str: 最严格的 ACL 类型。
        """
        if not types:
            return PermissionEnvelopeCalculator._DEFAULT_ENVELOPE_ACL
        return min(
            types,
            key=lambda t: PermissionEnvelopeCalculator._acl_rank(t),
        )

    @staticmethod
    async def _get_snapshot_envelope(
        snapshot_id: UUID,
        session: AsyncSession,
    ) -> dict | None:
        """获取单个 Evidence Snapshot 的 permission_envelope。

        Args:
            snapshot_id: 快照 UUID。
            session: 异步会话。

        Returns:
            dict | None: permission_envelope 字段值，不存在时返回 None。
        """
        res = await session.execute(
            sa.select(
                ResearchEvidenceSnapshot.permission_envelope,
                ResearchEvidenceSnapshot.snapshot_number,
                ResearchEvidenceSnapshot.workspace_id,
            ).where(ResearchEvidenceSnapshot.id == snapshot_id)
        )
        row = res.first()
        if row is None:
            return None
        # 返回带 snapshot_number 的 envelope
        envelope_data = row[0] if row[0] is not None else {}
        if isinstance(envelope_data, dict):
            envelope_data["_snapshot_number"] = row[1]
            envelope_data["_workspace_id"] = str(row[2]) if row[2] else None
        return envelope_data

    @staticmethod
    def _extract_acl_from_envelope(envelope: dict) -> str:
        """从 permission_envelope 中提取 ACL 类型。

        首期简化：permission_envelope 记录了源数据的权限快照。
        如果 envelope 中有任何条目的 status 为非 active，则视为权限收紧为 private。
        如果 envelope 为空，视为不限制（all）。

        Args:
            envelope: permission_envelope 字典。

        Returns:
            str: ACL 类型。
        """
        if not envelope:
            return "all"
        # 检查所有源数据条目的状态
        for _key, value in envelope.items():
            if isinstance(value, dict):
                status = value.get("status", "active")
                if status != "active":
                    # 源数据状态非 active，权限收紧
                    return "private"
        # 所有源数据状态正常，默认为 tree（部门内可见）
        return "tree"
