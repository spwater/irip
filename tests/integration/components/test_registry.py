"""组件注册表集成测试。

验证（IRIP V2-T01）：
- 发布组件版本后自动生成编码与版本号；
- 按 name + version 查询；
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
    Path(__file__).resolve().parents[3] / "schemas" / "component-manifest" / "v1.schema.json"
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

#: 有效清单 YAML — ingestion 组件 v2（内容不同，用于测试版本递增）。
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
  - name: metadata
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
    return FixedClock(instant=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


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
        department_id=test_user.department_id,  # type: ignore[attr-defined]
        actor_id=test_user.user_id,  # type: ignore[attr-defined]
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
        """发布后创建组件主记录和版本记录（自动生成编码，版本号 1.0.0）。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        version = await registry_service.publish(manifest)

        assert version.version == "1.0.0"
        assert version.runtime == "python"
        assert version.status == "published"
        assert version.published_at is not None
        assert version.manifest_sha256 == manifest.sha256
        # manifest_yaml 中 name 已被替换为自动生成的编码
        comp, _ = await registry_service.get_version_by_id(version.id)
        assert comp.name in version.manifest_yaml

    async def test_publish_same_manifest_creates_new_component(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """重复发布相同清单会创建新组件（自动生成不同编码），不抛 conflict。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        version1 = await registry_service.publish(manifest)
        version2 = await registry_service.publish(manifest)

        # 两次发布创建不同的组件（自动生成不同编码）
        comp1, _ = await registry_service.get_version_by_id(version1.id)
        comp2, _ = await registry_service.get_version_by_id(version2.id)
        assert comp1.name != comp2.name
        assert version1.version == "1.0.0"
        assert version2.version == "1.0.0"

    async def test_publish_new_version_succeeds(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """同一组件发布新版本成功（版本号自动递增 patch）。"""
        manifest_v1 = validator.validate(VALID_YAML_INGESTION)
        version_v1 = await registry_service.publish(manifest_v1)

        # 获取自动生成的组件名，用它发布新版本
        comp, _ = await registry_service.get_version_by_id(version_v1.id)
        v2_yaml = VALID_YAML_INGESTION_V2.replace(
            "name: csv_ingestion", f"name: {comp.name}"
        )
        manifest_v2 = validator.validate(v2_yaml)
        version_v2 = await registry_service.publish(manifest_v2)

        assert version_v2.version == "1.0.1"
        assert version_v2.status == "published"

    async def test_publish_kind_mismatch_fails(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """同名组件 kind 不一致时抛出 conflict。"""
        manifest1 = validator.validate(VALID_YAML_INGESTION)
        version1 = await registry_service.publish(manifest1)

        # 获取自动生成的组件名
        comp, _ = await registry_service.get_version_by_id(version1.id)
        component_name = comp.name

        # 用自动生成的名字但不同 kind 发布
        mismatched_yaml = VALID_YAML_INGESTION.replace(
            "name: csv_ingestion", f"name: {component_name}"
        ).replace("kind: ingestion", "kind: transform")
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
        """按 name + version 查询成功（使用自动生成的编码）。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        version = await registry_service.publish(manifest)

        comp, _ = await registry_service.get_version_by_id(version.id)
        result = await registry_service.get(comp.name, "1.0.0")
        assert result.version == "1.0.0"
        assert result.manifest_sha256 == manifest.sha256

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
        version = await registry_service.publish(manifest)

        comp, _ = await registry_service.get_version_by_id(version.id)
        with pytest.raises(AppError) as exc_info:
            await registry_service.get(comp.name, "99.0.0")
        assert exc_info.value.code == "not_found"

    async def test_get_version_by_id(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """按版本 UUID 查询成功。"""
        manifest = validator.validate(VALID_YAML_INGESTION)
        version = await registry_service.publish(manifest)

        comp, ver = await registry_service.get_version_by_id(version.id)
        assert comp.name.startswith("iface_")
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
        await registry_service.publish(validator.validate(VALID_YAML_INGESTION))
        await registry_service.publish(validator.validate(VALID_YAML_TRANSFORM))

        items = await registry_service.list()
        assert len(items) == 2

    async def test_list_filter_by_kind(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """按 kind 过滤。"""
        await registry_service.publish(validator.validate(VALID_YAML_INGESTION))
        await registry_service.publish(validator.validate(VALID_YAML_TRANSFORM))
        await registry_service.publish(validator.validate(VALID_YAML_QUALITY))

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
        version = await registry_service.publish(validator.validate(VALID_YAML_INGESTION))
        comp, _ = await registry_service.get_version_by_id(version.id)
        await registry_service.deprecate(comp.name)

        items = await registry_service.list(status="deprecated")
        assert len(items) == 1
        comp_item, _ver = items[0]
        assert comp_item.status == "deprecated"

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
        """同一组件多版本，列表返回组件及其当前活跃版本。"""
        version_v1 = await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        comp, _ = await registry_service.get_version_by_id(version_v1.id)
        component_name = comp.name

        v2_yaml = VALID_YAML_INGESTION_V2.replace(
            "name: csv_ingestion", f"name: {component_name}"
        )
        await registry_service.publish(validator.validate(v2_yaml))

        items = await registry_service.list()
        assert len(items) == 1  # 同一组件只出现一次
        _comp, ver = items[0]
        assert ver.version == "1.0.1"  # 当前活跃版本为最新


@pytest.mark.asyncio
class TestDeprecateComponent:
    """废弃组件测试。"""

    async def test_deprecate_published_component(
        self,
        registry_service: ComponentRegistryService,
        validator: ManifestValidator,
    ) -> None:
        """废弃已发布组件。"""
        version = await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        comp, _ = await registry_service.get_version_by_id(version.id)

        component = await registry_service.deprecate(comp.name)
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
        version = await registry_service.publish(
            validator.validate(VALID_YAML_INGESTION)
        )
        comp, _ = await registry_service.get_version_by_id(version.id)
        await registry_service.deprecate(comp.name)

        items = await registry_service.list(status="deprecated")
        assert len(items) == 1
        comp_item, _ver = items[0]
        assert comp_item.name == comp.name
        assert comp_item.status == "deprecated"
