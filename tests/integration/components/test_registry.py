"""组件注册表集成测试。

验证（IRIP V2-T01）：
- 发布组件版本后不可变（重复发布抛 conflict）；
- 按 kind + version 查询；
- 列表过滤（kind / status）；
- 废弃组件。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL，未设置时 skip）。
fixture async_session_factory / test_user 由 tests/conftest.py 提供。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import FixedClock
from packages.common.errors import AppError
from packages.components.manifest import ManifestValidator
from packages.components.registry import ComponentRegistryService


#: JSON Schema 路径（相对项目根目录）。
SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "component-manifest"
    / "v1.schema.json"
)

#: 有效清单 YAML — ingestion 组件 v1。
VALID_YAML_INGESTION: str = """\
name: csv_ingestion
version: 1.0.0
kind: ingestion
runtime: python
inputs:
  - name: raw_file
    data_type: artifact
outputs:
  - name: dataset
    data_type: dataset
"""

#: 有效清单 YAML — transform 组件 v1。
VALID_YAML_TRANSFORM: str = """\
name: field_mapper
version: 1.0.0
kind: transform
runtime: python
inputs:
  - name: input_data
    data_type: dataset
outputs:
  - name: output_data
    data_type: dataset
"""

#: 有效清单 YAML — ingestion 组件 v2。
VALID_YAML_INGESTION_V2: str = """\
name: csv_ingestion
version: 2.0.0
kind: ingestion
runtime: python
inputs:
  - name: raw_file
    data_type: artifact
outputs:
  - name: dataset
    data_type: dataset
"""

#: 有效清单 YAML — quality 组件。
VALID_YAML_QUALITY: str = """\
name: null_check
version: 1.0.0
kind: quality
runtime: python
inputs:
  - name: input_data
    data_type: dataset
outputs:
  - name: report
    data_type: report
"""


@pytest.fixture
def validator() -> ManifestValidator:
    """创建清单校验器。"""
    return ManifestValidator(SCHEMA_PATH)


@pytest.fixture
def fixed_clock() -> FixedClock:
    """固定时钟（确定性测试）。"""
    return FixedClock(
        instant=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    )


@pytest.fixture
async def registry_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    fixed_clock: FixedClock,
) -> ComponentRegistryService:
    """组件注册表服务。

    依赖 tests/conftest.py 的 async_session_factory 与 test_user fixture。
    若 IRIP_TEST_DATABASE_URL 未设置，上游 fixture 会 skip。
    """
    return ComponentRegistryService(
        session_factory=async_session_factory,
        organization_id=test_user.organization_id,  # type: ignore[attr-defined]
        clock=fixed_clock,
    )


@pytest.mark.asyncio
class TestPublishComponent:
    """发布组件测试。"""

    async def test_publish_creates_component_and_version(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """发布后创建组件主记录和版本记录。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        version = await registry_service.publish(manifest)

        assert version.version == "1.0.0"
        assert version.runtime == "python"
        assert version.status == "published"
        assert version.published_at is not None
        assert version.manifest_sha256 == manifest.sha256
        assert version.manifest_yaml == manifest.raw_yaml

    async def test_published_version_is_immutable(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """发布同一版本时抛出 conflict（已发布版本不可变）。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        await registry_service.publish(manifest)

        with pytest.raises(AppError) as exc_info:
            await registry_service.publish(manifest)
        assert exc_info.value.code == "conflict"

    async def test_publish_new_version_succeeds(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """同一组件发布新版本成功。"""
        manifest_v1 = validator.validate(VALID_YAML_INGESTION)
        await registry_service.publish(manifest_v1)

        manifest_v2 = validator.validate(VALID_YAML_INGESTION_V2)
        version_v2 = await registry_service.publish(manifest_v2)

        assert version_v2.version == "2.0.0"
        assert version_v2.status == "published"

    async def test_publish_kind_mismatch_fails(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """同名组件 kind 不一致时抛出 conflict。"""
        manifest1 = validator.validate(VALID_YAML_INGESTION)
        await registry_service.publish(manifest1)

        mismatched_yaml = VALID_YAML_INGESTION.replace(
            "kind: ingestion", "kind: transform"
        )
        manifest2 = validator.validate(mismatched_yaml)
        with pytest.raises(AppError) as exc_info:
            await registry_service.publish(manifest2)
        assert exc_info.value.code == "conflict"


@pytest.mark.asyncio
class TestGetComponent:
    """查询组件测试。"""

    async def test_get_by_name_and_version(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """按 name + version 查询成功。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        await registry_service.publish(manifest)

        version = await registry_service.get(
            "csv_ingestion", "1.0.0"
        )
        assert version.version == "1.0.0"
        assert version.manifest_sha256 == manifest.sha256

    async def test_get_nonexistent_name_fails(
        self,
        registry_service: ComponentRegistryService,
    ) -> None:
        """查询不存在的组件名抛出 not_found。"""
        with pytest.raises(AppError) as exc_info:
            await registry_service.get("nonexistent", "1.0.0")
        assert exc_info.value.code == "not_found"

    async def test_get_nonexistent_version_fails(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """查询不存在的版本抛出 not_found。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        await registry_service.publish(manifest)

        with pytest.raises(AppError) as exc_info:
            await registry_service.get("csv_ingestion", "99.0.0")
        assert exc_info.value.code == "not_found"

    async def test_get_version_by_id(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """按版本 UUID 查询成功。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        version = await registry_service.publish(manifest)

        comp, ver = await registry_service.get_version_by_id(
            version.id
        )
        assert comp.name == "csv_ingestion"
        assert ver.version == "1.0.0"
        assert ver.id == version.id

    async def test_get_version_by_nonexistent_id_fails(
        self,
        registry_service: ComponentRegistryService,
    ) -> None:
        """查询不存在的版本 UUID 抛出 not_found。"""
        from uuid import uuid4

        with pytest.raises(AppError) as exc_info:
            await registry_service.get_version_by_id(uuid4())
        assert exc_info.value.code == "not_found"


@pytest.mark.asyncio
class TestListComponents:
    """列表过滤测试。"""

    async def test_list_all(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """列出所有组件。"""
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        await registry_service.publish(
            validator.validate(VALID_YAML_TRANSFORM)
        )

        items = await registry_service.list()
        assert len(items) == 2

    async def test_list_filter_by_kind(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """按 kind 过滤。"""
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        await registry_service.publish(
            validator.validate(VALID_YAML_TRANSFORM)
        )
        await registry_service.publish(
            validator.validate(VALID_YAML_QUALITY)
        )

        items = await registry_service.list(kind="ingestion")
        assert len(items) == 1
        comp, _ver = items[0]
        assert comp.kind == "ingestion"

        items = await registry_service.list(kind="quality")
        assert len(items) == 1

        items = await registry_service.list(kind="transform")
        assert len(items) == 1

    async def test_list_filter_by_status(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """按 status 过滤。"""
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        await registry_service.deprecate("csv_ingestion")

        items = await registry_service.list(status="deprecated")
        assert len(items) == 1
        comp, _ver = items[0]
        assert comp.status == "deprecated"

        items = await registry_service.list(status="published")
        assert len(items) == 0

    async def test_list_empty(
        self,
        registry_service: ComponentRegistryService,
    ) -> None:
        """无组件时返回空列表。"""
        items = await registry_service.list()
        assert len(items) == 0

    async def test_list_multiple_versions(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """同一组件多版本均出现在列表中。"""
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION_V2)
        )

        items = await registry_service.list()
        assert len(items) == 2
        versions = [ver.version for _comp, ver in items]
        assert "1.0.0" in versions
        assert "2.0.0" in versions


@pytest.mark.asyncio
class TestDeprecateComponent:
    """废弃组件测试。"""

    async def test_deprecate_published_component(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """废弃已发布组件。"""
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )

        component = await registry_service.deprecate("csv_ingestion")
        assert component.status == "deprecated"

    async def test_deprecate_nonexistent_fails(
        self,
        registry_service: ComponentRegistryService,
    ) -> None:
        """废弃不存在的组件抛出 not_found。"""
        with pytest.raises(AppError) as exc_info:
            await registry_service.deprecate("nonexistent")
        assert exc_info.value.code == "not_found"

    async def test_deprecated_component_still_listed(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """废弃后仍可通过 status=deprecated 列出。"""
        await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        await registry_service.deprecate("csv_ingestion")

        items = await registry_service.list(status="deprecated")
        assert len(items) == 1
        comp, _ver = items[0]
        assert comp.name == "csv_ingestion"
        assert comp.status == "deprecated"
