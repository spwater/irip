"""IRIP 哈希工具。

约定：
- 内容寻址 / refresh token 摘要一律使用 SHA-256（hex 小写）；
- 绝不存储明文 token——仅存储 sha256_bytes(token.encode()) 的摘要。
"""

import hashlib


def sha256_bytes(data: bytes) -> str:
    """计算字节串的 SHA-256 十六进制摘要（小写）。

    Args:
        data: 任意字节内容。

    Returns:
        str: 64 位小写十六进制字符串。
    """
    return hashlib.sha256(data).hexdigest()
