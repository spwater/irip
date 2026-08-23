"""单元测试：密钥存储与预览服务（mapping.py）。

覆盖 ``packages/connectors/mapping.py``：
- SecretStore.get：按 ID 解析密钥值（含解密 / 明文回退）
- SecretStore.create：创建加密凭据
- IngestionService.preview：按 kind 构造连接器并预览
- IngestionService.set_rls_override：RLS 覆盖
- 异常路径：密钥不存在、凭据为空
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.connectors.contracts import ConnectorSource, PreviewTable
from packages.connectors.mapping import IngestionService, SecretStore

# ============================================================
# 辅助：mock scoped_session 上下文管理器
# ============================================================


def _make_mock_scoped_session(mock_session: AsyncMock):
    """创建一个 mock 的 scoped_session 上下文管理器工厂。

    SecretStore._scoped_session() 调用 scoped_session(self._factory, ...)
    返回的 async context manager yield 一个 AsyncMock session。
    """

    @pytest.fixture
    async def _fixture():
        # 这里不用 fixture，直接返回 patcher
        pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _mock_scoped_session(factory, dept_id=None, user_id=None):
        yield mock_session

    return _mock_scoped_session


# ============================================================
# SecretStore.get
# ============================================================


class TestSecretStoreGet:
    """SecretStore.get 密钥解析测试。"""

    async def test_get_secret_not_found_raises(self) -> None:
        """密钥不存在时抛 AppError。"""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        store = SecretStore(MagicMock(), uuid4())

        with patch(
            "packages.common.database.scoped_session",
            side_effect=_make_mock_scoped_session(mock_session),
        ):
            with pytest.raises(AppError, match="密钥不存在"):
                await store.get(uuid4())

    async def test_get_secret_decrypts_value(self) -> None:
        """成功解密密钥值。"""
        secret_id = uuid4()
        mock_secret = MagicMock()
        mock_secret.id = secret_id
        mock_secret.value = "v0:encrypted_value"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_secret
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_crypto = MagicMock()
        mock_crypto.decrypt.return_value = "plaintext-dsn"

        store = SecretStore(MagicMock(), uuid4())

        with (
            patch.dict("os.environ", {"IRIP_ENV": "test"}),
            patch("packages.common.crypto.EnvelopeCrypto.from_env", return_value=mock_crypto),
            patch("packages.common.crypto.EnvelopeCrypto.reset_singleton"),
            patch(
                "packages.common.database.scoped_session",
                side_effect=_make_mock_scoped_session(mock_session),
            ),
        ):
            result = await store.get(secret_id)

        assert result == "plaintext-dsn"
        mock_crypto.decrypt.assert_called_once_with("v0:encrypted_value")

    async def test_get_secret_fallback_plaintext_in_test(self) -> None:
        """测试环境解密失败时回退明文。"""
        secret_id = uuid4()
        mock_secret = MagicMock()
        mock_secret.id = secret_id
        mock_secret.value = "plaintext-dsn"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_secret
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_crypto = MagicMock()
        mock_crypto.decrypt.side_effect = ValueError("decrypt failed")

        store = SecretStore(MagicMock(), uuid4())

        with (
            patch.dict("os.environ", {"IRIP_ENV": "test"}),
            patch("packages.common.crypto.EnvelopeCrypto.from_env", return_value=mock_crypto),
            patch("packages.common.crypto.EnvelopeCrypto.reset_singleton"),
            patch(
                "packages.common.database.scoped_session",
                side_effect=_make_mock_scoped_session(mock_session),
            ),
        ):
            result = await store.get(secret_id)

        assert result == "plaintext-dsn"


# ============================================================
# SecretStore.create
# ============================================================


class TestSecretStoreCreate:
    """SecretStore.create 创建加密凭据测试。"""

    async def test_create_empty_value_raises(self) -> None:
        """空凭据值抛 AppError。"""
        mock_factory = MagicMock()
        store = SecretStore(mock_factory, uuid4())
        with pytest.raises(AppError, match="凭据值不能为空"):
            await store.create("postgres_dsn", "")

    async def test_create_encrypts_and_stores(self) -> None:
        """加密后存储密钥。"""
        dept_id = uuid4()
        fixed_id = uuid4()

        added_secrets: list = []

        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda s: added_secrets.append(s))

        async def mock_flush():
            """模拟 flush：设置 secret.id。"""
            for s in added_secrets:
                s.id = fixed_id

        mock_session.flush = mock_flush

        mock_crypto = MagicMock()
        mock_crypto.encrypt.return_value = "v0:encrypted"

        store = SecretStore(MagicMock(), dept_id)

        with (
            patch("packages.common.crypto.EnvelopeCrypto.from_env", return_value=mock_crypto),
            patch("packages.common.crypto.EnvelopeCrypto.reset_singleton"),
            patch(
                "packages.common.database.scoped_session",
                side_effect=_make_mock_scoped_session(mock_session),
            ),
        ):
            result = await store.create("postgres_dsn", "my-dsn-value")

        assert result == fixed_id
        mock_crypto.encrypt.assert_called_once_with("my-dsn-value")
        assert len(added_secrets) == 1

        # 验证添加的 Secret 对象属性
        added_secret = added_secrets[0]
        assert added_secret.department_id == dept_id
        assert added_secret.kind == "postgres_dsn"
        assert added_secret.value == "v0:encrypted"


# ============================================================
# IngestionService
# ============================================================


class TestIngestionService:
    """IngestionService 测试。"""

    async def test_preview_rest_source(self) -> None:
        """preview rest 数据源调用 RestConnector。"""
        mock_factory = MagicMock()
        dept_id = uuid4()

        mock_preview_table = PreviewTable(
            columns=("id", "name"),
            rows=((1, "A"),),
            row_count=1,
        )

        mock_connector = MagicMock()
        mock_connector.preview = AsyncMock(return_value=mock_preview_table)

        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(uuid4()), "path": "/data", "method": "GET"},
        )

        with patch("packages.connectors.build_connector", return_value=mock_connector):
            service = IngestionService(mock_factory, dept_id)
            result = await service.preview(source, limit=10)

        assert result == mock_preview_table
        mock_connector.preview.assert_called_once()

    async def test_preview_postgres_source(self) -> None:
        """preview postgres 数据源调用 PostgresConnector。"""
        mock_factory = MagicMock()
        dept_id = uuid4()

        mock_preview_table = PreviewTable(
            columns=("id",),
            rows=((1,),),
            row_count=1,
        )

        mock_connector = MagicMock()
        mock_connector.preview = AsyncMock(return_value=mock_preview_table)

        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": str(uuid4()), "query": "SELECT 1"},
        )

        with patch("packages.connectors.build_connector", return_value=mock_connector):
            service = IngestionService(mock_factory, dept_id)
            result = await service.preview(source, limit=10)

        assert result == mock_preview_table

    async def test_preview_file_source(self) -> None:
        """preview file 数据源调用 FileConnector。"""
        mock_factory = MagicMock()
        dept_id = uuid4()
        artifact_service = MagicMock()

        mock_preview_table = PreviewTable(
            columns=("a", "b"),
            rows=((1, 2),),
            row_count=1,
        )

        mock_connector = MagicMock()
        mock_connector.preview = AsyncMock(return_value=mock_preview_table)

        source = ConnectorSource(
            kind="file",
            config={"artifact_id": str(uuid4()), "format": "csv"},
        )

        with patch("packages.connectors.build_connector", return_value=mock_connector):
            service = IngestionService(mock_factory, dept_id, artifact_service=artifact_service)
            result = await service.preview(source, limit=10)

        assert result == mock_preview_table

    def test_set_rls_override(self) -> None:
        """set_rls_override 正确设置部门 ID。"""
        mock_factory = MagicMock()
        dept_id = uuid4()
        service = IngestionService(mock_factory, dept_id)

        override_id = uuid4()
        service.set_rls_override(override_id)
        assert service._rls_dept_id == override_id

    def test_set_rls_override_none(self) -> None:
        """set_rls_override(None) 清除覆盖。"""
        mock_factory = MagicMock()
        dept_id = uuid4()
        service = IngestionService(mock_factory, dept_id)

        service.set_rls_override(uuid4())
        service.set_rls_override(None)
        assert service._rls_dept_id is None

    async def test_preview_passes_artifact_service(self) -> None:
        """artifact_service 被传递给 build_connector。"""
        mock_factory = MagicMock()
        dept_id = uuid4()
        artifact_service = MagicMock()

        mock_connector = MagicMock()
        mock_connector.preview = AsyncMock(
            return_value=PreviewTable(columns=(), rows=(), row_count=0)
        )

        source = ConnectorSource(
            kind="file",
            config={"artifact_id": str(uuid4()), "format": "csv"},
        )

        with patch("packages.connectors.build_connector", return_value=mock_connector) as mock_bc:
            service = IngestionService(mock_factory, dept_id, artifact_service=artifact_service)
            await service.preview(source, limit=10)

        mock_bc.assert_called_once()
        call_kwargs = mock_bc.call_args
        assert call_kwargs.kwargs.get("artifact_service") is artifact_service

    async def test_preview_passes_secret_store(self) -> None:
        """secret_store 被传递给 build_connector。"""
        mock_factory = MagicMock()
        dept_id = uuid4()

        mock_connector = MagicMock()
        mock_connector.preview = AsyncMock(
            return_value=PreviewTable(columns=(), rows=(), row_count=0)
        )

        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(uuid4()), "path": "/d", "method": "GET"},
        )

        with patch("packages.connectors.build_connector", return_value=mock_connector) as mock_bc:
            service = IngestionService(mock_factory, dept_id)
            await service.preview(source, limit=10)

        mock_bc.assert_called_once()
        call_kwargs = mock_bc.call_args
        assert "secret_store" in call_kwargs.kwargs
        assert isinstance(call_kwargs.kwargs["secret_store"], SecretStore)
