"""C-01 摄入预览：SourceFileConfig 只接受 artifact_id。

覆盖 T02 修改的 ``apps/api/routers/ingestions.py``：
- ``SourceFileConfig.path: str`` → ``artifact_id: UUID``；
- 不再接受任意服务器路径 ``path`` 字段（消除路径遍历攻击面）。

本测试为纯单元测试，验证 Pydantic 模型的字段定义和校验行为，
不依赖数据库或外部服务。
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.routers.ingestions import (
    PreviewRequest,
    SourceFileConfig,
    SourceSpec,
)


class TestSourceFileConfigArtifactOnly:
    """SourceFileConfig 只接受 artifact_id。"""

    def test_artifact_id_required(self) -> None:
        """artifact_id 是必填字段。"""
        with pytest.raises(ValidationError) as exc_info:
            SourceFileConfig(format="csv")  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("artifact_id",) for e in errors)

    def test_artifact_id_accepts_valid_uuid(self) -> None:
        """接受有效的 UUID 作为 artifact_id。"""
        artifact_id = uuid4()
        config = SourceFileConfig(artifact_id=artifact_id, format="csv")
        assert config.artifact_id == artifact_id

    def test_artifact_id_accepts_uuid_string(self) -> None:
        """接受 UUID 字符串作为 artifact_id（Pydantic 自动转换）。"""
        artifact_id_str = str(uuid4())
        config = SourceFileConfig(artifact_id=artifact_id_str, format="csv")
        assert str(config.artifact_id) == artifact_id_str

    def test_format_required(self) -> None:
        """format 是必填字段。"""
        with pytest.raises(ValidationError) as exc_info:
            SourceFileConfig(artifact_id=uuid4())  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("format",) for e in errors)

    @pytest.mark.parametrize("fmt", ["csv", "xlsx", "json"])
    def test_format_accepts_valid_values(self, fmt: str) -> None:
        """接受 csv / xlsx / json 三种格式。"""
        config = SourceFileConfig(artifact_id=uuid4(), format=fmt)
        assert config.format == fmt

    def test_format_rejects_invalid_value(self) -> None:
        """拒绝非法格式值。"""
        with pytest.raises(ValidationError) as exc_info:
            SourceFileConfig(artifact_id=uuid4(), format="xml")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("format",) for e in errors)

    def test_artifact_id_rejects_non_uuid_string(self) -> None:
        """拒绝非 UUID 字符串作为 artifact_id。"""
        with pytest.raises(ValidationError) as exc_info:
            SourceFileConfig(artifact_id="not-a-uuid", format="csv")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("artifact_id",) for e in errors)

    def test_artifact_id_rejects_file_path(self) -> None:
        """拒绝文件路径字符串作为 artifact_id（防止路径遍历）。"""
        malicious_paths = [
            "/etc/passwd",
            "../../../etc/shadow",
            "/tmp/irip-backup/database.dump",
            "C:\\Windows\\System32\\config\\SAM",
            "/var/lib/postgresql/data/pg_hba.conf",
        ]
        for path in malicious_paths:
            with pytest.raises(ValidationError) as exc_info:
                SourceFileConfig(artifact_id=path, format="csv")
            errors = exc_info.value.errors()
            assert any(e["loc"] == ("artifact_id",) for e in errors), (
                f"Path '{path}' should be rejected as artifact_id"
            )


class TestSourceFileConfigNoPathField:
    """SourceFileConfig 不再接受 path 字段。"""

    def test_no_path_field_in_model(self) -> None:
        """SourceFileConfig 模型字段中不含 path。"""
        fields = SourceFileConfig.model_fields
        assert "path" not in fields
        assert "artifact_id" in fields
        assert "format" in fields

    def test_path_field_rejected_by_extra_ignore(self) -> None:
        """传入 path 字段时，因 Pydantic 默认忽略额外字段而不报错，
        但 path 不会出现在 model_dump 中。"""
        artifact_id = uuid4()
        config = SourceFileConfig(
            artifact_id=artifact_id,
            format="csv",
            path="/etc/passwd",  # type: ignore[call-arg]
        )
        dumped = config.model_dump()
        assert "path" not in dumped
        assert "artifact_id" in dumped
        assert "format" in dumped

    def test_model_dump_contains_artifact_id_not_path(self) -> None:
        """model_dump 输出包含 artifact_id 而非 path。"""
        artifact_id = uuid4()
        config = SourceFileConfig(artifact_id=artifact_id, format="csv")
        dumped = config.model_dump()
        assert str(dumped["artifact_id"]) == str(artifact_id)
        assert "path" not in dumped


class TestSourceSpecWithFile:
    """SourceSpec 携带 file 类型时的行为。"""

    def test_source_spec_with_file_kind(self) -> None:
        """SourceSpec 的 file 字段接受 SourceFileConfig。"""
        artifact_id = uuid4()
        file_config = SourceFileConfig(artifact_id=artifact_id, format="csv")
        spec = SourceSpec(kind="file", file=file_config)
        assert spec.kind == "file"
        assert spec.file is not None
        assert spec.file.artifact_id == artifact_id

    def test_source_spec_file_config_has_artifact_id(self) -> None:
        """SourceSpec 中 file 配置的 artifact_id 正确传递。"""
        artifact_id = uuid4()
        spec = SourceSpec(
            kind="file",
            file=SourceFileConfig(artifact_id=artifact_id, format="xlsx"),
        )
        assert spec.file is not None
        assert spec.file.artifact_id == artifact_id
        assert spec.file.format == "xlsx"

    def test_preview_request_with_file_source(self) -> None:
        """PreviewRequest 携带 file 类型 SourceSpec 正确解析。"""
        artifact_id = uuid4()
        request = PreviewRequest(
            source=SourceSpec(
                kind="file",
                file=SourceFileConfig(artifact_id=artifact_id, format="json"),
            ),
            limit=50,
        )
        assert request.source.kind == "file"
        assert request.source.file is not None
        assert request.source.file.artifact_id == artifact_id
        assert request.limit == 50
