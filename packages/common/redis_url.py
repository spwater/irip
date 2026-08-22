"""Redis 连接 URL 读取（file-backed secret 优先）。

约定（阶段2 层次2）：**完整 redis URL 走 ``*_FILE``**。优先读取
``IRIP_REDIS_URL_FILE`` 指向的 secret 文件（内容为完整的
``redis://:password@host:port/db`` 连接串，含 requirepass 密码），
文件不存在/未配置时回退到 ``IRIP_REDIS_URL`` 环境变量。两者皆缺失时
返回 ``default``。

该约定与 ``packages.common.database.get_database_url`` 同构，侵入最小：
调用方从 ``os.getenv("IRIP_REDIS_URL", default)`` 一句式替换为
``get_redis_url(default)`` 即可，无需在运行时据密码重拼连接串。

Celery broker / result backend 也复用本函数（``apps.worker.celery_app`` 的
``REDIS_URL`` 同时作为 broker 与 backend），因此无需单独的
``IRIP_CELERY_BROKER_URL_FILE`` / ``IRIP_CELERY_RESULT_BACKEND_FILE`` 密钥，
侵入最小且语义清晰。
"""


def get_redis_url(default: str = "redis://localhost:6379/0") -> str:
    """读取运行时 redis 连接串（file-backed secret 优先）。

    Args:
        default: 连接串缺失时返回的默认值（开发环境用于指向本地 redis）。

    Returns:
        str: redis 连接串（如 ``redis://:password@host:6379/0``），
        缺失时返回 ``default``。
    """
    from packages.common.secret_files import read_secret

    return read_secret("IRIP_REDIS_URL", required=False) or default
