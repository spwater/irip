"""单元测试：artifacts 内容寻址工件服务。

覆盖 _build_object_key + ALLOWED_MEDIA_TYPES + ArtifactRef + ArtifactService 基本方法。
"""

from uuid import uuid4

from packages.common.artifacts import (
    ALLOWED_MEDIA_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    ArtifactRef,
    _build_object_key,
)


class TestBuildObjectKey:
    """_build_object_key 测试。"""

    def test_key_format(self) -> None:
        """key 格式为 sha256/<前2位>/<digest>。"""
        sha = "a" * 64
        key = _build_object_key(sha)
        assert key == f"sha256/aa/{sha}"

    def test_key_with_different_prefix(self) -> None:
        """不同前缀正确。"""
        sha = "b" * 64
        key = _build_object_key(sha)
        assert key.startswith("sha256/bb/")


class TestAllowedMediaTypes:
    """ALLOWED_MEDIA_TYPES 测试。"""

    def test_contains_common_types(self) -> None:
        assert "text/plain" in ALLOWED_MEDIA_TYPES
        assert "application/pdf" in ALLOWED_MEDIA_TYPES
        assert "image/png" in ALLOWED_MEDIA_TYPES
        assert "application/json" in ALLOWED_MEDIA_TYPES

    def test_excludes_unsafe_types(self) -> None:
        assert "application/x-executable" not in ALLOWED_MEDIA_TYPES
        assert "text/html" not in ALLOWED_MEDIA_TYPES

    def test_max_upload_size(self) -> None:
        assert MAX_UPLOAD_SIZE_BYTES == 100 * 1024 * 1024


class TestArtifactRef:
    """ArtifactRef 值对象测试。"""

    def test_creation(self) -> None:
        ref = ArtifactRef(
            artifact_id=uuid4(),
            object_key="sha256/aa/aaa",
            sha256="a" * 64,
            media_type="text/plain",
            size_bytes=100,
            filename="test.txt",
        )
        assert ref.size_bytes == 100
        assert ref.media_type == "text/plain"
