"""L2 事实查询服务（复杂读）。

FactQueryService 承担所有读投影：
- 快照富化（fetch_snapshots + coalesce task_name）；
- group_counts 统计；
- data_summary 构建（JSON Artifact 下载 + 解析）；
- search_by_data（通用 KV 索引搜索）；
- get_fact_data（含 alembic-URL 超管引擎绕过 RLS 补查跨部门元数据）。

依赖注入 session_factory（事务管理）、department_id（当前部门）、
actor_id（操作人）、s3_repo（对象存储）。读/写分离后 FactService 保持精简，
FactQueryService 承担所有读投影。

session 语义：
- 读带 RLS → ScopedSessionMixin._scoped_session()（设 dept+user GUC）；
- data_summary 与 snap+count 分独立 session（避免 Artifact 下载导致
  ResourceClosedError）；
- get_fact_data 的 alembic-URL 超管引擎在 _resolve_task_info 内原样搬迁，
  不改连接/SQL/GUC 细节。
"""

import json
import logging
import os
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.artifacts import ArtifactService
from packages.common.database import ScopedSessionMixin
from packages.common.tenant_guc import set_dept_guc, set_user_guc
from packages.facts.observations import FactDetailRow
from packages.facts.repository import FactRepository

_logger = logging.getLogger(__name__)


class FactQueryService(ScopedSessionMixin):
    """事实查询服务（复杂读）。

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    actor_id（操作人）、s3_repo（对象存储）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _s3_repo: S3 对象存储客户端。
        _rls_dept_id: RLS 部门 ID（平台管理员绕过隔离，可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        s3_repo: object,
        rls_dept_id: UUID | None = None,
    ) -> None:
        """初始化事实查询服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID（可选，用于 ArtifactService uploaded_by）。
            s3_repo: S3 对象存储客户端封装。
            rls_dept_id: RLS 部门 ID（平台管理员绕过隔离，可选）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._s3_repo = s3_repo
        self._rls_dept_id = rls_dept_id

    # ---- 公开只读属性（同 FactService） ----

    @property
    def department_id(self) -> UUID:
        """当前部门 ID。"""
        return self._dept_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂。"""
        return self._factory

    @property
    def actor_id(self) -> UUID | None:
        """当前操作人 ID。"""
        return self._actor_id

    # ---- 内部辅助 ----

    def _artifact_service(self) -> ArtifactService:
        """构建 ArtifactService 实例（用 s3_repo + session_factory + dept + actor）。"""
        return ArtifactService(
            s3_repo=self._s3_repo,  # type: ignore[arg-type]
            session_factory=self._factory,
            department_id=self._dept_id,
            uploaded_by=self._actor_id,  # type: ignore[arg-type]
        )

    # ---- 公开读方法 ----

    async def list_facts_detail(
        self,
        *,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactDetailRow], str | None, dict[str, int]]:
        """分页列出事实（含快照富化 + group_counts + data_summary）。

        流程：
        1. repo.list_facts → 基础 dict + fact_ids + next_cursor；
        2. session A：fetch_snapshots(include_project=True, with_task_code_fallback=True)
           + count_group_by_task(None)；
        3. session B（独立，避免 ResourceClosedError）：
           find_json_artifact + _build_data_summary；
        4. 组装 FactDetailRow。

        Args:
            filters: 过滤条件字典（fact_type, object_id, status）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactDetailRow], str | None, dict[str, int]]:
            (事实详情列表, 下一页游标, group_counts)。
        """
        # Step 1: 基础分页查询
        async with self._scoped_session() as session:
            items, next_cursor = await FactRepository.list_facts(
                session,
                org_id=self._dept_id,
                filters=filters,
                cursor=cursor,
                page_size=page_size,
            )
        if not items:
            return [], next_cursor, {}

        fact_ids = [item["fact_id"] for item in items]

        # Step 2: 快照富化 + group_counts（session A）
        async with self._scoped_session() as session:
            snap_map = await FactRepository.fetch_snapshots(
                session,
                fact_ids,
                include_project=True,
                with_task_code_fallback=True,
            )
            group_counts = await FactRepository.count_group_by_task(session, None)

        # Step 3: data_summary（session B，独立避免 ResourceClosedError）
        artifact_svc = self._artifact_service()
        data_summaries: dict[UUID, str | None] = {}
        async with self._scoped_session() as session:
            for fid in fact_ids:
                try:
                    data_summaries[fid] = await self._build_data_summary(fid, session, artifact_svc)
                except Exception:
                    _logger.warning("生成 data_summary 失败", exc_info=True)
                    data_summaries[fid] = None

        # Step 4: 组装 FactDetailRow
        rows: list[FactDetailRow] = []
        for item in items:
            fid: UUID = item["fact_id"]
            snap = snap_map.get(fid)
            rows.append(
                FactDetailRow(
                    fact_id=fid,
                    fact_type=item["fact_type"],
                    subject_id=item["subject_id"],
                    status=item["status"],
                    task_code=snap.task_code if snap else None,
                    task_name=snap.task_name if snap else None,
                    project_name=snap.project_name if snap else None,
                    department_name=snap.department_name if snap else None,
                    operator=snap.operator if snap else None,
                    run_operator=snap.run_operator if snap else None,
                    equipment_name=snap.equipment_name if snap else None,
                    data_summary=data_summaries.get(fid),
                    created_at=snap.created_at if snap else None,
                )
            )

        return rows, next_cursor, group_counts

    async def search_facts_detail(
        self,
        *,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactDetailRow], str | None, dict[str, int]]:
        """全文搜索事实（含快照富化 + group_counts，不做 data_summary）。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件字典。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactDetailRow], str | None, dict[str, int]]:
            (事实详情列表, 下一页游标, group_counts)。
        """
        # Step 1: 基础搜索
        async with self._scoped_session() as session:
            items, next_cursor = await FactRepository.search_facts(
                session,
                query=query,
                org_id=self._dept_id,
                filters=filters,
                cursor=cursor,
                page_size=page_size,
            )
        if not items:
            return [], next_cursor, {}

        fact_ids = [item["fact_id"] for item in items]

        # Step 2: 快照富化 + group_counts（不做 data_summary，与原 search 一致）
        async with self._scoped_session() as session:
            snap_map = await FactRepository.fetch_snapshots(
                session,
                fact_ids,
                include_project=False,
            )
            group_counts = await FactRepository.count_group_by_task(session, None)

        # Step 3: 组装 FactDetailRow
        rows: list[FactDetailRow] = []
        for item in items:
            fid: UUID = item["fact_id"]
            snap = snap_map.get(fid)
            rows.append(
                FactDetailRow(
                    fact_id=fid,
                    fact_type=item["fact_type"],
                    subject_id=item["subject_id"],
                    status=item["status"],
                    task_code=snap.task_code if snap else None,
                    task_name=snap.task_name if snap else None,
                    department_name=snap.department_name if snap else None,
                    operator=snap.operator if snap else None,
                    run_operator=snap.run_operator if snap else None,
                    equipment_name=snap.equipment_name if snap else None,
                    created_at=snap.created_at if snap else None,
                )
            )

        return rows, next_cursor, group_counts

    async def search_by_data(
        self,
        *,
        q: str | None = None,
        key: str | None = None,
        value: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactDetailRow], dict[str, int]]:
        """按数据内容搜索事实（通用 KV 索引）。

        流程：
        1. session A：search_data_index → fact_ids（空则直接返回空）；
        2. fetch_snapshots(include_base=True) → snap_map；
        3. count_group_by_task(fact_ids) → group_counts；
        4. session B（独立）：find_json_artifact + _build_data_summary；
        5. 组装 FactDetailRow。

        Args:
            q: 全文搜索（匹配任意 key 或 value）。
            key: 精确匹配 key。
            value: 精确匹配 value。
            min_value: 数值下限。
            max_value: 数值上限。
            page_size: 每页数量。

        Returns:
            tuple[list[FactDetailRow], dict[str, int]]:
            (事实详情列表, group_counts)。
        """
        # Step 1-3: search_data_index + fetch_snapshots + count_group_by_task（session A）
        async with self._scoped_session() as session:
            fact_ids = await FactRepository.search_data_index(
                session,
                q=q,
                key=key,
                value=value,
                min_value=min_value,
                max_value=max_value,
                page_size=page_size,
            )
            if not fact_ids:
                return [], {}

            snap_map = await FactRepository.fetch_snapshots(
                session,
                fact_ids,
                include_project=False,
                include_base=True,
            )
            group_counts = await FactRepository.count_group_by_task(session, fact_ids)

        # Step 4: data_summary（session B，独立避免 ResourceClosedError）
        artifact_svc = self._artifact_service()
        data_summaries: dict[UUID, str | None] = {}
        async with self._scoped_session() as session:
            for fid in fact_ids:
                try:
                    data_summaries[fid] = await self._build_data_summary(fid, session, artifact_svc)
                except Exception as _e:
                    _logger.warning("生成 data_summary 失败: %s", _e, exc_info=True)
                    data_summaries[fid] = None

        # Step 5: 组装 FactDetailRow（从 snap 数据直接构建，include_base=True）
        rows: list[FactDetailRow] = []
        for fid in fact_ids:
            snap = snap_map.get(fid)
            if snap is None:
                continue
            rows.append(
                FactDetailRow(
                    fact_id=fid,
                    fact_type=snap.fact_type or "",
                    subject_id=snap.subject_id or "",
                    status=snap.status or "",
                    task_code=snap.task_code,
                    task_name=snap.task_name,
                    department_name=snap.department_name,
                    operator=snap.operator,
                    run_operator=snap.run_operator,
                    equipment_name=snap.equipment_name,
                    data_summary=data_summaries.get(fid),
                    created_at=snap.created_at,
                )
            )

        return rows, group_counts

    async def get_fact_detail(self, fact_id: UUID) -> FactDetailRow:
        """获取单个事实详情（含快照富化）。

        Args:
            fact_id: 事实 UUID。

        Returns:
            FactDetailRow: 事实详情。

        Raises:
            AppError: code="not_found"，当事实不存在时。
        """
        async with self._scoped_session() as session:
            # repo.get_fact 抛 not_found
            fact = await FactRepository.get_fact(session, fact_id, self._dept_id)
            snap_map = await FactRepository.fetch_snapshots(
                session,
                [fact_id],
                include_project=False,
                include_base=True,
            )

        snap = snap_map.get(fact_id)
        return FactDetailRow(
            fact_id=fact_id,
            fact_type=fact.fact_type,
            subject_id=fact.subject_id,
            status=fact.status,
            task_code=snap.task_code if snap else None,
            task_name=snap.task_name if snap else None,
            department_name=snap.department_name if snap else None,
            operator=snap.operator if snap else None,
            run_operator=snap.run_operator if snap else None,
            equipment_name=snap.equipment_name if snap else None,
            created_at=snap.created_at if snap else None,
        )

    async def get_fact_data(self, fact_id: UUID) -> dict:
        """获取事实关联的提取数据（从 artifact 下载 JSON）。

        完整保留原 get_fact_data 逻辑：
        1. find_json_artifact → 下载解析 JSON；
        2. _resolve_task_info（快照优先 → flow_run_id 的 alembic-URL 超管引擎
           补查 data_source_list → fallback 用 fact 自身 GUC 反查）；
        3. find_source_file_artifact → 返回 result_data dict。

        Args:
            fact_id: 事实 UUID。

        Returns:
            dict: {"metadata": ..., "points": [...], "series": [...],
            "task_info": ..., "source_file": ...} 格式的数据。

        Raises:
            AppError: code="not_found"，当事实不存在时。
        """
        async with self._scoped_session() as session:
            # repo.get_fact 抛 not_found（保持实际 404 行为）
            fact_record = await FactRepository.get_fact(session, fact_id, self._dept_id)

            # 查找 JSON artifact
            art_record = await FactRepository.find_json_artifact(session, fact_id)
            if art_record is None:
                return {"metadata": {}, "points": [], "series": []}

            # 下载 artifact 内容（MinIO 文件不存在时返回空数据而非 500）
            artifact_svc = self._artifact_service()
            data_bytes: bytes | None = None
            json_error: str | None = None
            try:
                data_bytes = await artifact_svc.get_bytes(art_record.id)
            except Exception as exc:
                _logger.warning("JSON artifact 下载失败: %s — %s", art_record.id, exc)
                json_error = str(exc)[:200]

            if data_bytes is not None:
                result_data = json.loads(data_bytes.decode("utf-8"))
            else:
                result_data = {"metadata": {}, "points": [], "series": []}

            if "points" not in result_data:
                result_data["points"] = []
            if "series" not in result_data:
                result_data["series"] = []

            # 解析任务信息（含 alembic-URL 超管引擎绕过 + fallback GUC 反查）
            task_info = await self._resolve_task_info(fact_record, session)
            if task_info:
                result_data["task_info"] = task_info

            if json_error:
                result_data["data_error"] = f"数据文件丢失: {json_error}"

            # 查原始文件（PDF 等）
            try:
                pdf_artifact = await FactRepository.find_source_file_artifact(session, fact_id)
                if pdf_artifact:
                    result_data["source_file"] = {
                        "filename": pdf_artifact.filename or "原始文件",
                        "media_type": pdf_artifact.media_type,
                        "artifact_id": str(pdf_artifact.id),
                    }
            except Exception:
                _logger.warning("查找原始文件失败", exc_info=True)

            return result_data

    # ---- 私有方法 ----

    async def _build_data_summary(
        self,
        fact_id: UUID,
        session: AsyncSession,
        artifact_service: ArtifactService,
    ) -> str | None:
        """构建数据摘要（从 JSON artifact 取前 3 行指标/序列）。

        Args:
            fact_id: 事实 UUID。
            session: 异步会话。
            artifact_service: Artifact 服务（用于下载 artifact 内容）。

        Returns:
            str | None: 数据摘要字符串，无 artifact 或解析失败时返回 None。
        """
        art_record = await FactRepository.find_json_artifact(session, fact_id)
        if art_record is None:
            return None

        data_bytes = await artifact_service.get_bytes(art_record.id)
        parsed = json.loads(data_bytes.decode("utf-8"))
        pts = parsed.get("points", [])
        srs = parsed.get("series", [])

        if pts:
            pairs = [f"{p.get('name', '')}={p.get('value', '')}" for p in pts[:3]]
            total = len(pts)
            return f"共{total}个指标：" + "，".join(pairs) + ("..." if total > 3 else "")
        elif srs:
            names = [s.get("name", f"序列{i + 1}") for i, s in enumerate(srs[:3])]
            total = len(srs)
            return f"共{total}组序列：" + "，".join(names) + ("..." if total > 3 else "")
        return None

    async def _resolve_task_info(
        self,
        fact_record: object,
        session: AsyncSession,
    ) -> dict:
        """解析任务信息（快照优先 → alembic-URL 超管引擎补查 → fallback GUC 反查）。

        优先从快照字段读任务信息（零 JOIN）。如果快照命中且有 flow_run_id，
        用 alembic-URL 超管引擎开独立 session 绕过 RLS 补查跨部门元数据
        （data_source_list 等）。如果快照没命中，fallback 到通过 flow_run_id
        外键反查（用 fact 自身 GUC 设 RLS 可见）。

        **alembic-URL 超管引擎逻辑原样搬迁，不改连接/SQL/GUC 细节。**

        Args:
            fact_record: Fact ORM 实体（含快照字段 + flow_run_id + department_id + owner_user_id）。
            session: 主 scoped session（fallback 路径用此 session 设 GUC 反查）。

        Returns:
            dict: 任务信息字典（可能为空 dict 表示未获取到任何信息）。
        """
        from packages.facts.entities import Fact

        assert isinstance(fact_record, Fact)

        # Department 在快照路径和 fallback 路径均可能使用，
        # 提前导入避免深层嵌套中行宽超限。
        from packages.departments.entities import Department  # noqa: F811

        task_info: dict = {}
        try:
            if fact_record.task_code or fact_record.task_name:
                # ---- 快照命中路径 ----
                task_info = {
                    "task_name": fact_record.task_name,
                    "task_source": fact_record.department_name,
                    "operator": fact_record.operator,
                    "run_operator": fact_record.run_operator,
                    "equipment_name": fact_record.equipment_name,
                    "project_name": None,
                    "owner_name": None,
                    "job_id": None,
                    "data_interface": None,
                    "created_at": None,
                }

                if fact_record.flow_run_id:
                    # 用 alembic URL (superuser) 开 session 绕过 RLS，仅用于补查元数据
                    _alembic_url = os.getenv("IRIP_ALEMBIC_DATABASE_URL", "")
                    if _alembic_url:
                        from sqlalchemy.ext.asyncio import (
                            async_sessionmaker as _asm,
                        )
                        from sqlalchemy.ext.asyncio import (
                            create_async_engine as _cae,
                        )

                        from packages.components.flow_runtime import (
                            FlowDefinition,
                            FlowDefinitionVersionORM,
                            FlowRun,
                        )

                        _engine = _cae(
                            _alembic_url.replace(
                                "postgresql+psycopg://",
                                "postgresql+psycopg_async://",
                                1,
                            )
                        )
                        _factory = _asm(_engine, expire_on_commit=False)

                        # 所有查询均在 async with 块内（确保 session 有效）
                        async with _factory() as sess:
                            run_stmt = sa.select(FlowRun).where(
                                FlowRun.id == fact_record.flow_run_id
                            )
                            run_record = (await sess.execute(run_stmt)).scalar_one_or_none()
                            if run_record:
                                fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                                    FlowDefinitionVersionORM.id == run_record.flow_version_id
                                )
                                fv = (await sess.execute(fv_stmt)).scalar_one_or_none()
                                if fv:
                                    fd_stmt = sa.select(FlowDefinition).where(
                                        FlowDefinition.id == fv.flow_definition_id
                                    )
                                    fd = (await sess.execute(fd_stmt)).scalar_one_or_none()
                                    if fd:
                                        nodes = fv.nodes_json or []
                                        comp_names = list(
                                            {
                                                n.get("component_name", "")
                                                for n in nodes
                                                if n.get("component_name")
                                            }
                                        )
                                        # 查 experiment_project 取项目名 + 负责人
                                        project_name = None
                                        owner_display_name = None
                                        if fd.project_id:
                                            from packages.auth.entities import AppUser
                                            from packages.experiment_project.entities import (
                                                ExperimentProject,
                                            )

                                            ep_stmt = sa.select(ExperimentProject).where(
                                                ExperimentProject.id == fd.project_id
                                            )
                                            ep = (await sess.execute(ep_stmt)).scalar_one_or_none()
                                            if ep:
                                                project_name = ep.display_name
                                                owner_stmt = sa.select(AppUser.display_name).where(
                                                    AppUser.id == ep.owner_user_id
                                                )
                                                owner_row = (
                                                    await sess.execute(owner_stmt)
                                                ).scalar_one_or_none()
                                                owner_display_name = owner_row
                                        task_info["owner_name"] = owner_display_name
                                        task_info["project_name"] = project_name
                                        task_info["job_id"] = (
                                            str(run_record.job_id) if run_record.job_id else None
                                        )
                                        task_info["created_at"] = (
                                            fd.created_at.isoformat() if fd.created_at else None
                                        )
                                        # 查所属单位名称
                                        if fd.department_id:
                                            dept_stmt = sa.select(Department).where(
                                                Department.id == fd.department_id
                                            )
                                            dept_record = (
                                                await sess.execute(dept_stmt)
                                            ).scalar_one_or_none()
                                            task_info["department_name"] = (
                                                dept_record.display_name if dept_record else None
                                            )
                                        else:
                                            task_info["department_name"] = None
                                        # 查每个组件的实验对象→设备→部门链路
                                        data_source_list = []
                                        for comp_name in comp_names:
                                            ds: dict = {"component": comp_name}
                                            # 查组件 display_name 和 experimental_object_code
                                            import yaml as yaml_lib

                                            from packages.components.registry import (
                                                Component,
                                                ComponentVersion,
                                            )

                                            cv_stmt = (
                                                sa.select(ComponentVersion)
                                                .join(
                                                    Component,
                                                    ComponentVersion.component_id == Component.id,
                                                )
                                                .where(Component.name == comp_name)
                                                .order_by(ComponentVersion.created_at.desc())
                                                .limit(1)
                                            )
                                            cv = (await sess.execute(cv_stmt)).scalar_one_or_none()
                                            if cv:
                                                try:
                                                    manifest = yaml_lib.safe_load(cv.manifest_yaml)
                                                    ds["component_display_name"] = manifest.get(
                                                        "display_name", comp_name
                                                    )
                                                except Exception:
                                                    ds["component_display_name"] = comp_name
                                            if cv and cv.experimental_object_code:
                                                ds["experimental_object_code"] = (
                                                    cv.experimental_object_code
                                                )
                                                from packages.standards.objects import (
                                                    IndustrialObject,
                                                )

                                                obj_stmt = sa.select(IndustrialObject).where(
                                                    IndustrialObject.code
                                                    == cv.experimental_object_code
                                                )
                                                obj = (
                                                    await sess.execute(obj_stmt)
                                                ).scalar_one_or_none()
                                                if obj:
                                                    ds["object_name"] = obj.display_name
                                                    if obj.equipment_id:
                                                        from packages.equipment.entities import (
                                                            Equipment,
                                                        )

                                                        eq_stmt = sa.select(Equipment).where(
                                                            Equipment.id == obj.equipment_id
                                                        )
                                                        eq = (
                                                            await sess.execute(eq_stmt)
                                                        ).scalar_one_or_none()
                                                        if eq:
                                                            ds["equipment_name"] = eq.display_name
                                                            if eq.department_id:
                                                                dept_stmt = sa.select(
                                                                    Department
                                                                ).where(
                                                                    Department.id
                                                                    == eq.department_id
                                                                )
                                                                dept = (
                                                                    await sess.execute(dept_stmt)
                                                                ).scalar_one_or_none()
                                                                if dept:
                                                                    ds["department_name"] = (
                                                                        dept.display_name
                                                                    )
                                            data_source_list.append(ds)
                                        task_info["data_interface"] = (
                                            ", ".join(comp_names) if comp_names else None
                                        )
                                        task_info["data_source_list"] = data_source_list
        except Exception as e:
            _logger.warning("Failed to query data_source_list: %s", e, exc_info=True)

        # ---- Fallback：快照没命中，通过 flow_run_id 外键反查（兼容旧数据）----
        if not task_info:
            try:
                from packages.components.flow_runtime import (
                    FlowDefinition,
                    FlowDefinitionVersionORM,
                    FlowRun,
                )

                flow_run_id = fact_record.flow_run_id
                if flow_run_id:
                    # 用 fact 自己的 department_id 设 GUC，确保 RLS 可见
                    if fact_record.department_id:
                        await set_dept_guc(session, fact_record.department_id)
                    if fact_record.owner_user_id:
                        await set_user_guc(session, fact_record.owner_user_id)

                    run_stmt = sa.select(FlowRun).where(FlowRun.id == flow_run_id)
                    run_record = (await session.execute(run_stmt)).scalar_one_or_none()
                    if run_record:
                        fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                            FlowDefinitionVersionORM.id == run_record.flow_version_id
                        )
                        fv = (await session.execute(fv_stmt)).scalar_one_or_none()
                        if fv:
                            fd_stmt = sa.select(FlowDefinition).where(
                                FlowDefinition.id == fv.flow_definition_id
                            )
                            fd = (await session.execute(fd_stmt)).scalar_one_or_none()
                            if fd:
                                dept_name = None
                                if fd.department_id:
                                    dept_stmt = sa.select(Department).where(
                                        Department.id == fd.department_id
                                    )
                                    dept_record = (
                                        await session.execute(dept_stmt)
                                    ).scalar_one_or_none()
                                    if dept_record:
                                        dept_name = dept_record.display_name

                                nodes = fv.nodes_json or []
                                comp_names = list(
                                    {
                                        n.get("component_name", "")
                                        for n in nodes
                                        if n.get("component_name")
                                    }
                                )

                                # 查 experiment_project 取项目名
                                project_name = None
                                if fd.project_id:
                                    from packages.experiment_project.entities import (
                                        ExperimentProject,
                                    )

                                    ep_stmt = sa.select(ExperimentProject).where(
                                        ExperimentProject.id == fd.project_id
                                    )
                                    ep = (await session.execute(ep_stmt)).scalar_one_or_none()
                                    if ep:
                                        project_name = ep.display_name

                                task_info = {
                                    "task_name": fd.display_name,
                                    "task_source": dept_name,
                                    "operator": fd.operator,
                                    "run_operator": (run_record.input_snapshot or {}).get(
                                        "_operator"
                                    )
                                    if run_record
                                    else None,
                                    "project_name": project_name,
                                    "owner_name": None,
                                    "job_id": str(run_record.job_id)
                                    if run_record and run_record.job_id
                                    else None,
                                    "department_name": dept_name,
                                    "data_interface": ", ".join(comp_names) if comp_names else None,
                                    "created_at": fd.created_at.isoformat()
                                    if fd.created_at
                                    else None,
                                }
            except (sa.exc.SQLAlchemyError, KeyError, ValueError) as e:
                _logger.warning(
                    "Failed to query task info for fact %s: %s",
                    fact_record.id,
                    e,
                    exc_info=True,
                )

        return task_info
