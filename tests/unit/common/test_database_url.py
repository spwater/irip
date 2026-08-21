"""File-backed DB URL resolution tests (阶段2 A1).

验证 ``packages.common.database.get_database_url`` /
``get_database_admin_url`` 的 ``*_FILE`` 优先、环境变量回退行为：
- ``IRIP_DATABASE_URL_FILE`` 存在时从 secret 文件读取完整连接串；
- 未配置 ``_FILE`` 时回退 ``IRIP_DATABASE_URL`` 环境变量；
- 两者皆缺失时返回调用方传入的默认值；
- superuser 连接串（``IRIP_DATABASE_ADMIN_URL_FILE``）同理。
"""

from packages.common.database import get_database_admin_url, get_database_url

APP_URL: str = "postgresql+psycopg://irip_app:app_secret@postgres:5432/irip"
ADMIN_URL: str = "postgresql+psycopg://irip:admin_secret@postgres:5432/irip"


def test_database_url_reads_from_file(monkeypatch, tmp_path):
    """IRIP_DATABASE_URL_FILE 存在时，从文件读取完整连接串。"""
    secret = tmp_path / "database_app_url"
    secret.write_text(APP_URL + "\n")
    secret.chmod(0o600)

    monkeypatch.setenv("IRIP_DATABASE_URL_FILE", str(secret))
    monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql+psycopg://irip_app:inline@localhost/irip")

    assert get_database_url() == APP_URL


def test_database_url_falls_back_to_env(monkeypatch):
    """未配置 IRIP_DATABASE_URL_FILE 时，回退到 IRIP_DATABASE_URL 环境变量。"""
    monkeypatch.delenv("IRIP_DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("IRIP_DATABASE_URL", APP_URL)

    assert get_database_url() == APP_URL


def test_database_url_returns_default_when_missing(monkeypatch):
    """两者皆缺失时返回调用方默认值（不抛异常）。"""
    monkeypatch.delenv("IRIP_DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("IRIP_DATABASE_URL", raising=False)

    assert get_database_url("fallback-url") == "fallback-url"
    assert get_database_url() == ""


def test_admin_url_reads_from_file(monkeypatch, tmp_path):
    """IRIP_DATABASE_ADMIN_URL_FILE 存在时，从文件读取 superuser 连接串。"""
    secret = tmp_path / "database_admin_url"
    secret.write_text(ADMIN_URL + "\n")
    secret.chmod(0o600)

    monkeypatch.setenv("IRIP_DATABASE_ADMIN_URL_FILE", str(secret))
    monkeypatch.setenv("IRIP_DATABASE_ADMIN_URL", "postgresql+psycopg://irip:inline@localhost/irip")

    assert get_database_admin_url() == ADMIN_URL


def test_admin_url_falls_back_to_env(monkeypatch):
    """未配置 IRIP_DATABASE_ADMIN_URL_FILE 时，回退到 IRIP_DATABASE_ADMIN_URL 环境变量。"""
    monkeypatch.delenv("IRIP_DATABASE_ADMIN_URL_FILE", raising=False)
    monkeypatch.setenv("IRIP_DATABASE_ADMIN_URL", ADMIN_URL)

    assert get_database_admin_url() == ADMIN_URL


def test_admin_url_returns_default_when_missing(monkeypatch):
    """两者皆缺失时返回调用方默认值。"""
    monkeypatch.delenv("IRIP_DATABASE_ADMIN_URL_FILE", raising=False)
    monkeypatch.delenv("IRIP_DATABASE_ADMIN_URL", raising=False)

    assert get_database_admin_url("fallback-admin-url") == "fallback-admin-url"
    assert get_database_admin_url() == ""
