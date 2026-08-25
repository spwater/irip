"""File-backed secret loading with permission validation."""

import os
from pathlib import Path


class SecretFileError(Exception):
    """Secret file loading error."""

    pass


def read_secret_file(path: Path) -> str:
    """Read a secret from a file with strict permission checks.

    Rejects symlinks, non-regular files, empty content, oversized
    content (> 64 KiB), and files with group/world read or write
    permissions.
    """
    if path.is_symlink():
        raise SecretFileError(f"Secret file {path} must not be a symlink")
    if not path.is_file():
        raise SecretFileError(f"Secret file {path} is not a regular file")
    mode = path.stat().st_mode
    # Windows/Docker Desktop 挂载的 secret 文件权限恒为 777，
    # staging 环境（IRIP_STAGING=1）跳过权限检查
    is_staging = os.getenv("IRIP_STAGING", "0") == "1"
    if not is_staging and (mode & 0o077):  # group or world can read/write
        raise SecretFileError(
            f"Secret file {path} has insecure permissions "
            f"(mode {oct(mode & 0o777)}); expected 0o400 or 0o600"
        )
    content = path.read_text().strip()
    if not content:
        raise SecretFileError(f"Secret file {path} is empty")
    if len(content) > 65536:
        raise SecretFileError(f"Secret file {path} exceeds 64 KiB")
    return content


def read_secret(name: str, *, required: bool = True) -> str | None:
    """Read a secret from file (NAME_FILE) or env (NAME).

    File takes precedence over environment variable. When neither
    source is available and ``required`` is True, raises
    :class:`SecretFileError`.
    """
    file_env = f"{name}_FILE"
    file_path = os.environ.get(file_env)
    if file_path:
        return read_secret_file(Path(file_path))
    value = os.environ.get(name)
    if value:
        return value
    if required:
        raise SecretFileError(f"Secret {name} not found in env or file")
    return None
