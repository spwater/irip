"""运行工件服务：工件收集 + 白名单扫描 + MinIO 持久化。

RunArtifactService 负责：
1. 收集沙箱输出的工件（code / log / chart / data / intermediate）；
2. 白名单扫描（不允许路径穿越、不允许非白名单类型）；
3. 上传到 MinIO（路径前缀 research/artifacts/{run_id}/{step_id}/）；
4. 计算 content_hash（SHA-256）；
5. 插入 research_run_artifact 记录；
6. 发布资格标记（依赖闭包全部成功时 is_publishable=true）。

参照 packages/research/snapshots.py 的 ScopedSessionMixin 模式。
"""

import hashlib
import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.research.models_trusted import ArtifactContent, ArtifactRef
from packages.research.repository_trusted import ResearchRepositoryTrusted

logger = logging.getLogger("research.artifacts")

#: 允许的工件类型白名单。
ARTIFACT_TYPE_WHITELIST: set[str] = {
    "code",
    "log",
    "chart",
    "data",
    "intermediate",
}

#: 允许的文件扩展名白名单。
FILE_EXTENSION_WHITELIST: set[str] = {
    ".py",
    ".json",
    ".csv",
    ".txt",
    ".log",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".html",
    ".md",
}

#: 禁止的路径模式（防路径穿越）。
FORBIDDEN_PATH_PATTERNS: list[str] = ["..", "~", "//", "\\"]


class RunArtifactService(ScopedSessionMixin):
    """运行工件业务编排服务。

    依赖注入 session_factory、s3_repo。

    Attributes:
        _factory: 异步会话工厂。
        _s3_repo: S3 / MinIO 存储客户端。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        s3_repo: object,
    ) -> None:
        """初始化工件服务。

        Args:
            session_factory: 异步会话工厂。
            s3_repo: S3 / MinIO 存储客户端。
        """
        self._factory = session_factory
        self._s3_repo = s3_repo
        self._dept_id: UUID | None = None
        self._actor_id: UUID | None = None
        self._rls_dept_id: UUID | None = None

    def set_context(self, department_id: UUID, actor_id: UUID | None) -> None:
        """设置租户上下文（Worker 调用时使用）。

        Args:
            department_id: 部门 ID。
            actor_id: 操作人 ID。
        """
        self._dept_id = department_id
        self._actor_id = actor_id

    async def collect_artifact(
        self,
        run_id: UUID,
        step_id: UUID | None,
        artifact_type: str,
        artifact_key: str,
        content: bytes,
        is_publishable: bool = False,
    ) -> ArtifactRef:
        """收集工件并持久化到 MinIO + 数据库。

        流程：
        1. 白名单扫描（类型 + 文件扩展名 + 路径穿越检测）；
        2. 计算 content_hash（SHA-256）；
        3. 上传到 MinIO（路径 research/artifacts/{run_id}/{step_id}/{artifact_key}）；
        4. 插入 research_run_artifact 记录。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID（可选）。
            artifact_type: 工件类型（code / log / chart / data / intermediate）。
            artifact_key: 工件键名（文件名）。
            content: 文件内容（bytes）。
            is_publishable: 是否可发布。

        Returns:
            ArtifactRef: 工件引用。

        Raises:
            ValueError: 当工件类型不在白名单中、文件扩展名不允许或检测到路径穿越时。
        """
        # 1. 白名单扫描
        self._scan_artifact(artifact_type, artifact_key)

        # 2. 计算 content_hash
        content_hash = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)

        # 3. 构建 MinIO 存储路径
        step_prefix = str(step_id) if step_id is not None else "general"
        storage_path = f"research/artifacts/{run_id}/{step_prefix}/{artifact_key}"

        # 4. 上传到 MinIO
        await self._upload_to_minio(storage_path, content)

        # 5. 插入数据库记录
        async with self._scoped_session() as session:
            artifact = await ResearchRepositoryTrusted.insert_artifact(
                session,
                run_id=run_id,
                step_id=step_id,
                artifact_type=artifact_type,
                artifact_key=artifact_key,
                storage_path=storage_path,
                content_hash=content_hash,
                size_bytes=size_bytes,
                is_publishable=is_publishable,
            )
            return ArtifactRef(
                artifact_id=artifact.id,
                run_id=artifact.run_id,
                step_id=artifact.step_id,
                artifact_type=artifact.artifact_type,
                artifact_key=artifact.artifact_key,
                storage_path=artifact.storage_path,
                content_hash=artifact.content_hash,
                size_bytes=artifact.size_bytes,
                is_publishable=artifact.is_publishable,
                created_at=artifact.created_at,
            )

    async def list_artifacts(
        self,
        run_id: UUID,
        step_id: UUID | None = None,
        artifact_type: str | None = None,
    ) -> list[ArtifactRef]:
        """列出工件。

        Args:
            run_id: Run ID。
            step_id: 步骤 ID（可选过滤）。
            artifact_type: 工件类型过滤（可选）。

        Returns:
            list[ArtifactRef]: 工件引用列表。
        """
        async with self._scoped_session() as session:
            if step_id is not None:
                artifacts = await ResearchRepositoryTrusted.list_artifacts_by_step(
                    session, step_id
                )
            else:
                artifacts = await ResearchRepositoryTrusted.list_artifacts_by_run(
                    session, run_id, artifact_type
                )
            return [
                ArtifactRef(
                    artifact_id=a.id,
                    run_id=a.run_id,
                    step_id=a.step_id,
                    artifact_type=a.artifact_type,
                    artifact_key=a.artifact_key,
                    storage_path=a.storage_path,
                    content_hash=a.content_hash,
                    size_bytes=a.size_bytes,
                    is_publishable=a.is_publishable,
                    created_at=a.created_at,
                )
                for a in artifacts
            ]

    async def get_artifact(self, artifact_id: UUID) -> ArtifactContent | None:
        """获取工件内容（从 MinIO 下载）。

        Args:
            artifact_id: 工件 ID。

        Returns:
            ArtifactContent | None: 工件内容，不存在时返回 None。
        """
        async with self._scoped_session() as session:
            artifact = await ResearchRepositoryTrusted.get_artifact(session, artifact_id)
            if artifact is None:
                return None
            content = await self._download_from_minio(artifact.storage_path)
            return ArtifactContent(
                artifact_id=artifact.id,
                artifact_type=artifact.artifact_type,
                artifact_key=artifact.artifact_key,
                content=content,
                content_hash=artifact.content_hash,
            )

    async def mark_publishable(
        self,
        run_id: UUID,
        step_keys_success: set[str],
    ) -> int:
        """标记依赖闭包全部成功的步骤工件为可发布。

        Args:
            run_id: Run ID。
            step_keys_success: 成功步骤的 step_key 集合。

        Returns:
            int: 标记为可发布的工件数。
        """
        async with self._scoped_session() as session:
            # 获取 Run 的全部步骤，找到成功步骤的 ID
            steps = await ResearchRepositoryTrusted.list_steps_by_run(session, run_id)
            success_step_ids: set[UUID] = set()
            for s in steps:
                if s.step_key in step_keys_success and s.status == "succeeded":
                    success_step_ids.add(s.id)

            # 获取全部工件
            artifacts = await ResearchRepositoryTrusted.list_artifacts_by_run(session, run_id)
            count = 0
            for a in artifacts:
                is_pub = a.step_id in success_step_ids if a.step_id is not None else False
                if is_pub != a.is_publishable:
                    await ResearchRepositoryTrusted.update_artifact_publishable(
                        session, a.id, is_pub
                    )
                    count += 1
            return count

    async def mark_all_unpublishable(self, run_id: UUID) -> int:
        """标记 Run 的全部工件为不可发布（取消 Run 时调用）。

        Args:
            run_id: Run ID。

        Returns:
            int: 标记为不可发布的工件数。
        """
        async with self._scoped_session() as session:
            artifacts = await ResearchRepositoryTrusted.list_artifacts_by_run(session, run_id)
            count = 0
            for a in artifacts:
                if a.is_publishable:
                    await ResearchRepositoryTrusted.update_artifact_publishable(
                        session, a.id, False
                    )
                    count += 1
            return count

    def _scan_artifact(self, artifact_type: str, artifact_key: str) -> None:
        """白名单扫描工件。

        检查：
        1. 工件类型在白名单中；
        2. 文件扩展名在白名单中；
        3. 无路径穿越。

        Args:
            artifact_type: 工件类型。
            artifact_key: 工件键名。

        Raises:
            ValueError: 当扫描不通过时。
        """
        if artifact_type not in ARTIFACT_TYPE_WHITELIST:
            raise ValueError(
                f"工件类型 '{artifact_type}' 不在白名单中: {ARTIFACT_TYPE_WHITELIST}"
            )

        # 检查路径穿越
        for pattern in FORBIDDEN_PATH_PATTERNS:
            if pattern in artifact_key:
                raise ValueError(
                    f"工件键名包含禁止的路径模式 '{pattern}': {artifact_key}"
                )

        # 检查文件扩展名
        ext = ""
        if "." in artifact_key:
            ext = "." + artifact_key.rsplit(".", 1)[-1].lower()
        if ext and ext not in FILE_EXTENSION_WHITELIST:
            raise ValueError(
                f"文件扩展名 '{ext}' 不在白名单中: {FILE_EXTENSION_WHITELIST}"
            )

    async def _upload_to_minio(self, storage_path: str, content: bytes) -> None:
        """上传内容到 MinIO。

        Args:
            storage_path: MinIO 存储路径。
            content: 文件内容。
        """
        # S3Repository.put_object 是同步方法，需要 (key, data, content_type)
        sync_put = getattr(self._s3_repo, "put_object", None)
        if sync_put is not None:
            # 根据路径推断 content_type
            if storage_path.endswith(".json"):
                ct = "application/json"
            elif storage_path.endswith(".png"):
                ct = "image/png"
            elif storage_path.endswith(".pdf"):
                ct = "application/pdf"
            elif storage_path.endswith(".py"):
                ct = "text/x-python"
            elif storage_path.endswith(".txt") or storage_path.endswith(".log"):
                ct = "text/plain"
            else:
                ct = "application/octet-stream"
            sync_put(storage_path, content, ct)
        else:
            logger.warning("S3Repository has no put_object method, skipping upload")

    async def _download_from_minio(self, storage_path: str) -> bytes:
        """从 MinIO 下载内容。

        Args:
            storage_path: MinIO 存储路径。

        Returns:
            bytes: 文件内容。
        """
        get_method = getattr(self._s3_repo, "get_object", None)
        if get_method is not None:
            result = await get_method(storage_path)
            return result if isinstance(result, bytes) else result
        sync_get = getattr(self._s3_repo, "get_object_sync", None)
        if sync_get is not None:
            return sync_get(storage_path)
        logger.warning("S3Repository has no get_object method, returning empty")
        return b""
