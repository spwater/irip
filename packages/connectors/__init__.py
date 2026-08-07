"""连接器包：外部数据源接入与预览。

公开导出：
- 值类型与协议：ConnectorSource / PreviewTable / SourceRecord / Connector
  （来自 contracts）。
- ORM：Secret / SecretKind（来自 entities）。
- 服务：IngestionService / SecretStore（来自 mapping）。
- 连接器实现：FileConnector / PostgresConnector / RestConnector。
- 工厂：build_connector(source, secret_store) → Connector 实例。

映射相关类（MappingRule / MappingCandidate / MappingProfile /
MappingProfileVersion / ProfileStatus / MappingService /
MappingProfileService）已随标准层空表清理删除（migration 0057）。
"""

from packages.connectors.contracts import (
    Connector,
    ConnectorSource,
    PreviewTable,
    SourceRecord,
)
from packages.connectors.entities import (
    Secret,
    SecretKind,
)
from packages.connectors.file_connectors import FileConnector
from packages.connectors.mapping import (
    IngestionService,
    SecretStore,
)
from packages.connectors.postgres_connector import PostgresConnector
from packages.connectors.rest_connector import RestConnector


def build_connector(
    source: ConnectorSource,
    secret_store: SecretStore | None = None,
    artifact_service: object | None = None,
) -> Connector:
    """按数据源类型构造连接器实例。

    Args:
        source: 数据源描述（kind + config）。
        secret_store: 密钥存储（postgres/rest 连接器需要，file 连接器忽略）。
        artifact_service: 工件服务（file 连接器需要，C-01: 从 artifact 流读取）。

    Returns:
        Connector: 对应类型的连接器实例。

    Raises:
        AppError: code="validation_failed"，当 kind 未知时。
    """
    from packages.common.errors import AppError

    if source.kind == "file":
        return FileConnector(artifact_service=artifact_service)  # type: ignore[return-value]
    if source.kind == "postgres":
        if secret_store is None:
            raise AppError(
                code="validation_failed",
                message="postgres 数据源需要 secret_store",
                retryable=False,
                fields={"kind": source.kind},
            )
        return PostgresConnector(secret_store=secret_store)  # type: ignore[return-value]
    if source.kind == "rest":
        if secret_store is None:
            raise AppError(
                code="validation_failed",
                message="rest 数据源需要 secret_store",
                retryable=False,
                fields={"kind": source.kind},
            )
        return RestConnector(secret_store=secret_store)  # type: ignore[return-value]
    raise AppError(
        code="validation_failed",
        message=f"未知数据源类型：{source.kind}",
        retryable=False,
        fields={"kind": source.kind},
    )


__all__ = [
    "Connector",
    "ConnectorSource",
    "FileConnector",
    "IngestionService",
    "PostgresConnector",
    "PreviewTable",
    "RestConnector",
    "Secret",
    "SecretKind",
    "SecretStore",
    "SourceRecord",
    "build_connector",
]
