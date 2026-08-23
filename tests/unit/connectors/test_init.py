"""单元测试：连接器包 __init__.py（build_connector 工厂 + 导出）。

覆盖 ``packages/connectors/__init__.py``：
- build_connector：按 kind 构造连接器实例
- __all__ 导出列表验证
- 异常路径：未知 kind、缺少 secret_store
"""

from unittest.mock import MagicMock

import pytest

from packages.common.errors import AppError
from packages.connectors import (
    ConnectorSource,
    FileConnector,
    PostgresConnector,
    PreviewTable,
    RestConnector,
    SecretKind,
    SourceRecord,
    build_connector,
)

# ============================================================
# __all__ 导出
# ============================================================


class TestExports:
    """导出列表验证。"""

    def test_all_expected_names_exported(self) -> None:
        """所有期望的名称都在 __all__ 中。"""
        expected = {
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
        }
        from packages.connectors import __all__

        assert set(__all__) == expected

    def test_connector_source_is_dataclass(self) -> None:
        """ConnectorSource 可正确构造。"""
        source = ConnectorSource(kind="file", config={"format": "csv"})
        assert source.kind == "file"

    def test_preview_table_is_dataclass(self) -> None:
        """PreviewTable 可正确构造。"""
        table = PreviewTable(columns=("a",), rows=((1,),), row_count=1)
        assert table.row_count == 1

    def test_source_record_is_dataclass(self) -> None:
        """SourceRecord 可正确构造。"""
        record = SourceRecord(fields={"a": "1"})
        assert record.fields["a"] == "1"

    def test_secret_kind_enum(self) -> None:
        """SecretKind 枚举值正确。"""
        assert SecretKind.POSTGRES_DSN == "postgres_dsn"
        assert SecretKind.REST_TOKEN == "rest_token"


# ============================================================
# build_connector: file
# ============================================================


class TestBuildConnectorFile:
    """build_connector file 类型测试。"""

    def test_file_connector_no_secret_store(self) -> None:
        """file 连接器不需要 secret_store。"""
        source = ConnectorSource(kind="file", config={"format": "csv"})
        connector = build_connector(source)
        assert isinstance(connector, FileConnector)

    def test_file_connector_with_artifact_service(self) -> None:
        """file 连接器接受 artifact_service。"""
        artifact_svc = MagicMock()
        source = ConnectorSource(kind="file", config={"format": "csv"})
        connector = build_connector(source, artifact_service=artifact_svc)
        assert isinstance(connector, FileConnector)

    def test_file_connector_no_artifact_service(self) -> None:
        """file 连接器 artifact_service 可选。"""
        source = ConnectorSource(kind="file", config={"format": "csv"})
        connector = build_connector(source)
        assert isinstance(connector, FileConnector)


# ============================================================
# build_connector: postgres
# ============================================================


class TestBuildConnectorPostgres:
    """build_connector postgres 类型测试。"""

    def test_postgres_connector(self) -> None:
        """postgres 连接器需要 secret_store。"""
        mock_store = MagicMock()
        source = ConnectorSource(kind="postgres", config={"query": "SELECT 1"})
        connector = build_connector(source, secret_store=mock_store)
        assert isinstance(connector, PostgresConnector)

    def test_postgres_without_secret_store_raises(self) -> None:
        """postgres 缺少 secret_store 抛 AppError。"""
        source = ConnectorSource(kind="postgres", config={"query": "SELECT 1"})
        with pytest.raises(AppError, match="需要 secret_store"):
            build_connector(source)


# ============================================================
# build_connector: rest
# ============================================================


class TestBuildConnectorRest:
    """build_connector rest 类型测试。"""

    def test_rest_connector(self) -> None:
        """rest 连接器需要 secret_store。"""
        mock_store = MagicMock()
        source = ConnectorSource(kind="rest", config={"path": "/api"})
        connector = build_connector(source, secret_store=mock_store)
        assert isinstance(connector, RestConnector)

    def test_rest_without_secret_store_raises(self) -> None:
        """rest 缺少 secret_store 抛 AppError。"""
        source = ConnectorSource(kind="rest", config={"path": "/api"})
        with pytest.raises(AppError, match="需要 secret_store"):
            build_connector(source)


# ============================================================
# build_connector: unknown kind
# ============================================================


class TestBuildConnectorUnknown:
    """build_connector 未知类型测试。"""

    def test_unknown_kind_raises(self) -> None:
        """未知 kind 抛 AppError。"""
        source = ConnectorSource(kind="mongodb", config={})  # type: ignore[arg-type]
        with pytest.raises(AppError, match="未知数据源类型"):
            build_connector(source)

    def test_empty_kind_raises(self) -> None:
        source = ConnectorSource(kind="", config={})  # type: ignore[arg-type]
        with pytest.raises(AppError, match="未知数据源类型"):
            build_connector(source)


# ============================================================
# build_connector: 返回类型符合 Connector 协议
# ============================================================


class TestBuildConnectorReturnType:
    """build_connector 返回类型测试。"""

    def test_file_connector_is_connector(self) -> None:
        source = ConnectorSource(kind="file", config={"format": "csv"})
        connector = build_connector(source)
        assert isinstance(connector, FileConnector)
        # FileConnector 结构性地满足 Connector 协议
        assert hasattr(connector, "preview")
        assert hasattr(connector, "read")

    def test_postgres_connector_is_connector(self) -> None:
        mock_store = MagicMock()
        source = ConnectorSource(kind="postgres", config={"query": "SELECT 1"})
        connector = build_connector(source, secret_store=mock_store)
        assert isinstance(connector, PostgresConnector)
        assert hasattr(connector, "preview")
        assert hasattr(connector, "read")

    def test_rest_connector_is_connector(self) -> None:
        mock_store = MagicMock()
        source = ConnectorSource(kind="rest", config={"path": "/api"})
        connector = build_connector(source, secret_store=mock_store)
        assert isinstance(connector, RestConnector)
        assert hasattr(connector, "preview")
        assert hasattr(connector, "read")
