"""研究域业务编排服务：WorkspaceService。

WorkspaceService 提供研究工作空间的创建、列表、详情、归档、删除、分叉、
研究问题版本管理、证据引用管理等功能。

核心不变量：
1. 工作空间只属于创建者本人（owner_user_id），无成员列表；
2. 创建工作空间时同步创建 ResearchQuestionVersion v1；
3. 更新研究问题生成新版本（version_number 递增），旧版本不可变；
4. 证据引用软删除（status → removed），不物理删除；
5. 分叉仅继承主研究问题最新版本 + 证据引用列表（副本）；
6. 所有写操作产生审计记录。

参照 packages/facts/service.py 的 ScopedSessionMixin 模式。
"""

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.models import (
    CreateWorkspaceCommand,
    EvidenceRefDTO,
    FactSummary,
    QuestionVersionRef,
    WorkspaceDetail,
    WorkspaceRef,
)
from packages.research.repository import ResearchRepository

if TYPE_CHECKING:
    pass


class CoreFactProviderProtocol(Protocol):
    """CoreFactProvider 协议类型提示（T03 实现具体类）。

    研究域通过此接口只读访问 Fact 数据，不暴露核心数据库会话。
    """

    async def search_facts(
        self,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactSummary], str | None]:
        """搜索当前用户有权访问的 Fact。"""
        ...

    async def get_fact_summary(self, fact_id: UUID) -> FactSummary:
        """获取 Fact 摘要（不含完整数据内容）。"""
        ...

    async def get_fact_fields(self, fact_id: UUID) -> list[str]:
        """获取 Fact 的字段清单。"""
        ...


class WorkspaceService(ScopedSessionMixin):
    """研究工作空间业务编排服务。

    依赖注入 session_factory（事务管理）、department_id（当前部门）、
    actor_id（操作人）、fact_provider（只读 Fact 适配器）。

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
        research_catalog: object | None = None,
    ) -> None:
        """初始化工作空间服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            fact_provider: CoreFactProvider 只读适配器。
            research_catalog: ResearchCatalog 实例（阶段 3 新增，用于 research:derived 校验）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._fact_provider = fact_provider
        self._research_catalog = research_catalog
        self._rls_dept_id: UUID | None = None

    @property
    def department_id(self) -> UUID:
        """当前部门 ID。"""
        return self._dept_id

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """异步会话工厂。"""
        return self._factory

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

    async def create_workspace(
        self,
        command: CreateWorkspaceCommand,
    ) -> WorkspaceRef:
        """创建工作空间 + 研究问题 v1。

        流程：
        1. 插入 research_workspace（status=draft, current_question_version=0）；
        2. 插入 research_question_version v1（question_text, sub_questions=[]）；
        3. 更新 workspace.current_question_version = 1；
        4. 审计。

        Args:
            command: 创建命令（name + question_text）。

        Returns:
            WorkspaceRef: 工作空间引用。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 插入工作空间
            workspace = await ResearchRepository.insert_workspace(
                session,
                owner_user_id=actor_id,
                department_id=self._dept_id,
                name=command.name,
                status="draft",
            )

            # 2. 插入问题版本 v1
            question = await ResearchRepository.insert_question_version(
                session,
                workspace_id=workspace.id,
                version_number=1,
                question_text=command.question_text,
                sub_questions=[],
                created_by=actor_id,
            )

            # 3. 更新工作空间当前版本号
            await ResearchRepository.update_workspace_current_version(
                session, workspace.id, 1
            )

            # 4. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.workspace.create",
                    actor_user_id=actor_id,
                    resource_type="research_workspace",
                    resource_id=workspace.id,
                    payload={"name": command.name},
                ),
            )

            return WorkspaceRef(
                workspace_id=workspace.id,
                name=workspace.name,
                status=workspace.status,
                current_question_version=1,
            )

    async def list_workspaces(
        self,
        status: str | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[WorkspaceRef], str | None]:
        """分页列出当前用户的工作空间。

        Args:
            status: 状态过滤（可选）。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[WorkspaceRef], str | None]:
            (工作空间引用列表, 下一页游标)。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            items, next_cursor = await ResearchRepository.list_workspaces(
                session,
                owner_user_id=actor_id,
                status=status,
                cursor=cursor,
                page_size=page_size,
            )
            refs = [
                WorkspaceRef(
                    workspace_id=ws.id,
                    name=ws.name,
                    status=ws.status,
                    current_question_version=ws.current_question_version,
                    forked_from_id=ws.forked_from_id,
                )
                for ws in items
            ]
            return refs, next_cursor

    async def update_workspace_name(
        self,
        workspace_id: UUID,
        name: str,
    ) -> WorkspaceRef:
        """更新工作空间名称。

        Args:
            workspace_id: 工作空间 ID。
            name: 新名称。

        Returns:
            WorkspaceRef: 更新后的工作空间引用。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            await ResearchRepository.update_workspace_name(
                session, workspace_id, name
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.workspace.update",
                    actor_user_id=actor_id,
                    resource_type="research_workspace",
                    resource_id=workspace_id,
                    payload={"name": name},
                ),
            )

            return WorkspaceRef(
                workspace_id=workspace.id,
                name=name,
                status=workspace.status,
                current_question_version=workspace.current_question_version,
                forked_from_id=workspace.forked_from_id,
            )

    async def get_workspace(self, workspace_id: UUID) -> WorkspaceDetail:
        """获取工作空间详情（含当前问题版本 + 证据数 + 快照数）。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            WorkspaceDetail: 工作空间详情。

        Raises:
            AppError: code="not_found"，当工作空间不存在或不属于当前用户时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 获取当前问题版本
            current_question_orm = await ResearchRepository.get_latest_question_version(
                session, workspace_id
            )
            current_question: QuestionVersionRef | None = None
            if current_question_orm is not None:
                current_question = QuestionVersionRef(
                    version_id=current_question_orm.id,
                    workspace_id=current_question_orm.workspace_id,
                    version_number=current_question_orm.version_number,
                    question_text=current_question_orm.question_text,
                    sub_questions=list(current_question_orm.sub_questions or []),
                )

            # 获取证据数
            evidence_count = await ResearchRepository.count_active_evidence_refs(
                session, workspace_id
            )

            # 获取快照列表
            snapshots_orm = await ResearchRepository.list_snapshots(session, workspace_id)
            from packages.research.models import SnapshotRef

            snapshots = [
                SnapshotRef(
                    snapshot_id=s.id,
                    snapshot_number=s.snapshot_number,
                    content_hash=s.content_hash,
                    captured_at=s.captured_at,
                )
                for s in snapshots_orm
            ]

            return WorkspaceDetail(
                workspace_id=workspace.id,
                name=workspace.name,
                status=workspace.status,
                current_question=current_question,
                evidence_count=evidence_count,
                snapshots=snapshots,
            )

    async def archive_workspace(self, workspace_id: UUID) -> None:
        """归档工作空间（status → archived）。

        Args:
            workspace_id: 工作空间 ID。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            await ResearchRepository.update_workspace_status(
                session, workspace_id, "archived"
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.workspace.archive",
                    actor_user_id=actor_id,
                    resource_type="research_workspace",
                    resource_id=workspace_id,
                ),
            )

    async def delete_workspace(self, workspace_id: UUID) -> None:
        """物理删除工作空间（CASCADE 级联删除子表）。

        阶段 4 升级：有已发布成果包的 Workspace 只能归档不能删除。

        Args:
            workspace_id: 工作空间 ID。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
            AppError: code="conflict"，当存在已发布成果包时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 阶段 4：检查是否存在已发布成果包
            published_count = await ResearchRepository.count_published_results_by_workspace(
                session, workspace_id
            )
            if published_count > 0:
                raise AppError(
                    code="conflict",
                    message=(
                        f"工作空间存在 {published_count} 个已发布成果包，"
                        f"无法删除，请改为归档"
                    ),
                    retryable=False,
                    fields={
                        "workspace_id": str(workspace_id),
                        "published_count": published_count,
                    },
                )

            await ResearchRepository.delete_workspace(session, workspace_id)

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.workspace.delete",
                    actor_user_id=actor_id,
                    resource_type="research_workspace",
                    resource_id=workspace_id,
                ),
            )

    async def fork_workspace(
        self,
        workspace_id: UUID,
        new_name: str,
    ) -> WorkspaceRef:
        """分叉工作空间。

        Q5 继承规则：仅继承主研究问题最新版本 + 证据引用列表（副本）。

        Args:
            workspace_id: 源工作空间 ID。
            new_name: 新工作空间名称。

        Returns:
            WorkspaceRef: 新工作空间引用。

        Raises:
            AppError: code="not_found"，当源工作空间不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 读取源工作空间
            source = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if source is None:
                raise AppError(
                    code="not_found",
                    message="源研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 2. 读取源最新问题版本
            latest_question = await ResearchRepository.get_latest_question_version(
                session, workspace_id
            )

            # 3. 读取源证据引用列表（active）
            evidence_refs = await ResearchRepository.list_evidence_refs(
                session, workspace_id, status="active"
            )

            # 4. 创建新工作空间
            new_ws = await ResearchRepository.insert_workspace(
                session,
                owner_user_id=actor_id,
                department_id=self._dept_id,
                name=new_name,
                status="draft",
                forked_from_id=source.id,
            )

            # 5. 创建问题版本 v1（继承源最新问题文本）
            if latest_question is not None:
                await ResearchRepository.insert_question_version(
                    session,
                    workspace_id=new_ws.id,
                    version_number=1,
                    question_text=latest_question.question_text,
                    sub_questions=list(latest_question.sub_questions or []),
                    created_by=actor_id,
                )
            else:
                await ResearchRepository.insert_question_version(
                    session,
                    workspace_id=new_ws.id,
                    version_number=1,
                    question_text="",
                    sub_questions=[],
                    created_by=actor_id,
                )

            # 更新工作空间版本号
            await ResearchRepository.update_workspace_current_version(
                session, new_ws.id, 1
            )

            # 6. 复制证据引用（副本而非共享引用）
            for ref in evidence_refs:
                await ResearchRepository.insert_evidence_ref(
                    session,
                    workspace_id=new_ws.id,
                    source_namespace=ref.source_namespace,
                    source_id=ref.source_id,
                    source_version=ref.source_version,
                    source_name=ref.source_name,
                    added_by=actor_id,
                )

            # 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.workspace.fork",
                    actor_user_id=actor_id,
                    resource_type="research_workspace",
                    resource_id=new_ws.id,
                    payload={"forked_from": str(source.id)},
                ),
            )

            return WorkspaceRef(
                workspace_id=new_ws.id,
                name=new_ws.name,
                status=new_ws.status,
                current_question_version=1,
                forked_from_id=source.id,
            )

    async def update_question(
        self,
        workspace_id: UUID,
        question_text: str,
        sub_questions: list[str] | None = None,
    ) -> QuestionVersionRef:
        """更新研究问题（创建新版本）。

        Args:
            workspace_id: 工作空间 ID。
            question_text: 新主研究问题文本。
            sub_questions: 子问题列表（可选）。

        Returns:
            QuestionVersionRef: 新版本引用。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            new_version_number = workspace.current_question_version + 1

            version = await ResearchRepository.insert_question_version(
                session,
                workspace_id=workspace_id,
                version_number=new_version_number,
                question_text=question_text,
                sub_questions=sub_questions if sub_questions is not None else [],
                created_by=actor_id,
            )

            await ResearchRepository.update_workspace_current_version(
                session, workspace_id, new_version_number
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.question.update",
                    actor_user_id=actor_id,
                    resource_type="research_question_version",
                    resource_id=version.id,
                    payload={"version_number": new_version_number},
                ),
            )

            return QuestionVersionRef(
                version_id=version.id,
                workspace_id=workspace_id,
                version_number=new_version_number,
                question_text=question_text,
                sub_questions=sub_questions if sub_questions is not None else [],
            )

    async def add_evidence(
        self,
        workspace_id: UUID,
        source_namespace: str,
        source_id: UUID,
    ) -> EvidenceRefDTO:
        """加入证据引用。

        流程：
        1. 校验工作空间归属；
        2. 通过 CoreFactProvider 校验数据级权限（P1-5）；
        3. 获取 Fact 摘要（source_name / source_version）；
        4. 插入证据引用；
        5. 审计。

        Args:
            workspace_id: 工作空间 ID。
            source_namespace: 源命名空间（如 "core:fact"）。
            source_id: 源对象 ID（Fact UUID）。

        Returns:
            EvidenceRefDTO: 证据引用 DTO。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
            AppError: code="forbidden"，当无权访问 Fact 时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 校验工作空间归属
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            # 2. 通过 CoreFactProvider 校验数据级权限 + 获取摘要
            if source_namespace == "core:fact":
                fact_summary = await self._fact_provider.get_fact_summary(source_id)
                source_name = fact_summary.subject_id or fact_summary.fact_type
                source_version = None  # Fact 摘要不含版本号，留空

            elif source_namespace == "research:derived":
                # 阶段 3：通过 ResearchCatalog 校验 DerivedDataset 归属和版本
                if self._research_catalog is None:
                    raise AppError(
                        code="forbidden",
                        message="ResearchCatalog 未配置，无法加入衍生数据证据",
                        retryable=False,
                        fields={},
                    )
                # 搜索校验归属（通过 owner_user_id 过滤）
                results = await self._research_catalog.search_derived_data(
                    query="",
                    filters={"dataset_id": str(source_id)},
                )
                if not results:
                    raise AppError(
                        code="forbidden",
                        message="衍生数据集不存在或不属于当前用户",
                        retryable=False,
                        fields={"source_id": str(source_id)},
                    )
                dataset_info = results[0]
                source_name = dataset_info.get("name", "衍生数据")
                source_version = str(dataset_info.get("current_version", "1"))

            elif source_namespace == "research:published_derived":
                # 阶段 4：从已发布成果包中添加 DerivedDataset 作为证据
                if self._research_catalog is None:
                    raise AppError(
                        code="forbidden",
                        message="ResearchCatalog 未配置，无法加入已发布衍生数据证据",
                        retryable=False,
                        fields={},
                    )
                # 通过跨用户 ACL 过滤搜索校验
                results = await self._research_catalog.search_published_derived_data(
                    query="",
                    filters={"dataset_id_filter": str(source_id)},
                )
                # 手动过滤 dataset_id
                matching = [
                    r for r in results
                    if r.get("dataset_id") == str(source_id)
                ]
                if not matching:
                    raise AppError(
                        code="forbidden",
                        message="已发布数据集不存在或当前用户无权访问",
                        retryable=False,
                        fields={"source_id": str(source_id)},
                    )
                dataset_info = matching[0]
                source_name = dataset_info.get("dataset_name", "已发布衍生数据")
                source_version = str(
                    dataset_info.get("dataset_version_number", "1")
                )

            else:
                raise AppError(
                    code="validation_failed",
                    message=f"不支持的证据命名空间: {source_namespace}",
                    retryable=False,
                    fields={"source_namespace": source_namespace},
                )

            # 3. 插入证据引用
            ref = await ResearchRepository.insert_evidence_ref(
                session,
                workspace_id=workspace_id,
                source_namespace=source_namespace,
                source_id=source_id,
                source_version=source_version,
                source_name=source_name,
                added_by=actor_id,
            )

            # 4. 审计
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.evidence.add",
                    actor_user_id=actor_id,
                    resource_type="research_workspace_evidence_ref",
                    resource_id=ref.id,
                    payload={
                        "workspace_id": str(workspace_id),
                        "source_namespace": source_namespace,
                    },
                ),
            )

            return EvidenceRefDTO(
                ref_id=ref.id,
                source_namespace=ref.source_namespace,
                source_id=ref.source_id,
                source_version=ref.source_version,
                source_name=ref.source_name,
                status=ref.status,
            )

    async def remove_evidence(
        self,
        workspace_id: UUID,
        ref_id: UUID,
    ) -> None:
        """移除证据引用（软删除 status → removed）。

        Args:
            workspace_id: 工作空间 ID。
            ref_id: 证据引用 ID。

        Raises:
            AppError: code="not_found"，当工作空间或证据引用不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            ref = await ResearchRepository.get_evidence_ref(session, ref_id, workspace_id)
            if ref is None:
                raise AppError(
                    code="not_found",
                    message="证据引用不存在",
                    retryable=False,
                    fields={"ref_id": str(ref_id)},
                )

            await ResearchRepository.update_evidence_ref_status(
                session, ref_id, "removed"
            )

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.evidence.remove",
                    actor_user_id=actor_id,
                    resource_type="research_workspace_evidence_ref",
                    resource_id=ref_id,
                ),
            )

    async def list_evidence(
        self,
        workspace_id: UUID,
    ) -> list[EvidenceRefDTO]:
        """列出工作空间的活跃证据引用。

        Args:
            workspace_id: 工作空间 ID。

        Returns:
            list[EvidenceRefDTO]: 证据引用列表。

        Raises:
            AppError: code="not_found"，当工作空间不存在时。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            workspace = await ResearchRepository.get_workspace(
                session, workspace_id, actor_id
            )
            if workspace is None:
                raise AppError(
                    code="not_found",
                    message="研究工作空间不存在",
                    retryable=False,
                    fields={"workspace_id": str(workspace_id)},
                )

            refs = await ResearchRepository.list_evidence_refs(
                session, workspace_id, status="active"
            )
            return [
                EvidenceRefDTO(
                    ref_id=ref.id,
                    source_namespace=ref.source_namespace,
                    source_id=ref.source_id,
                    source_version=ref.source_version,
                    source_name=ref.source_name,
                    status=ref.status,
                )
                for ref in refs
            ]

    async def search_facts(
        self,
        query: str,
        filters: dict | None = None,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[FactSummary], str | None]:
        """搜索 Fact（委托 CoreFactProvider）。

        Args:
            query: 搜索查询字符串。
            filters: 过滤条件。
            cursor: 分页游标。
            page_size: 每页数量。

        Returns:
            tuple[list[FactSummary], str | None]:
            (Fact 摘要列表, 下一页游标)。
        """
        return await self._fact_provider.search_facts(
            query=query,
            filters=filters,
            cursor=cursor,
            page_size=page_size,
        )
