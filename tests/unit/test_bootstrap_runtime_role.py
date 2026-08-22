"""bootstrap 运行时角色密码重设单元测试。

覆盖 ``deployments.compose.bootstrap.ensure_runtime_role_password``：
- 密码非空时执行 ALTER ROLE（psycopg sql.Literal 安全转义，非 f-string 拼接）；
- 密码为空时 fail-open 跳过（不执行 SQL、仅打 warning）。
"""


class _FakeSession:
    """记录 execute 调用的最小异步会话替身。"""

    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, stmt, *args, **kwargs) -> None:
        """记录被执行的 SQL 文本。"""
        self.executed.append(str(stmt))


async def test_runtime_role_password_set_when_secret_present(monkeypatch) -> None:
    """密码非空时，执行 ALTER ROLE 且密码被安全转义内联。"""
    from deployments.compose.bootstrap import ensure_runtime_role_password

    monkeypatch.setenv("IRIP_DATABASE_APP_PASSWORD", "sup3r-secret!")
    monkeypatch.delenv("IRIP_DATABASE_APP_PASSWORD_FILE", raising=False)

    session = _FakeSession()
    await ensure_runtime_role_password(session)  # type: ignore[arg-type]

    assert len(session.executed) == 1
    sql = session.executed[0]
    assert sql.startswith("ALTER ROLE irip_app LOGIN PASSWORD ")
    # 密码以引号包裹的字面量安全内联（非 f-string 裸拼接）
    assert "'sup3r-secret!'" in sql


async def test_runtime_role_password_escapes_special_chars(monkeypatch) -> None:
    """含单引号/反斜杠的密码被正确转义，避免 SQL 注入。"""
    from deployments.compose.bootstrap import ensure_runtime_role_password

    tricky = "a'b\\c; DROP ROLE x--"
    monkeypatch.setenv("IRIP_DATABASE_APP_PASSWORD", tricky)
    monkeypatch.delenv("IRIP_DATABASE_APP_PASSWORD_FILE", raising=False)

    session = _FakeSession()
    await ensure_runtime_role_password(session)  # type: ignore[arg-type]

    sql = session.executed[0]
    assert sql.startswith("ALTER ROLE irip_app LOGIN PASSWORD ")
    # 单引号必须转义为相邻两个单引号，注入片段以字面量形式被引号包裹，
    # 不会破坏语句结构。
    assert "a''b" in sql
    assert "DROP ROLE x--" in sql
    # 原始密码中的裸单引号不应以未转义形式出现在语句中。
    assert "a'b" not in sql


async def test_runtime_role_password_skipped_when_secret_empty(monkeypatch) -> None:
    """密码为空时 fail-open 跳过，不执行任何 SQL。"""
    from deployments.compose.bootstrap import ensure_runtime_role_password

    monkeypatch.delenv("IRIP_DATABASE_APP_PASSWORD", raising=False)
    monkeypatch.delenv("IRIP_DATABASE_APP_PASSWORD_FILE", raising=False)

    session = _FakeSession()
    await ensure_runtime_role_password(session)  # type: ignore[arg-type]

    assert session.executed == []
