"""组件清单校验契约测试。

验证（IRIP V2-T01）：
- 有效 manifest 通过验证；
- 缺少必填字段时验证失败；
- 端口类型不匹配时验证失败；
- 语义化版本格式检查；
- SHA-256 摘要一致性。

无数据库依赖（纯 YAML 解析 + JSON Schema 校验）。
"""

import hashlib
from pathlib import Path

import pytest

from packages.common.errors import AppError
from packages.components.manifest import ManifestValidator


#: JSON Schema 路径（相对项目根目录）。
SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "component-manifest"
    / "v1.schema.json"
)

#: 有效清单 YAML 模板。
VALID_MANIFEST_YAML: str = """\
name: csv_ingestion
version: 1.0.0
kind: ingestion
runtime: python
description: CSV 文件接入组件
inputs:
  - name: raw_file
    data_type: artifact
    required: true
outputs:
  - name: dataset
    data_type: dataset
    required: true
parameters:
  type: object
  properties:
    delimiter:
      type: string
      default: ","
dependencies:
  - field_mapper@1.0.0
timeout_seconds: 120
network_policy:
  allowed_hosts:
    - localhost
"""


@pytest.fixture
def validator() -> ManifestValidator:
    """创建清单校验器。"""
    return ManifestValidator(SCHEMA_PATH)


class TestManifestValidation:
    """清单校验测试。"""

    def test_valid_manifest_passes(
        self, validator: ManifestValidator
    ) -> None:
        """有效清单通过验证。"""
        manifest = validator.validate(VALID_MANIFEST_YAML)
        assert manifest.name == "csv_ingestion"
        assert manifest.version == "1.0.0"
        assert manifest.kind == "ingestion"
        assert manifest.runtime == "python"
        assert len(manifest.inputs) == 1
        assert manifest.inputs[0].name == "raw_file"
        assert manifest.inputs[0].data_type == "artifact"
        assert manifest.inputs[0].required is True
        assert len(manifest.outputs) == 1
        assert manifest.outputs[0].name == "dataset"
        assert manifest.dependencies == ("field_mapper@1.0.0",)
        assert manifest.raw_yaml == VALID_MANIFEST_YAML

    def test_missing_name_fails(
        self, validator: ManifestValidator
    ) -> None:
        """缺少 name 时验证失败。"""
        yaml_text = """\
version: 1.0.0
kind: ingestion
runtime: python
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_missing_version_fails(
        self, validator: ManifestValidator
    ) -> None:
        """缺少 version 时验证失败。"""
        yaml_text = """\
name: test_component
kind: ingestion
runtime: python
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_missing_kind_fails(
        self, validator: ManifestValidator
    ) -> None:
        """缺少 kind 时验证失败。"""
        yaml_text = """\
name: test_component
version: 1.0.0
runtime: python
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_missing_runtime_fails(
        self, validator: ManifestValidator
    ) -> None:
        """缺少 runtime 时验证失败。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: ingestion
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_invalid_kind_fails(
        self, validator: ManifestValidator
    ) -> None:
        """kind 不在枚举中时验证失败。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: unknown_kind
runtime: python
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_invalid_runtime_fails(
        self, validator: ManifestValidator
    ) -> None:
        """runtime 不在枚举中时验证失败。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: ingestion
runtime: javascript
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_invalid_name_pattern_fails(
        self, validator: ManifestValidator
    ) -> None:
        """name 不符合命名模式时验证失败。"""
        yaml_text = """\
name: Invalid-Name
version: 1.0.0
kind: ingestion
runtime: python
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_name_starts_with_digit_fails(
        self, validator: ManifestValidator
    ) -> None:
        """name 以数字开头时验证失败。"""
        yaml_text = """\
name: 1component
version: 1.0.0
kind: ingestion
runtime: python
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_port_missing_name_fails(
        self, validator: ManifestValidator
    ) -> None:
        """端口缺少 name 时验证失败。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: ingestion
runtime: python
inputs:
  - data_type: artifact
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_port_missing_data_type_fails(
        self, validator: ManifestValidator
    ) -> None:
        """端口缺少 data_type 时验证失败。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: ingestion
runtime: python
inputs:
  - name: raw_file
"""
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_empty_inputs_outputs(
        self, validator: ManifestValidator
    ) -> None:
        """无输入/输出端口时为空元组。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: statistics
runtime: python
"""
        manifest = validator.validate(yaml_text)
        assert manifest.inputs == ()
        assert manifest.outputs == ()

    def test_port_required_defaults_true(
        self, validator: ManifestValidator
    ) -> None:
        """端口 required 默认为 True。"""
        yaml_text = """\
name: test_component
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
        manifest = validator.validate(yaml_text)
        assert manifest.inputs[0].required is True
        assert manifest.outputs[0].required is True

    def test_port_explicit_required_false(
        self, validator: ManifestValidator
    ) -> None:
        """端口显式设置 required=False。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: transform
runtime: python
inputs:
  - name: optional_input
    data_type: dataset
    required: false
outputs:
  - name: output_data
    data_type: dataset
"""
        manifest = validator.validate(yaml_text)
        assert manifest.inputs[0].required is False

    def test_port_schema_preserved(
        self, validator: ManifestValidator
    ) -> None:
        """端口的 schema 字段被保留。"""
        yaml_text = """\
name: test_component
version: 1.0.0
kind: transform
runtime: python
inputs:
  - name: input_data
    data_type: dataset
    schema:
      type: object
      properties:
        rows:
          type: integer
outputs:
  - name: output_data
    data_type: dataset
"""
        manifest = validator.validate(yaml_text)
        assert manifest.inputs[0].schema is not None
        assert manifest.inputs[0].schema["type"] == "object"

    def test_invalid_yaml_fails(
        self, validator: ManifestValidator
    ) -> None:
        """非法 YAML 语法验证失败。"""
        yaml_text = "name: [unclosed"
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"

    def test_non_mapping_root_fails(
        self, validator: ManifestValidator
    ) -> None:
        """根节点非对象时验证失败。"""
        yaml_text = "- item1\n- item2"
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"


class TestSemanticVersioning:
    """语义化版本格式测试。"""

    @pytest.mark.parametrize(
        "version",
        [
            "1.0.0",
            "0.1.0",
            "10.20.30",
            "1.0.0-alpha",
            "1.0.0+build",
            "1.0.0-alpha.1",
            "1.0.0+build.123",
        ],
    )
    def test_valid_semver_passes(
        self,
        validator: ManifestValidator,
        version: str,
    ) -> None:
        """有效语义化版本通过。"""
        yaml_text = (
            f"name: test_component\n"
            f"version: {version}\n"
            f"kind: ingestion\n"
            f"runtime: python\n"
        )
        manifest = validator.validate(yaml_text)
        assert manifest.version == version

    @pytest.mark.parametrize(
        "version",
        ["1.0", "v1.0.0", "1.0.0.0", "latest", "1", "1.0.0.0.0"],
    )
    def test_invalid_semver_fails(
        self,
        validator: ManifestValidator,
        version: str,
    ) -> None:
        """无效语义化版本失败。"""
        yaml_text = (
            f"name: test_component\n"
            f"version: {version}\n"
            f"kind: ingestion\n"
            f"runtime: python\n"
        )
        with pytest.raises(AppError) as exc_info:
            validator.validate(yaml_text)
        assert exc_info.value.code == "invalid_manifest"


class TestSha256Digest:
    """SHA-256 摘要一致性测试。"""

    def test_sha256_matches_manual_hash(
        self, validator: ManifestValidator
    ) -> None:
        """SHA-256 摘要与手动计算一致。"""
        yaml_text = VALID_MANIFEST_YAML
        manifest = validator.validate(yaml_text)
        expected = hashlib.sha256(
            yaml_text.encode("utf-8")
        ).hexdigest()
        assert manifest.sha256 == expected
        assert len(manifest.sha256) == 64

    def test_same_content_same_digest(
        self, validator: ManifestValidator
    ) -> None:
        """相同内容产生相同摘要。"""
        manifest1 = validator.validate(VALID_MANIFEST_YAML)
        manifest2 = validator.validate(VALID_MANIFEST_YAML)
        assert manifest1.sha256 == manifest2.sha256

    def test_different_content_different_digest(
        self, validator: ManifestValidator
    ) -> None:
        """不同内容产生不同摘要。"""
        manifest1 = validator.validate(VALID_MANIFEST_YAML)
        modified = VALID_MANIFEST_YAML.replace(
            "csv_ingestion", "json_ingestion"
        )
        manifest2 = validator.validate(modified)
        assert manifest1.sha256 != manifest2.sha256

    def test_raw_yaml_preserved(
        self, validator: ManifestValidator
    ) -> None:
        """原始 YAML 文本被完整保存。"""
        manifest = validator.validate(VALID_MANIFEST_YAML)
        assert manifest.raw_yaml == VALID_MANIFEST_YAML
