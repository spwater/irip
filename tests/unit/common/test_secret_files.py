"""Secret file loader tests."""

import pytest


def test_file_secret_takes_precedence(monkeypatch, tmp_path):
    from packages.common.secret_files import read_secret

    secret = tmp_path / "jwt"
    secret.write_text("from-file\n")
    secret.chmod(0o600)
    monkeypatch.setenv("IRIP_JWT_SECRET", "from-env")
    monkeypatch.setenv("IRIP_JWT_SECRET_FILE", str(secret))
    assert read_secret("IRIP_JWT_SECRET") == "from-file"


def test_env_fallback_when_no_file(monkeypatch, tmp_path):
    from packages.common.secret_files import read_secret

    monkeypatch.setenv("IRIP_JWT_SECRET", "from-env")
    monkeypatch.delenv("IRIP_JWT_SECRET_FILE", raising=False)
    assert read_secret("IRIP_JWT_SECRET") == "from-env"


def test_required_secret_missing_raises(monkeypatch):
    from packages.common.secret_files import SecretFileError, read_secret

    monkeypatch.delenv("IRIP_TEST_SECRET", raising=False)
    monkeypatch.delenv("IRIP_TEST_SECRET_FILE", raising=False)
    with pytest.raises(SecretFileError):
        read_secret("IRIP_TEST_SECRET", required=True)


def test_world_readable_secret_is_rejected(tmp_path):
    from packages.common.secret_files import SecretFileError, read_secret_file

    secret = tmp_path / "jwt"
    secret.write_text("unsafe-secret")
    secret.chmod(0o666)
    with pytest.raises(SecretFileError):
        read_secret_file(secret)
