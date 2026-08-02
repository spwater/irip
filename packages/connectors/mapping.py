"""密钥存储与数据源预览服务（IRIP Task 13，标准层清理后精简版）。

原模块包含 4 个组件：
1. SecretStore: 密钥存储（保留）；
2. MappingService: 映射评分（已删除，依赖已删除的 variable 表）；
3. MappingProfileService: 映射配置生命周期（已删除，依赖已删除的
   mapping_profile 表）；
4. IngestionService: 数据源预览（保留）。

映射相关辅助函数（_rule_to_dict / _rule_from_dict / _rules_to_json /
_rules_from_json / _validate_profile_document / _load_schema /
_encode_list_cursor / _decode_list_cursor）仅被已删除的服务使用，一并清理。

安全约定：
- 密钥凭据绝不返回、绝不记录日志。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
)
from packages.connectors.entities import Secret


class SecretStore:
    """密钥存储：按 secret_id 解析凭据（组织隔离）。

    F-12: 使用 envelope encryption 加密存储密钥值。
    写入时加密，读取时解密，绝不返回凭据给 API 层，仅由连接器内部使用。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID（隔离过滤）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
    ) -> None:
        """初始化密钥存储。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
        """
        self._factory = session_factory
        self._dept_id = department_id

    async def get(self, secret_id: UUID) -> str:
        """按 ID 解析密钥值（读取时解密）。

        F-12: 使用 envelope encryption 解密存储的密钥。

        Args:
            secret_id: 密钥 UUID。

        Returns:
            str: 凭据明文。

        Raises:
            AppError: code="secret_not_found"，当密钥不存在或不属于当前部门时。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(Secret).where(
                    Secret.id == secret_id,
                    Secret.department_id == self._dept_id,
                )
            )
            secret = result.scalar_one_or_none()
        if secret is None:
            raise AppError(
                code="secret_not_found",
                message="密钥不存在或无权访问",
                retryable=False,
                fields={"secret_id": str(secret_id)},
            )
        # F-12: 解密密钥值
        from packages.common.crypto import EnvelopeCrypto

        crypto = EnvelopeCrypto.from_env()
        try:
            return crypto.decrypt(secret.value)
        except ValueError:
            # 兼容旧版明文存储（迁移期间）
            return secret.value


class IngestionService:
    """数据源预览服务：按 kind 构造连接器并预览。

    C-01: 文件预览改为从 artifact stream 读取，需要 artifact_service。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _artifact_service: 可选的工件服务，用于 file kind 的 artifact 流读取。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        artifact_service: object | None = None,
    ) -> None:
        """初始化预览服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            artifact_service: 工件服务实例（C-01: file kind 需要）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._artifact_service = artifact_service

    async def preview(self, source: ConnectorSource, limit: int = 100) -> PreviewTable:
        """预览数据源。

        Args:
            source: 数据源描述。
            limit: 预览行数上限。

        Returns:
            PreviewTable: 预览结果。
        """
        # 延迟导入以避免与 packages.connectors.__init__ 的循环依赖。
        from packages.connectors import build_connector

        secret_store = SecretStore(self._factory, self._dept_id)
        connector = build_connector(
            source,
            secret_store=secret_store,
            artifact_service=self._artifact_service,
        )
        return await connector.preview(source, limit=limit)
