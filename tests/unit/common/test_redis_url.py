"""File-backed Redis URL resolution tests (阶段2 层次2).

验证 ``packages.common.redis_url.get_redis_url`` 的 ``*_FILE`` 优先、环境变量
回退行为：
- ``IRIP_REDIS_URL_FILE`` 存在时从 secret 文件读取完整连接串；
- 未配置 ``_FILE`` 时回退 ``IRIP_REDIS_URL`` 环境变量；
- 两者皆缺失时返回调用方传入的默认值。
"""

from packages.common.redis_url import get_redis_url

REDIS_URL: str = "redis://:redis_secret@redis:6379/0"


def test_redis_url_reads_from_file(monkeypatch, tmp_path):
    """IRIP_REDIS_URL_FILE 存在时，从文件读取完整连接串。"""
    secret = tmp_path / "redis_url"
    secret.write_text(REDIS_URL + "\n")
    secret.chmod(0o600)

    monkeypatch.setenv("IRIP_REDIS_URL_FILE", str(secret))
    monkeypatch.setenv("IRIP_REDIS_URL", "redis://:inline@localhost:6379/0")

    assert get_redis_url() == REDIS_URL


def test_redis_url_falls_back_to_env(monkeypatch):
    """未配置 IRIP_REDIS_URL_FILE 时，回退到 IRIP_REDIS_URL 环境变量。"""
    monkeypatch.delenv("IRIP_REDIS_URL_FILE", raising=False)
    monkeypatch.setenv("IRIP_REDIS_URL", REDIS_URL)

    assert get_redis_url() == REDIS_URL


def test_redis_url_returns_default_when_missing(monkeypatch):
    """两者皆缺失时返回调用方默认值（不抛异常）。"""
    monkeypatch.delenv("IRIP_REDIS_URL_FILE", raising=False)
    monkeypatch.delenv("IRIP_REDIS_URL", raising=False)

    assert get_redis_url("fallback-url") == "fallback-url"
    assert get_redis_url() == "redis://localhost:6379/0"
