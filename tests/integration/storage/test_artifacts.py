"""工件存储集成测试。

验证内容寻址存储的核心行为（docs/arch-v0.md §3.1 + Task 6 计划第 467-503 行）：
- 相同内容复用 blob 但保留独立业务链接（去重）；
- 哈希不匹配时 verify 返回 False；
- 未授权下载返回 401（缺少认证令牌）。

前置依赖：
- Docker compose 中的 minio-test 服务已启动（localhost:59000）；
- artifact_service fixture（在 conftest.py 中定义）。
"""

import pytest

from packages.common.artifacts import (
    ArtifactBlob,
    ArtifactService,
)
from packages.common.errors import AppError
from packages.common.hashing import sha256_bytes


@pytest.mark.integration
async def test_identical_content_reuses_blob_but_keeps_two_business_links(
    artifact_service: ArtifactService,
) -> None:
    """相同内容复用 blob 但保留两个独立业务链接。"""
    first = await artifact_service.put_bytes(b"same", "text/plain", "a.txt")
    second = await artifact_service.put_bytes(b"same", "text/plain", "b.txt")

    assert first.sha256 == second.sha256
    assert first.object_key == second.object_key
    assert first.artifact_id != second.artifact_id


@pytest.mark.integration
async def test_verify_returns_true_for_correct_content(
    artifact_service: ArtifactService,
) -> None:
    """校验正确内容的 SHA-256 返回 True。"""
    ref = await artifact_service.put_bytes(b"hello", "text/plain", "greeting.txt")
    assert await artifact_service.verify(ref.artifact_id) is True


@pytest.mark.integration
async def test_different_content_creates_separate_blobs(
    artifact_service: ArtifactService,
) -> None:
    """不同内容创建独立的 blob。"""
    ref_a = await artifact_service.put_bytes(b"content-a", "text/plain", "a.txt")
    ref_b = await artifact_service.put_bytes(b"content-b", "text/plain", "b.txt")

    assert ref_a.sha256 != ref_b.sha256
    assert ref_a.object_key != ref_b.object_key
    assert ref_a.artifact_id != ref_b.artifact_id


@pytest.mark.integration
async def test_media_type_allowlist_enforced(
    artifact_service: ArtifactService,
) -> None:
    """不在白名单中的媒体类型被拒绝。"""
    with pytest.raises(AppError, match="不支持的媒体类型"):
        await artifact_service.put_bytes(b"data", "application/octet-stream", "file.bin")


@pytest.mark.integration
async def test_allowed_media_types_accepted(
    artifact_service: ArtifactService,
) -> None:
    """白名单中的媒体类型全部可接受。"""
    test_cases = [
        (b"plain text", "text/plain", "file.txt"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "file.png"),
        (b"\xff\xd8\xff", "image/jpeg", "file.jpg"),
    ]
    for data, media_type, filename in test_cases:
        ref = await artifact_service.put_bytes(data, media_type, filename)
        assert ref.media_type == media_type
        assert ref.size_bytes == len(data)


@pytest.mark.integration
async def test_object_key_format(
    artifact_service: ArtifactService,
) -> None:
    """S3 object key 格式为 sha256/<前2位>/<digest>。"""
    data = b"test content for key format"
    ref = await artifact_service.put_bytes(data, "text/plain", "test.txt")
    expected_sha256 = sha256_bytes(data)
    expected_key = f"sha256/{expected_sha256[:2]}/{expected_sha256}"

    assert ref.sha256 == expected_sha256
    assert ref.object_key == expected_key


@pytest.mark.integration
async def test_verify_nonexistent_artifact_raises(
    artifact_service: ArtifactService,
) -> None:
    """校验不存在的工件抛出 not_found 错误。"""
    from uuid import uuid4

    with pytest.raises(AppError, match="工件不存在"):
        await artifact_service.verify(uuid4())


@pytest.mark.integration
async def test_presign_download_returns_url(
    artifact_service: ArtifactService,
) -> None:
    """预签名下载返回有效 URL。"""
    ref = await artifact_service.put_bytes(b"download me", "text/plain", "dl.txt")
    url = await artifact_service.presign_download(ref.artifact_id)

    assert isinstance(url, str)
    assert len(url) > 0
    assert "http" in url.lower()


@pytest.mark.integration
async def test_get_artifact_returns_ref(
    artifact_service: ArtifactService,
) -> None:
    """get_artifact 返回正确的工件引用。"""
    ref = await artifact_service.put_bytes(b"get me", "text/plain", "get.txt")
    retrieved = await artifact_service.get_artifact(ref.artifact_id)

    assert retrieved.artifact_id == ref.artifact_id
    assert retrieved.sha256 == ref.sha256
    assert retrieved.object_key == ref.object_key
    assert retrieved.media_type == ref.media_type
    assert retrieved.size_bytes == ref.size_bytes


@pytest.mark.integration
async def test_hash_mismatch_detected(
    artifact_service: ArtifactService,
) -> None:
    """哈希不匹配时 verify 返回 False。

    上传内容后篡改 S3 对象，verify 应检测到不一致。
    使用随机内容避免与之前测试运行的 blob 去重冲突。
    """
    import asyncio
    import os

    from packages.common.s3_repository import S3Repository

    # 使用随机内容确保 blob 是新的（避免之前运行残留的 tampered 对象）
    unique_content = b"original content " + os.urandom(8)

    # 上传正确内容
    ref = await artifact_service.put_bytes(unique_content, "text/plain", "original.txt")
    assert await artifact_service.verify(ref.artifact_id) is True

    # 篡改 S3 对象（直接覆盖同一 key 的内容）
    s3_repo: S3Repository = artifact_service._s3  # type: ignore[attr-defined]
    await asyncio.to_thread(
        s3_repo.put_object,
        ref.object_key,
        b"tampered content",
        "text/plain",
    )

    # verify 应检测到哈希不匹配
    assert await artifact_service.verify(ref.artifact_id) is False


@pytest.mark.integration
async def test_unauthorized_download_returns_401(
    artifact_service: ArtifactService,
) -> None:
    """未授权下载返回 401（缺少认证令牌）。"""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from apps.api.routers.uploads import (
        artifacts_router,
        get_artifact_service,
    )
    from packages.common.errors import AppError

    # 先创建一个工件
    ref = await artifact_service.put_bytes(b"protected", "text/plain", "secret.txt")

    app = FastAPI(title="IRIP Unauthorized Test")
    app.include_router(artifacts_router)

    # 覆盖 artifact_service 依赖
    app.dependency_overrides[get_artifact_service] = lambda: artifact_service

    # AppError 处理器
    _STATUS_MAP: dict[str, int] = {
        "not_found": 404,
        "forbidden": 403,
        "invalid_credentials": 401,
        "unsupported_media_type": 415,
    }

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    client = TestClient(app)

    # 不带 Authorization header → 401
    response = client.get(f"/api/v1/artifacts/{ref.artifact_id}/download")
    assert response.status_code == 401


@pytest.mark.integration
async def test_blob_dedup_does_not_reupload(
    artifact_service: ArtifactService,
    async_session_factory,
) -> None:
    """相同内容的 blob 去重后不会在 S3 中产生重复对象。"""
    ref1 = await artifact_service.put_bytes(b"unique-dedup", "text/plain", "first.txt")
    ref2 = await artifact_service.put_bytes(b"unique-dedup", "text/plain", "second.txt")

    assert ref1.sha256 == ref2.sha256
    assert ref1.object_key == ref2.object_key

    import sqlalchemy as sa

    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(ArtifactBlob).where(ArtifactBlob.sha256 == ref1.sha256)
        )
        blobs = result.scalars().all()
        assert len(blobs) == 1
