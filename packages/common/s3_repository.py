"""S3 兼容对象存储客户端封装（基于 boto3）。

封装 MinIO / S3 的常用操作，屏蔽 boto3 细节，供 ArtifactService 使用：
- ensure_bucket: 幂等创建 bucket；
- put_object / get_object / head_object: 基本对象操作；
- presigned_put / presigned_get: 预签名 URL 生成。

所有方法为同步（boto3 限制），调用方在异步上下文中应使用 asyncio.to_thread() 包装。
"""

import logging
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


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
        """幂等创建默认 bucket。

        若 bucket 已存在则静默返回，否则创建之。
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            logger.info("Created S3 bucket: %s", self._bucket)

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
        result: dict[str, Any] = self._client.head_object(
            Bucket=self._bucket, Key=key
        )
        return result

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

    def presigned_put(self, key: str, expires: int = 3600, endpoint_override: str | None = None) -> str:
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

    def presigned_get(self, key: str, expires: int = 3600, endpoint_override: str | None = None) -> str:
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
