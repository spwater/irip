"""上传内存预算（Program Gate 5 Step 2，与 tests/security/test_upload_limits.py 配套）。

契约：
1. 100 MiB 边界：上传大小上限常量恰为 100 MiB，流式分块单元为 64 KiB；
2. 超限流在写入后端前中止：``ArtifactService.complete_upload`` 先经 HEAD
   核对实际大小，超过 100 MiB 时直接删除临时对象并拒绝，**不下载正文**，
   即不把 100 MiB 读入内存；
3. 峰值额外常驻内存：超限拒绝路径由于只处理 HEAD 元数据，峰值额外内存
   应远低于 16 MiB（用 ``tracemalloc`` 测量，阈值保守避免 CI 抖动）。

设计说明：内存测量在单测/CI 环境对真实 100 MiB 对象不稳定（受 GC、线程池、
boto3 底层缓冲影响），故对"边界内存"采用行为断言（mock S3 验证 HEAD 拒绝
发生时 ``get_object`` 从未被调用 → 没有一次性读入正文）+ 对拒绝路径的
tracemalloc 峰值测量（该路径本就不分配大对象，测量稳定）。这不是空壳：
它证明的是"超限流在写入后端前中止"这一内存安全机制本身。
"""

from __future__ import annotations

import tracemalloc
from uuid import uuid4

import pytest

from packages.common.artifacts import (
    MAX_UPLOAD_SIZE_BYTES,
    ArtifactService,
)
from packages.common.errors import AppError
from packages.common.s3_repository import ObjectInfo

#: 峰值额外常驻内存预算（Gate 5 Step 2）。
MEMORY_BUDGET_BYTES = 16 * 1024 * 1024


class _FakeS3:
    """记录调用的 S3 替身：HEAD 返回给定大小，正文读取会立即失败。"""

    def __init__(self, size_bytes: int) -> None:
        self._size = size_bytes
        self.get_object_called = False
        self.delete_object_called = False
        self.put_object_called = False

    def head_object_info(self, key: str) -> ObjectInfo:
        return ObjectInfo(
            key=key,
            size=self._size,
            content_type="text/csv",
            etag="etag-123",
        )

    def get_object(self, key: str) -> bytes:
        self.get_object_called = True
        raise AssertionError("get_object 不应被调用：超限对象禁止下载正文")

    def delete_object(self, key: str) -> None:
        self.delete_object_called = True

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        self.put_object_called = True


def _make_service(size_bytes: int) -> tuple[ArtifactService, _FakeS3]:
    """构造带假 S3 的 ArtifactService（超限路径不触碰 session_factory）。"""
    fake = _FakeS3(size_bytes)
    service = ArtifactService(
        s3_repo=fake,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type] — 超限路径在 DB 前即拒绝
        department_id=uuid4(),
        uploaded_by=uuid4(),
    )
    return service, fake


class TestUploadMemoryBudget:
    """上传内存预算契约。"""

    def test_max_upload_size_is_100_mib(self) -> None:
        """MAX_UPLOAD_SIZE_BYTES 恰为 100 MiB。"""
        assert MAX_UPLOAD_SIZE_BYTES == 100 * 1024 * 1024
        assert MAX_UPLOAD_SIZE_BYTES == 104_857_600

    def test_streaming_chunk_size_is_bounded_64_kib(self) -> None:
        """流式分块单元为 64 KiB（不存在整对象读入内存的分块规模）。"""
        from packages.common.artifacts import CHUNK_SIZE

        assert CHUNK_SIZE == 64 * 1024

    async def test_oversized_stream_rejected_before_body_read(self) -> None:
        """超限流（HEAD 报告 > 100 MiB）在下载正文前中止，并清理临时对象。"""
        service, fake = _make_service(size_bytes=MAX_UPLOAD_SIZE_BYTES + 1)

        with pytest.raises(AppError) as exc_info:
            await service.complete_upload(
                temp_key="uploads/00000000/00000000-0000-0000-0000-000000000000",
                media_type="text/csv",
                filename="huge.csv",
                expected_sha256="0" * 64,
                expected_size=MAX_UPLOAD_SIZE_BYTES + 1,
            )

        assert exc_info.value.code == "file_too_large"
        assert fake.get_object_called is False, "超限对象正文绝不能被读入内存"
        assert fake.put_object_called is False, "超限对象不得写入后端"
        assert fake.delete_object_called is True, "超限临时对象应被清理"

    async def test_oversized_rejection_peak_memory_under_budget(self) -> None:
        """超限拒绝路径峰值额外常驻内存 < 16 MiB（只处理 HEAD 元数据）。"""
        service, fake = _make_service(size_bytes=10 * 1024 * 1024 * 1024)  # 10 GiB

        tracemalloc.start()
        try:
            with pytest.raises(AppError):
                await service.complete_upload(
                    temp_key="uploads/00000000/00000000-0000-0000-0000-000000000000",
                    media_type="text/csv",
                    filename="huge.csv",
                    expected_sha256="0" * 64,
                    expected_size=10 * 1024 * 1024 * 1024,
                )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert fake.get_object_called is False
        # HEAD 拒绝不下载正文，峰值额外内存只来自元数据，远低于 16 MiB
        assert peak < MEMORY_BUDGET_BYTES, (
            f"超限拒绝路径峰值额外内存 {peak} 字节 ≥ 预算 {MEMORY_BUDGET_BYTES}"
        )
