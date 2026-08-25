"""S3 兼容对象存储客户端封装（基于 boto3）。

封装 MinIO / S3 的常用操作，屏蔽 boto3 细节，供 ArtifactService 使用：
- ensure_bucket: 幂等创建 bucket；
- put_object / put_object_stream / get_object / head_object: 基本对象操作；
- presigned_put / presigned_get / create_presigned_post: 预签名 URL 生成。

H-04 增强：
- create_presigned_post: 生成带 content-length-range 的 POST policy；
- put_object_stream: 流式上传大对象（不整对象读入内存）。

所有方法为同步（boto3 限制），调用方在异步上下文中应使用 asyncio.to_thread() 包装。
"""

import logging
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ObjectInfo:
    """S3 对象元数据（H-04: HEAD 验证用）。

    Attributes:
        key: 对象 key。
        size: 对象大小（字节）。
        content_type: MIME 类型。
        etag: 对象 ETag。
    """

    key: str
    size: int
    content_type: str
    etag: str


class S3Repository:
    """S3 兼容对象存储客户端封装。

    通过 boto3 连接 MinIO 或任意 S3 兼容服务。所有方法同步执行，
    异步调用方需使用 ``asyncio.to_thread()`` 包装。

    Attributes:
        _client: boto3 S3 客户端实例。
        _bucket: 默认 bucket 名称。
    """

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket_name: str = "irip",
        region: str = "us-east-1",
        external_endpoint_url: str | None = None,
    ) -> None:
        """初始化 S3 客户端。

        Args:
            endpoint_url: S3 兼容端点 URL（如 ``http://minio:9000``）。
            access_key: 访问密钥。
            secret_key: 秘密密钥。
            bucket_name: 默认 bucket 名称。
            region: 区域名（MinIO 默认 us-east-1）。
            external_endpoint_url: 外部访问端点 URL（用于生成预签名 URL）。
                为 None 时预签名 URL 用内部端点生成。非 None 时，预签名
                URL 用该端点生成，确保签名与浏览器访问的 host 一致。
        """
        self._client: BaseClient = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name=region,
        )
        self._bucket: str = bucket_name
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        # 预签名专用 client：用外部端点生成 URL，确保签名 host 与浏览器访问一致
        if external_endpoint_url:
            self._presign_client: BaseClient = boto3.client(
                "s3",
                endpoint_url=external_endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=BotoConfig(signature_version="s3v4"),
                region_name=region,
            )
        else:
            self._presign_client = self._client

    @property
    def bucket(self) -> str:
        """返回默认 bucket 名称。"""
        return self._bucket

    def delete_object(self, key: str) -> None:
        """删除 S3 对象（幂等，不存在时静默返回）。

        Args:
            key: S3 object key。
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError:
            pass

    def ensure_bucket(self) -> None:
        """幂等创建默认 bucket，并配置生命周期规则。

        若 bucket 已存在则静默返回，否则创建之。
        创建后自动配置生命周期规则：
        - research/artifacts/ 前缀：365 天后自动转移到归档存储
        - temp/ 前缀：7 天后自动删除
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            logger.info("Created S3 bucket: %s", self._bucket)

        # P2-I5: 配置 MinIO 生命周期策略
        self._configure_lifecycle()

    def _configure_lifecycle(self) -> None:
        """配置 bucket 生命周期规则（幂等）。

        - research/artifacts/ 前缀：365 天后归档
        - temp/ 前缀：7 天后删除
        """
        lifecycle_config: dict[str, Any] = {
            "Rules": [
                {
                    "ID": "archive-old-research-artifacts",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "research/artifacts/"},
                    "Transitions": [
                        {"Days": 365, "StorageClass": "STANDARD_IA"},
                    ],
                },
                {
                    "ID": "expire-temp-objects",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "temp/"},
                    "Expiration": {"Days": 7},
                },
            ]
        }
        try:
            self._client.put_bucket_lifecycle_configuration(
                Bucket=self._bucket,
                LifecycleConfiguration=lifecycle_config,
            )
            logger.debug("Configured lifecycle rules for bucket: %s", self._bucket)
        except Exception:
            logger.warning("Failed to configure lifecycle rules (non-fatal)", exc_info=True)

    def put_object(
        self,
        key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        """上传字节数据到指定 key。

        Args:
            key: 对象 key（如 ``sha256/ab/abcdef...``）。
            data: 字节内容。
            content_type: MIME 类型。
        """
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get_object(self, key: str) -> bytes:
        """下载对象内容。

        Args:
            key: 对象 key。

        Returns:
            bytes: 对象内容。

        Raises:
            ClientError: 对象不存在时抛出 NoSuchKey。
        """
        response: dict[str, Any] = self._client.get_object(
            Bucket=self._bucket,
            Key=key,
        )
        body: Any = response["Body"]
        data: bytes = body.read()
        return data

    def head_object(self, key: str) -> dict[str, Any]:
        """获取对象元数据（不下载内容）。

        Args:
            key: 对象 key。

        Returns:
            dict: HeadObject 响应（含 ContentLength、ContentType 等）。

        Raises:
            ClientError: 对象不存在时抛出 404。
        """
        result: dict[str, Any] = self._client.head_object(Bucket=self._bucket, Key=key)
        return result

    def head_object_info(self, key: str) -> ObjectInfo:
        """获取对象元数据并返回类型化结果（H-04: HEAD 验证用）。

        Args:
            key: 对象 key。

        Returns:
            ObjectInfo: 包含 size、content_type、etag 的类型化元数据。

        Raises:
            ClientError: 对象不存在时抛出 404。
        """
        result: dict[str, Any] = self._client.head_object(Bucket=self._bucket, Key=key)
        return ObjectInfo(
            key=key,
            size=int(result.get("ContentLength", 0)),
            content_type=str(result.get("ContentType", "application/octet-stream")),
            etag=str(result.get("ETag", "")),
        )

    def put_object_stream(self, key: str, file_obj: Any, content_type: str) -> None:
        """流式上传大对象（H-04/H-09: 不整对象读入内存）。

        使用 boto3 upload_fileobj 进行分片上传，适用于大文件。

        Args:
            key: 对象 key。
            file_obj: 可读的文件类对象（有 read 方法）。
            content_type: MIME 类型。
        """
        self._client.upload_fileobj(
            Fileobj=file_obj,
            Bucket=self._bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )

    def object_exists(self, key: str) -> bool:
        """检查对象是否存在。

        Args:
            key: 对象 key。

        Returns:
            bool: 存在返回 True，否则 False。
        """
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def _make_presign_client(self, endpoint_override: str | None = None) -> BaseClient:
        """构造用于生成预签名 URL 的 client。

        无 override 时用 _presign_client（外部端点或内部端点）。
        有 override 时动态创建临时 client，确保签名 host 与浏览器访问一致。
        """
        if not endpoint_override:
            return self._presign_client
        return boto3.client(
            "s3",
            endpoint_url=endpoint_override,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name=self._region,
        )

    def presigned_put(
        self,
        key: str,
        expires: int = 3600,
        endpoint_override: str | None = None,
    ) -> str:
        """生成预签名 PUT URL。

        Args:
            key: 对象 key。
            expires: URL 有效期（秒），默认 3600。
            endpoint_override: 可选，用指定端点生成签名 URL（host 与浏览器一致）。

        Returns:
            str: 预签名 PUT URL。
        """
        client = self._make_presign_client(endpoint_override)
        url: str = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )
        return url

    def presigned_get(
        self,
        key: str,
        expires: int = 3600,
        endpoint_override: str | None = None,
    ) -> str:
        """生成预签名 GET URL。

        Args:
            key: 对象 key。
            expires: URL 有效期（秒），默认 3600。
            endpoint_override: 可选，用指定端点生成签名 URL（host 与浏览器一致）。

        Returns:
            str: 预签名 GET URL。
        """
        client = self._make_presign_client(endpoint_override)
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )
        return url

    def create_presigned_post(
        self,
        key: str,
        expires: int = 3600,
        max_size: int = 100 * 1024 * 1024,
        content_type: str | None = None,
        endpoint_override: str | None = None,
    ) -> dict[str, str]:
        """生成带 content-length-range 的预签名 POST（H-04: 上传大小限制）。

        使用 S3 POST policy 机制，在服务端强制限制上传文件大小，
        客户端无法绕过。POST policy 中的 content-length-range 条件
        确保超限对象在接收正文前即被 S3/MinIO 拒绝。

        Args:
            key: 对象 key。
            expires: URL 有效期（秒），默认 3600。
            max_size: 最大允许上传字节数（默认 100 MiB）。
            content_type: 可选，限制 MIME 类型。
            endpoint_override: 可选，用指定端点生成签名 URL。

        Returns:
            dict: 包含 url 和 fields 的字典，客户端用于构造 multipart POST。
        """
        client = self._make_presign_client(endpoint_override)
        conditions: list[Any] = [
            ["content-length-range", 0, max_size],
        ]
        fields: dict[str, str] = {"key": key}
        if content_type is not None:
            conditions.append({"Content-Type": content_type})
            fields["Content-Type"] = content_type

        response: dict[str, Any] = client.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=expires,
        )
        return {
            "url": str(response["url"]),
            **{k: str(v) for k, v in response.get("fields", {}).items()},
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_s3_repo_from_env() -> "S3Repository":
    """从环境变量构建 S3 客户端（API 和 Worker 共用的唯一入口）。

    读取的环境变量：
    - IRIP_MINIO_ENDPOINT: 内部端点（默认 http://localhost:9000）
    - IRIP_MINIO_EXTERNAL_ENDPOINT: 外部端点（可选，用于预签名 URL）
    - IRIP_MINIO_ACCESS_KEY: 访问密钥（默认 irip）
    - IRIP_MINIO_SECRET_KEY / IRIP_MINIO_SECRET_KEY_FILE: 秘密密钥
    - IRIP_MINIO_BUCKET: bucket 名（默认 irip-artifacts）
    - IRIP_MINIO_REGION: 区域（默认 us-east-1）

    Returns:
        S3Repository: 已初始化的 S3 客户端（含 ensure_bucket）。
    """
    import os

    from packages.common.secret_files import read_secret

    endpoint = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"
    external_endpoint = os.getenv("IRIP_MINIO_EXTERNAL_ENDPOINT")
    if external_endpoint and not external_endpoint.startswith("http"):
        external_endpoint = f"http://{external_endpoint}"
    return S3Repository(
        endpoint_url=endpoint,
        access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        secret_key=read_secret("IRIP_MINIO_SECRET_KEY", required=False) or "irip_dev_password",
        bucket_name=os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
        region=os.getenv("IRIP_MINIO_REGION", "us-east-1"),
        external_endpoint_url=external_endpoint,
    )
