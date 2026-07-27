"""内容寻址工件服务。

提供内容寻址（content-addressed）的对象存储抽象：
- ArtifactRef: 工件引用（frozen dataclass）；
- ArtifactBlob / Artifact: ORM 模型（blob 去重 + 业务链接）；
- ArtifactService: 上传、去重、校验、预签名。

设计要点（docs/arch-v0.md §3.1 第 294-298 行）：
- 相同内容多业务引用共享同一 ``artifact_blob``（按 SHA-256 去重）；
- object_key 格式：``sha256/<前2位>/<digest>``；
- media_type allowlist 限制可接受的文件类型。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.hashing import sha256_bytes
from packages.common.ids import new_id
from packages.common.s3_repository import S3Repository

#: 允许的媒体类型白名单。
ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset({
    "text/plain",
    "text/csv",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "application/json",
})

#: 最大上传大小（字节），100 MiB。
MAX_UPLOAD_SIZE_BYTES: int = 100 * 1024 * 1024


def _build_object_key(sha256: str) -> str:
    """构建 S3 object key：``sha256/<前2位>/<digest>``。

    Args:
        sha256: SHA-256 十六进制摘要（64 位）。

    Returns:
        str: S3 object key。
    """
    return f"sha256/{sha256[:2]}/{sha256}"


@dataclass(frozen=True)
class ArtifactRef:
    """工件引用（不可变值对象）。

    作为 ArtifactService.put_bytes() 等方法的返回值，
    包含调用方所需的全部元数据。

    Attributes:
        artifact_id: 工件 UUID（业务链接 ID）。
        object_key: S3 object key。
        sha256: 内容 SHA-256 摘要（hex 小写）。
        media_type: MIME 类型。
        size_bytes: 内容字节数。
    """

    artifact_id: UUID
    object_key: str
    sha256: str
    media_type: str
    size_bytes: int


class ArtifactBlob(Base):
    """内容寻址 blob ORM 模型（对应 artifact_blob 表）。

    相同内容共享同一行（按 SHA-256 去重）。

    Attributes:
        sha256: SHA-256 摘要（PK）。
        object_key: S3 object key（UNIQUE）。
        size_bytes: 内容字节数。
        media_type: MIME 类型。
        created_at: 创建时间（UTC）。
    """

    __tablename__ = "artifact_blob"

    sha256: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    object_key: Mapped[str] = mapped_column(
        sa.Text, unique=True, nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"ArtifactBlob(sha256={self.sha256[:12]}..., "
            f"size_bytes={self.size_bytes})"
        )


class Artifact(Base):
    """工件业务链接 ORM 模型（对应 artifact 表）。

    每次上传创建一行，指向共享的 artifact_blob。

    Attributes:
        id: 工件 UUID（PK）。
        organization_id: 所属组织 ID。
        sha256: 关联 blob 的 SHA-256（FK→artifact_blob.sha256）。
        filename: 原始文件名。
        media_type: MIME 类型。
        size_bytes: 内容字节数。
        uploaded_by: 上传者用户 ID（FK→app_user.id）。
        created_at: 创建时间（UTC）。
    """

    __tablename__ = "artifact"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    sha256: Mapped[str] = mapped_column(
        sa.Text,
        sa.ForeignKey("artifact_blob.sha256", name="fk_artifact_sha256"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(sa.Text, nullable=False)
    media_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("app_user.id", name="fk_artifact_uploaded_by"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"Artifact(id={self.id!r}, filename={self.filename!r}, "
            f"sha256={self.sha256[:12]}...)"
        )


class ArtifactService:
    """内容寻址工件服务。

    依赖注入 S3Repository（对象存储）、session_factory（数据库事务）、
    organization_id（当前组织）、uploaded_by（当前用户）。

    核心流程：
    - put_bytes: 计算 SHA-256 → 查 blob 去重 → 上传 S3（如需）→ INSERT blob + artifact
    - verify: 下载 S3 对象 → 重算 SHA-256 → 与存储值比对
    - presign_upload / presign_download: 生成预签名 URL
    """

    def __init__(
        self,
        s3_repo: S3Repository,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        uploaded_by: UUID,
    ) -> None:
        """初始化工件服务。

        Args:
            s3_repo: S3 对象存储客户端封装。
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            uploaded_by: 当前上传者用户 ID。
        """
        self._s3 = s3_repo
        self._factory = session_factory
        self._org_id = organization_id
        self._uploaded_by = uploaded_by

    async def put_bytes(
        self,
        data: bytes,
        media_type: str,
        filename: str,
    ) -> ArtifactRef:
        """上传字节内容，返回工件引用。

        流程：
        1. 校验 media_type 白名单；
        2. 计算 SHA-256 + object_key；
        3. 查 artifact_blob 是否已存在（去重）；
        4. 若不存在：上传 S3 + INSERT artifact_blob；
        5. INSERT artifact（业务链接）；
        6. 返回 ArtifactRef。

        Args:
            data: 字节内容。
            media_type: MIME 类型（必须在白名单中）。
            filename: 原始文件名。

        Returns:
            ArtifactRef: 工件引用。

        Raises:
            AppError: code="unsupported_media_type"，当 media_type 不在白名单中。
        """
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise AppError(
                code="unsupported_media_type",
                message=f"不支持的媒体类型: {media_type}",
                retryable=False,
                fields={"media_type": media_type},
            )

        sha256: str = sha256_bytes(data)
        object_key: str = _build_object_key(sha256)
        size: int = len(data)

        async with session_scope(self._factory) as session:
            # 查 blob 是否已存在（去重）
            existing_blob: ArtifactBlob | None = await session.scalar(
                sa.select(ArtifactBlob).where(ArtifactBlob.sha256 == sha256)
            )
            if existing_blob is None:
                # 上传到 S3（同步操作，包装为异步）
                await asyncio.to_thread(
                    self._s3.put_object, object_key, data, media_type
                )
                blob = ArtifactBlob(
                    sha256=sha256,
                    object_key=object_key,
                    size_bytes=size,
                    media_type=media_type,
                )
                session.add(blob)
                await session.flush()

            artifact = Artifact(
                organization_id=self._org_id,
                sha256=sha256,
                filename=filename,
                media_type=media_type,
                size_bytes=size,
                uploaded_by=self._uploaded_by,
            )
            session.add(artifact)
            await session.flush()

            return ArtifactRef(
                artifact_id=artifact.id,
                object_key=object_key,
                sha256=sha256,
                media_type=media_type,
                size_bytes=size,
            )

    async def verify(self, artifact_id: UUID) -> bool:
        """校验 S3 对象的 SHA-256 与存储值是否一致。

        下载对象内容并重算 SHA-256，与 artifact_blob.sha256 比对。

        安全约定（F-09）：查询加 organization_id 条件，防止跨租户 IDOR。

        Args:
            artifact_id: 工件 UUID。

        Returns:
            bool: 一致返回 True，否则 False。

        Raises:
            AppError: code="not_found"，当工件不存在时。
        """
        async with session_scope(self._factory) as session:
            artifact: Artifact | None = await session.scalar(
                sa.select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.organization_id == self._org_id,
                )
            )
            if artifact is None:
                raise AppError(
                    code="not_found",
                    message=f"工件不存在: {artifact_id}",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )

            blob: ArtifactBlob | None = await session.scalar(
                sa.select(ArtifactBlob).where(
                    ArtifactBlob.sha256 == artifact.sha256
                )
            )
            if blob is None:
                return False

            data: bytes = await asyncio.to_thread(
                self._s3.get_object, blob.object_key
            )
            return sha256_bytes(data) == artifact.sha256

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRef:
        """获取工件引用（只读）。

        安全约定（F-09）：查询加 organization_id 条件，防止跨租户 IDOR。

        Args:
            artifact_id: 工件 UUID。

        Returns:
            ArtifactRef: 工件引用。

        Raises:
            AppError: code="not_found"，当工件不存在时。
        """
        async with session_scope(self._factory) as session:
            row = (
                await session.execute(
                    sa.select(Artifact, ArtifactBlob).where(
                        Artifact.id == artifact_id,
                        Artifact.organization_id == self._org_id,
                        ArtifactBlob.sha256 == Artifact.sha256,
                    )
                )
            ).first()
            if row is None:
                raise AppError(
                    code="not_found",
                    message=f"工件不存在: {artifact_id}",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            artifact: Artifact = row[0]
            blob: ArtifactBlob = row[1]
            return ArtifactRef(
                artifact_id=artifact.id,
                object_key=blob.object_key,
                sha256=artifact.sha256,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
            )

    async def get_bytes(self, artifact_id: UUID) -> bytes:
        """下载工件内容字节。

        通过 artifact_id 查找关联的 blob，从 S3 下载内容。
        供模型服务下载模型工件等场景使用。

        安全约定（F-09）：查询加 organization_id 条件，防止跨租户 IDOR。

        Args:
            artifact_id: 工件 UUID。

        Returns:
            bytes: 工件内容字节。

        Raises:
            AppError: code="not_found"，当工件不存在时。
        """
        async with self._factory() as session:
            row = (
                await session.execute(
                    sa.select(Artifact, ArtifactBlob).where(
                        Artifact.id == artifact_id,
                        Artifact.organization_id == self._org_id,
                        ArtifactBlob.sha256 == Artifact.sha256,
                    )
                )
            ).first()
            if row is None:
                raise AppError(
                    code="not_found",
                    message=f"工件不存在: {artifact_id}",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            artifact: Artifact = row[0]
            blob: ArtifactBlob = row[1]
        data: bytes = await asyncio.to_thread(
            self._s3.get_object, blob.object_key
        )
        return data

    def presign_upload(self, sha256: str, expires: int = 3600) -> str:
        """生成预签名上传 URL（基于内容寻址 key）。

        Args:
            sha256: 预期内容的 SHA-256 摘要。
            expires: URL 有效期（秒）。

        Returns:
            str: 预签名 PUT URL。
        """
        object_key = _build_object_key(sha256)
        return self._s3.presigned_put(object_key, expires)

    def presign_upload_for_key(self, object_key: str, expires: int = 3600, endpoint_override: str | None = None) -> str:
        """生成预签名上传 URL（基于任意 key）。

        用于预签名上传流程：客户端先上传到临时 key，
        完成后服务端校验并移动到内容寻址 key。

        Args:
            object_key: S3 object key。
            expires: URL 有效期（秒）。
            endpoint_override: 可选，用指定端点生成签名 URL。

        Returns:
            str: 预签名 PUT URL。
        """
        return self._s3.presigned_put(object_key, expires, endpoint_override)

    async def complete_upload(
        self,
        temp_key: str,
        media_type: str,
        filename: str,
        expected_sha256: str,
        expected_size: int,
    ) -> ArtifactRef:
        """完成预签名上传：下载临时对象、校验、创建正式工件记录。

        流程：
        1. 从 S3 下载临时对象；
        2. 校验 SHA-256 和 size；
        3. 通过 put_bytes 创建正式工件记录（含去重）。

        Args:
            temp_key: 临时 S3 object key。
            media_type: MIME 类型（必须在白名单中）。
            filename: 原始文件名。
            expected_sha256: 客户端计算的 SHA-256。
            expected_size: 客户端计算的大小。

        Returns:
            ArtifactRef: 工件引用。

        Raises:
            AppError: code="unsupported_media_type"。
            AppError: code="hash_mismatch"。
            AppError: code="size_mismatch"。
        """
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise AppError(
                code="unsupported_media_type",
                message=f"不支持的媒体类型: {media_type}",
                retryable=False,
                fields={"media_type": media_type},
            )

        data: bytes = await asyncio.to_thread(
            self._s3.get_object, temp_key
        )

        actual_sha256: str = sha256_bytes(data)
        if actual_sha256 != expected_sha256:
            raise AppError(
                code="hash_mismatch",
                message="SHA-256 校验失败",
                retryable=False,
                fields={"expected": expected_sha256, "actual": actual_sha256},
            )

        if len(data) != expected_size:
            raise AppError(
                code="size_mismatch",
                message="文件大小校验失败",
                retryable=False,
                fields={
                    "expected": expected_size,
                    "actual": len(data),
                },
            )

        return await self.put_bytes(data, media_type, filename)

    async def presign_download(
        self, artifact_id: UUID, expires: int = 3600, endpoint_override: str | None = None,
    ) -> str:
        """异步生成预签名下载 URL。

        安全约定（F-09）：查询加 organization_id 条件，防止跨租户 IDOR。

        Args:
            artifact_id: 工件 UUID。
            expires: URL 有效期（秒）。
            endpoint_override: 可选，用指定端点生成签名 URL。

        Returns:
            str: 预签名 GET URL。

        Raises:
            AppError: code="not_found"，当工件不存在时。
        """
        async with session_scope(self._factory) as session:
            row = (
                await session.execute(
                    sa.select(ArtifactBlob.object_key)
                    .join(
                        Artifact,
                        Artifact.sha256 == ArtifactBlob.sha256,
                    )
                    .where(
                        Artifact.id == artifact_id,
                        Artifact.organization_id == self._org_id,
                    )
                )
            ).first()
            if row is None:
                raise AppError(
                    code="not_found",
                    message=f"工件不存在: {artifact_id}",
                    retryable=False,
                    fields={"artifact_id": str(artifact_id)},
                )
            object_key: str = row[0]
            url: str = await asyncio.to_thread(
                self._s3.presigned_get, object_key, expires, endpoint_override,
            )
            return url
