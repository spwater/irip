"""研究域证据快照服务：EvidenceSnapshotService。

EvidenceSnapshotService 负责证据快照的冻结逻辑：
1. 列出 active 证据引用；
2. 逐条通过 CoreFactProvider 校验权限（P1-5）；
3. 逐条获取字段清单；
4. 计算内容哈希（SHA-256）；
5. 构建权限包络；
6. 构建字段清单；
7. 获取当前快照编号 + 1；
8. 插入 snapshot 记录；
9. 审计。

哈希计算约定（Q3）：
- 范围：实际引用字段清单对应的数据；
- 按 (namespace, id, field_name) 排序后 JSON 序列化（sort_keys=True）；
- hashlib.sha256(json_bytes).hexdigest()。

参照 packages/facts/service.py 的 ScopedSessionMixin 模式。
"""

import hashlib
import json
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.models import FactSummary, SnapshotRef
from packages.research.repository import ResearchRepository
from packages.research.service import CoreFactProviderProtocol

logger = logging.getLogger("research.snapshots")


class EvidenceSnapshotService(ScopedSessionMixin):
    """证据快照业务编排服务。

    依赖注入 session_factory、department_id、actor_id、fact_provider。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _fact_provider: CoreFactProvider 只读适配器。
        _rls_dept_id: RLS 部门 ID（平台管理员绕过隔离，可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        fact_provider: CoreFactProviderProtocol,
        lineage_writer: object | None = None,
    ) -> None:
        """初始化证据快照服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            fact_provider: CoreFactProvider 只读适配器。
            lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._fact_provider = fact_provider
        self._lineage_writer = lineage_writer
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    async def freeze_snapshot(self, workspace_id: UUID) -> SnapshotRef:
        """冻结证据快照。

        流程：
        1. 列出 active 证据引用；
        2. 逐条校验权限 + 获取字段清单 + 获取 Fact 数据；
        3. 计算内容哈希；
        4. 构建权限包络；
        5. 构建字段清单；
        6. 获取当前快照编号 + 1；
        7. 插入 snapshot 记录；
        8. 审计。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            SnapshotRef: 快照引用。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
            AppError: code="validation_failed"，当无活跃证据时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 校验工作空间归属
            workspace = await ResearchRepository.get_workspace(session, workspace_id, actor_id)
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 1. 列出 active 证据引用
            refs = await ResearchRepository.list_evidence_refs(
                session, workspace_id, status="active"
            )
            if not refs:
                raise AppError(
                    code="validation_failed",
                    message="无活跃证据引用，无法冻结快照",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 2. 逐条校验权限 + 获取字段清单 + Fact 数据
            fact_summaries: dict[UUID, FactSummary] = {}
            fact_fields_map: dict[UUID, list[str]] = {}
            fact_data_map: dict[UUID, dict] = {}

            # research:derived 引用的 DerivedDatasetVersion content_hash 映射
            derived_data_map: dict[UUID, dict] = {}

            for ref in refs:
                if ref.source_namespace == "core:fact":
                    # 权限运行期校验（P1-5）：无权访问时 raise AppError(forbidden)
                    summary = await self._fact_provider.get_fact_summary(ref.source_id)
                    fact_summaries[ref.source_id] = summary

                    # 获取字段清单
                    fields = await self._fact_provider.get_fact_fields(ref.source_id)
                    fact_fields_map[ref.source_id] = fields

                    # 获取 Fact 数据（用于哈希计算）
                    fact_data = await self._get_fact_data_for_hash(ref.source_id)
                    fact_data_map[ref.source_id] = fact_data

                elif ref.source_namespace == "research:derived":
                    # 阶段 3：从 DerivedDatasetVersion 获取 content_hash + 三段式数据
                    derived_data = await self._get_derived_data_for_hash(
                        ref.source_id, ref.source_version
                    )
                    if derived_data is not None:
                        derived_data_map[ref.source_id] = derived_data
                        # 权限包络中记录 DerivedDataset 权限
                        fact_summaries[ref.source_id] = FactSummary(
                            fact_id=ref.source_id,
                            fact_type="research:derived",
                            subject_id=derived_data.get("name", ""),
                            status="confirmed",
                            department_name=None,
                        )
                        fact_fields_map[ref.source_id] = derived_data.get("field_names", [])

                elif ref.source_namespace == "research:published_derived":
                    # 阶段 4：从已发布成果包的 DerivedDatasetVersion 获取 content_hash
                    published_data = await self._get_published_derived_data_for_hash(
                        ref.source_id, ref.source_version
                    )
                    if published_data is not None:
                        derived_data_map[ref.source_id] = published_data
                        fact_summaries[ref.source_id] = FactSummary(
                            fact_id=ref.source_id,
                            fact_type="research:published_derived",
                            subject_id=published_data.get("name", ""),
                            status="published",
                            department_name=None,
                        )
                        fact_fields_map[ref.source_id] = published_data.get("field_names", [])

            # 3. 计算内容哈希
            content_hash = self._compute_content_hash(
                refs, fact_fields_map, fact_data_map, derived_data_map
            )

            # 4. 构建权限包络
            permission_envelope = self._build_permission_envelope(refs, fact_summaries)

            # 5. 构建字段清单
            field_manifest = self._build_field_manifest(refs, fact_fields_map)

            # 6. 构建源引用列表
            source_refs = [
                {
                    "namespace": ref.source_namespace,
                    "id": str(ref.source_id),
                    "version": ref.source_version,
                }
                for ref in refs
            ]

            # 7. 获取当前快照编号 + 1
            latest = await ResearchRepository.get_latest_snapshot(session, workspace_id)
            snapshot_number = (latest.snapshot_number + 1) if latest else 1

            # 8. 插入 snapshot 记录
            snapshot = await ResearchRepository.insert_snapshot(
                session,
                workspace_id=workspace_id,
                snapshot_number=snapshot_number,
                content_hash=content_hash,
                permission_envelope=permission_envelope,
                field_manifest=field_manifest,
                source_refs=source_refs,
                created_by=actor_id,
            )

            # 9. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.snapshot.freeze",
                    actor_user_id=actor_id,
                    resource_type="research_evidence_snapshot",
                    resource_id=snapshot.id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "snapshot_number": snapshot_number,
                        "content_hash": content_hash[:16],
                    },
                ),
            )

            result = SnapshotRef(
                snapshot_id=snapshot.id,
                snapshot_number=snapshot_number,
                content_hash=content_hash,
                captured_at=snapshot.captured_at,
            )
            _hook_snapshot_id = snapshot.id
            _hook_source_refs = source_refs

        # ── 阶段 5：溯源边写入 Hook（不阻断主流程） ──
        if self._lineage_writer is not None:
            try:
                await self._lineage_writer.on_snapshot_frozen(_hook_snapshot_id, _hook_source_refs)
            except Exception as exc:
                logger.warning("on_snapshot_frozen hook failed: %s", exc)

        return result

    async def list_snapshots(self, workspace_id: UUID) -> list[SnapshotRef]:
        """列出工作空间的全部快照。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[SnapshotRef]: 快照引用列表。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(session, workspace_id, actor_id)
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            snapshots = await ResearchRepository.list_snapshots(session, workspace_id)
            return [
                SnapshotRef(
                    snapshot_id=s.id,
                    snapshot_number=s.snapshot_number,
                    content_hash=s.content_hash,
                    captured_at=s.captured_at,
                )
                for s in snapshots
            ]

    async def _get_fact_data_for_hash(self, fact_id: UUID) -> dict:
        """获取 Fact 数据用于哈希计算。

        通过 CoreFactProvider 获取 Fact 的字段值数据。CoreFactProviderImpl
        内部调用 FactQueryService.get_fact_data() 获取 metadata/points/series。

        Args:
            fact_id: Fact UUID。

        Returns:
            dict: Fact 数据字典。
        """
        # CoreFactProvider 接口提供 get_fact_fields 获取字段名列表。
        # 哈希计算需要实际数据值，通过 fact_provider 的内部实现获取。
        # CoreFactProviderImpl 封装了 FactQueryService.get_fact_data()。
        # 此处通过 duck typing 调用 get_fact_data 方法（由 CoreFactProviderImpl 实现）。
        get_data = getattr(self._fact_provider, "get_fact_data", None)
        if get_data is not None:
            return await get_data(fact_id)
        # 如果 fact_provider 不提供 get_fact_data，返回空字典（哈希仅基于字段名）
        return {}

    async def _get_derived_data_for_hash(
        self,
        dataset_id: UUID,
        source_version: str | None,
    ) -> dict | None:
        """获取 DerivedDatasetVersion 数据用于哈希计算（阶段 3 新增）。

        从 research:derived 引用中获取 DerivedDatasetVersion 的三段式数据和 content_hash。

        Args:
            dataset_id: DerivedDataset UUID。
            source_version: 版本号字符串（如 "1"）。

        Returns:
            dict | None: 数据字典（含 name/field_names/content_hash/metadata/points/series），
                不存在时返回 None。
        """
        from packages.research.repository import ResearchRepository

        # 解析版本号
        version_number = 1
        if source_version is not None:
            try:
                version_number = int(source_version)
            except (ValueError, TypeError):
                version_number = 1

        # 使用工厂创建独立 session 查询（不使用 _scoped_session 避免嵌套事务）
        async with self._factory() as session:
            dataset = await ResearchRepository.get_dataset(session, dataset_id)
            if dataset is None:
                return None

            version = await ResearchRepository.get_dataset_version(
                session, dataset_id, version_number
            )
            if version is None:
                # 尝试获取最新版本
                version = await ResearchRepository.get_latest_dataset_version(session, dataset_id)
                if version is None:
                    return None

            # 从 field_manifest 提取字段名列表
            field_names = [
                fm.get("field_name", "")
                for fm in (version.field_manifest or [])
                if isinstance(fm, dict)
            ]

            return {
                "name": dataset.name,
                "field_names": field_names,
                "content_hash": version.content_hash,
                "metadata": version.metadata_content,
                "points": version.points_content,
                "series": version.series_content,
            }

    async def _get_published_derived_data_for_hash(
        self,
        dataset_id: UUID,
        source_version: str | None,
    ) -> dict | None:
        """获取已发布 DerivedDatasetVersion 数据用于哈希计算（阶段 4 新增）。

        从 research:published_derived 引用中获取 DerivedDatasetVersion 的三段式数据
        和 content_hash。逻辑与 _get_derived_data_for_hash 相同，因为已发布
        DerivedDataset 的版本内容在 DerivedDatasetVersion 表中依然存在。

        Args:
            dataset_id: DerivedDataset UUID。
            source_version: 版本号字符串（如 "1"）。

        Returns:
            dict | None: 数据字典（含 name/field_names/content_hash/metadata/points/series），
                不存在时返回 None。
        """
        # 已发布 DerivedDataset 的版本数据仍在 DerivedDatasetVersion 表中，
        # 复用 _get_derived_data_for_hash 的逻辑即可。
        return await self._get_derived_data_for_hash(dataset_id, source_version)

    def _compute_content_hash(
        self,
        refs: list,
        fact_fields_map: dict[UUID, list[str]],
        fact_data_map: dict[UUID, dict],
        derived_data_map: dict[UUID, dict] | None = None,
    ) -> str:
        """计算内容哈希（SHA-256）。

        按 (namespace, id, field_name) 排序后 JSON 序列化（sort_keys=True, ensure_ascii=False）。
        对 research:derived 引用，将 DerivedDatasetVersion 的三段式数据纳入哈希计算。

        Args:
            refs: 证据引用列表。
            fact_fields_map: Fact ID → 字段名列表。
            fact_data_map: Fact ID → 数据字典。
            derived_data_map: DerivedDataset ID → 数据字典（阶段 3 新增）。

        Returns:
            str: 64 字符十六进制 SHA-256 哈希。
        """
        # 收集所有 (namespace, source_id, field_name, value) 元组
        entries: list[dict] = []
        for ref in refs:
            fact_id = ref.source_id

            if (
                ref.source_namespace == "research:derived"
                or ref.source_namespace == "research:published_derived"
            ):
                # 阶段 3：DerivedDataset 数据纳入哈希
                derived_data = (derived_data_map or {}).get(fact_id, {})
                fields = derived_data.get("field_names", [])
                for field_name in fields:
                    entries.append(
                        {
                            "namespace": ref.source_namespace,
                            "id": str(fact_id),
                            "field": field_name,
                            "value": None,  # DerivedDataset 字段值通过 content_hash 间接校验
                        }
                    )
                # 将 content_hash 作为额外条目纳入
                content_hash = derived_data.get("content_hash", "")
                if content_hash:
                    entries.append(
                        {
                            "namespace": ref.source_namespace,
                            "id": str(fact_id),
                            "field": "_content_hash",
                            "value": content_hash,
                        }
                    )
            else:
                # core:fact 引用
                fields = fact_fields_map.get(fact_id, [])
                fact_data = fact_data_map.get(fact_id, {})

                for field_name in fields:
                    value = self._extract_field_value(fact_data, field_name)
                    entries.append(
                        {
                            "namespace": ref.source_namespace,
                            "id": str(fact_id),
                            "field": field_name,
                            "value": value,
                        }
                    )

        # 按 (namespace, id, field) 排序
        entries.sort(key=lambda e: (e["namespace"], e["id"], e["field"]))

        # 序列化为 JSON
        json_bytes = json.dumps(
            entries,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(json_bytes).hexdigest()

    def _extract_field_value(self, fact_data: dict, field_name: str) -> object:
        """从 Fact 数据字典中提取字段值。

        Fact 数据格式为 {"metadata": {...}, "points": [...], "series": [...]}。
        从 points 和 metadata 中搜索匹配的字段名。

        Args:
            fact_data: Fact 数据字典。
            field_name: 字段名。

        Returns:
            字段值（未找到时返回 None）。
        """
        # 从 metadata 中查找
        metadata = fact_data.get("metadata", {})
        if isinstance(metadata, dict) and field_name in metadata:
            return metadata[field_name]

        # 从 points 中查找（points 为 [{name, value, ...}] 格式）
        points = fact_data.get("points", [])
        if isinstance(points, list):
            for point in points:
                if isinstance(point, dict) and point.get("name") == field_name:
                    return point.get("value")

        return None

    def _build_permission_envelope(
        self,
        refs: list,
        fact_summaries: dict[UUID, FactSummary],
    ) -> dict:
        """构建权限包络。

        记录每个 source 的权限快照。

        Args:
            refs: 证据引用列表。
            fact_summaries: Fact ID → FactSummary 映射。

        Returns:
            dict: 权限包络，如 {fact_id_str: {fact_type, status, department_name}}。
        """
        envelope: dict[str, dict] = {}
        for ref in refs:
            summary = fact_summaries.get(ref.source_id)
            if summary is not None:
                envelope[str(ref.source_id)] = {
                    "fact_type": summary.fact_type,
                    "status": summary.status,
                    "department_name": summary.department_name,
                }
        return envelope

    def _build_field_manifest(
        self,
        refs: list,
        fact_fields_map: dict[UUID, list[str]],
    ) -> dict:
        """构建字段清单。

        Args:
            refs: 证据引用列表。
            fact_fields_map: Fact ID → 字段名列表。

        Returns:
            dict: 字段清单，如 {fact_id_str: ["字段名1", "字段名2"]}。
        """
        manifest: dict[str, list[str]] = {}
        for ref in refs:
            fields = fact_fields_map.get(ref.source_id, [])
            manifest[str(ref.source_id)] = list(fields)
        return manifest
