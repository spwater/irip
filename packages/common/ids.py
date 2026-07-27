"""IRIP 统一 ID 生成。

Phase V0 采用 UUIDv4（随机）作为实体主键：
- 标准库零依赖、跨语言/数据库兼容性最好；
- 架构文档 §7.3 提到 UUIDv7（时间有序、索引友好），属后续优化项；
  若引入 `uuid-utils` 等依赖可平滑切换到 v7，调用方签名不变。

约束：所有实体 ID 必须经 new_id() 生成，禁止散落 uuid4() 调用。
"""

from uuid import UUID, uuid4


def new_id() -> UUID:
    """生成一个新的全局唯一 ID（UUIDv4）。

    Returns:
        UUID: 随机 UUID。未来切换 UUIDv7 时本签名保持不变。
    """
    return uuid4()


def gen_code(prefix: str = "obj") -> str:
    """自动生成实体编码（满足 ^[a-z][a-z0-9_]*$ 格式）。

    格式: {prefix}_{uuid_hex前8位}
    例: obj_a1b2c3d4, flow_e5f6g7h8

    Args:
        prefix: 编码前缀，默认 "obj"。

    Returns:
        str: 自动生成的唯一编码。
    """
    return f"{prefix}_{uuid4().hex[:8]}"
