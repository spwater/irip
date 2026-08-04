"""迁移后数据校验脚本 — 防止 department_id 挂 root 导致全员可见。

校验内容:
  1. 哨兵部门存在性（root + system）
  2. department_id 为 NULL 的行数（迁移后应为 0）
  3. department_id 指向 root 的行数（敏感表必须为 0，内容表大量挂 root 告警）
  4. 敏感表（secret / backup_record）是否误挂 root（必须为 0）
  5. department_id 孤儿引用（指向不存在的部门）
  6. 部门树完整性（root 无父、system 父为 root）

用法:
  cd irip && set -a && source .env && set +a && \
  IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip" \
  .venv/bin/python scripts/validate_department_tenant.py

退出码:
  0 — 全部通过
  1 — 存在 WARN（需人工审视）
  2 — 存在 FAIL（必须修复）
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ── 表分类 ──────────────────────────────────────────────

# A 类：内容数据（归属 + 可共享），挂 root = 全员可见（公共数据可接受，大量则告警）
A_TABLES: list[str] = [
    "fact",
    "parameter",
    "evidence_set",
    "artifact",
    "model",
    "transformation_recipe",
    "component",
    "flow_definition",
    "industrial_object",
    "equipment",
]

# B 类：运营数据（仅归属），敏感表必须挂 system 不能挂 root
B_TABLES: list[str] = [
    "job",
    "flow_run",
    "derivation_run",
    "audit_event",
    "secret",
    "backup_record",
    "app_user",
    "scope_grant",
]

# 敏感 B 类表：department_id 必须指向 system，绝不能指向 root
SENSITIVE_TABLES: list[str] = ["secret", "backup_record"]

# A 类表中挂 root 的合理上限（超过则告警，提示回填 fallback 可能过度）
A_ROOT_WARN_THRESHOLD: int = 50

# B 类表中挂 root 的预期行——admin 属 root 部门，其创建的 job / audit_event / app_user
# 自然带 root 归属，对称可见性模型下全员可见属设计意图，非泄露。
# 以下表挂 root 时降为 INFO 而非 WARN。
B_ROOT_EXPECTED_TABLES: set[str] = {"job", "audit_event", "app_user"}

# ── 结果收集 ────────────────────────────────────────────

Severity = Literal["PASS", "WARN", "FAIL", "INFO"]


@dataclass
class CheckResult:
    name: str
    severity: Severity
    detail: str = ""
    table: str | None = None
    count: int | None = None


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def has_fail(self) -> bool:
        return any(r.severity == "FAIL" for r in self.results)

    @property
    def has_warn(self) -> bool:
        return any(r.severity == "WARN" for r in self.results)

    @property
    def exit_code(self) -> int:
        if self.has_fail:
            return 2
        if self.has_warn:
            return 1
        return 0


# ── 辅助 ────────────────────────────────────────────────

def fmt_count(n: int | None) -> str:
    if n is None:
        return "?"
    return str(n)


def table_exists(session, table: str) -> bool:
    """检查表是否存在（scope_grant 可能已删）。"""
    # 由调用方异步执行
    return True  # placeholder, actual check in async context


async def check_table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t)"
        ),
        {"t": table_name},
    )
    return bool(result.scalar())


# ── 校验函数 ────────────────────────────────────────────

async def check_sentinels(conn, report: Report) -> tuple[str | None, str | None]:
    """1. 哨兵部门存在性。"""
    root_row = await conn.execute(
        sa.text("SELECT id, display_name FROM department WHERE code = 'root' AND parent_id IS NULL")
    )
    root = root_row.first()

    if root:
        report.add(CheckResult("哨兵 root 存在", "PASS",
                                f"id={root[0]}, name='{root[1]}'"))
    else:
        report.add(CheckResult("哨兵 root 存在", "FAIL",
                                "department 表中无 code='root' AND parent_id IS NULL 的行，请先跑 bootstrap"))
        return None, None

    system_row = await conn.execute(
        sa.text("SELECT id, display_name FROM department WHERE code = 'system'")
    )
    system = system_row.first()

    if system:
        report.add(CheckResult("哨兵 system 存在", "PASS",
                                f"id={system[0]}, name='{system[1]}'"))
    else:
        report.add(CheckResult("哨兵 system 存在", "FAIL",
                                "department 表中无 code='system' 的行，请先跑 bootstrap"))
        return str(root[0]), None

    # system 的 parent 必须是 root
    if str(system[0]) and root:
        sys_parent = await conn.execute(
            sa.text("SELECT parent_id FROM department WHERE code = 'system'")
        )
        parent_id = sys_parent.scalar()
        if parent_id and str(parent_id) == str(root[0]):
            report.add(CheckResult("system 父节点 = root", "PASS"))
        else:
            report.add(CheckResult("system 父节点 = root", "FAIL",
                                    f"system.parent_id={parent_id} != root.id={root[0]}"))

    return str(root[0]), str(system[0])


async def check_null_dept(conn, table: str, report: Report) -> int:
    """2. department_id 为 NULL 的行数。"""
    exists = await check_table_exists(conn, table)
    if not exists:
        report.add(CheckResult(f"{table} 表存在", "INFO", "表不存在（可能已删除），跳过", table=table))
        return 0

    result = await conn.execute(
        sa.text(f"SELECT count(*) FROM {table} WHERE department_id IS NULL")
    )
    count = int(result.scalar() or 0)

    if count == 0:
        report.add(CheckResult(f"{table}.department_id 无 NULL", "PASS", table=table, count=0))
    else:
        report.add(CheckResult(f"{table}.department_id 有 NULL", "FAIL",
                               f"{count} 行 department_id 为 NULL，迁移回填未完成", table=table, count=count))
    return count


async def check_root_dept(conn, table: str, root_id: str, report: Report,
                          is_sensitive: bool = False, is_a_class: bool = False) -> int:
    """3. department_id 指向 root 的行数。"""
    exists = await check_table_exists(conn, table)
    if not exists:
        return 0

    result = await conn.execute(
        sa.text(f"SELECT count(*) FROM {table} WHERE department_id = :root_id"),
        {"root_id": root_id},
    )
    count = int(result.scalar() or 0)

    if is_sensitive:
        # 敏感表挂 root = FAIL
        if count == 0:
            report.add(CheckResult(f"{table} 不挂 root（敏感表）", "PASS", table=table, count=0))
        else:
            report.add(CheckResult(f"{table} 误挂 root（敏感表）", "FAIL",
                                   f"{count} 行 department_id=root，敏感数据全员可见！必须改为 system",
                                   table=table, count=count))
    elif is_a_class:
        # A 类表挂 root 可接受但大量则告警
        if count == 0:
            report.add(CheckResult(f"{table} 不挂 root", "PASS", table=table, count=0))
        elif count <= A_ROOT_WARN_THRESHOLD:
            report.add(CheckResult(f"{table} 挂 root（少量公共数据）", "PASS",
                                   f"{count} 行挂 root，属正常公共数据", table=table, count=count))
        else:
            report.add(CheckResult(f"{table} 大量挂 root", "WARN",
                                   f"{count} 行挂 root，超过阈值 {A_ROOT_WARN_THRESHOLD}，"
                                   "可能回填 fallback 过度导致数据泄露风险",
                                   table=table, count=count))
    else:
        # B 类非敏感表
        if count == 0:
            report.add(CheckResult(f"{table} 不挂 root", "PASS", table=table, count=0))
        elif table in B_ROOT_EXPECTED_TABLES:
            # admin 属 root 部门，其创建的数据自然挂 root，对称可见性下全员可见属设计意图
            report.add(CheckResult(f"{table} 挂 root（admin 操作，设计预期）", "INFO",
                                   f"{count} 行挂 root，admin 属 root 部门，对称可见性下全员可见属设计意图",
                                   table=table, count=count))
        else:
            report.add(CheckResult(f"{table} 挂 root", "WARN",
                                   f"{count} 行挂 root，运营数据挂 root 即全员可见，确认是否有意",
                                   table=table, count=count))
    return count


async def check_orphan_dept(conn, table: str, report: Report) -> int:
    """4. department_id 孤儿引用（指向不存在的部门）。"""
    exists = await check_table_exists(conn, table)
    if not exists:
        return 0

    result = await conn.execute(
        sa.text(f"""
            SELECT count(*) FROM {table} t
            WHERE t.department_id IS NOT NULL
              AND t.department_id NOT IN (SELECT id FROM department)
        """)
    )
    count = int(result.scalar() or 0)

    if count == 0:
        report.add(CheckResult(f"{table} 无孤儿引用", "PASS", table=table, count=0))
    else:
        report.add(CheckResult(f"{table} 有孤儿引用", "FAIL",
                               f"{count} 行 department_id 指向不存在的部门",
                               table=table, count=count))
    return count


async def check_dept_tree(conn, report: Report, root_id: str) -> None:
    """5. 部门树完整性。"""
    # root 不可被 re-parent（parent_id 必须为 NULL）
    root_parent = await conn.execute(
        sa.text("SELECT parent_id FROM department WHERE code = 'root'")
    )
    parent = root_parent.scalar()
    if parent is None:
        report.add(CheckResult("root 无父节点", "PASS"))
    else:
        report.add(CheckResult("root 无父节点", "FAIL",
                               f"root.parent_id={parent}，应为 NULL"))

    # 检查是否有循环引用
    cycle = await conn.execute(
        sa.text("""
            WITH RECURSIVE cycle_check AS (
                SELECT id, parent_id, ARRAY[id] AS path
                FROM department
                WHERE parent_id IS NOT NULL
                UNION ALL
                SELECT d.id, d.parent_id, c.path || d.parent_id
                FROM department d
                JOIN cycle_check c ON d.id = c.parent_id
                WHERE d.parent_id IS NOT NULL
                  AND d.parent_id <> ANY(c.path)
            )
            SELECT count(*) FROM cycle_check c
            JOIN department d ON d.id = c.parent_id
            WHERE d.parent_id = ANY(c.path)
        """)
    )
    cycle_count = int(cycle.scalar() or 0)
    if cycle_count == 0:
        report.add(CheckResult("部门树无循环引用", "PASS"))
    else:
        report.add(CheckResult("部门树无循环引用", "FAIL",
                               f"检测到 {cycle_count} 个循环引用"))

    # 检查 root 不可禁用/删除
    root_status = await conn.execute(
        sa.text("SELECT status FROM department WHERE code = 'root'")
    )
    status = root_status.scalar()
    if status == "active":
        report.add(CheckResult("root 状态 active", "PASS"))
    else:
        report.add(CheckResult("root 状态 active", "FAIL",
                               f"root.status='{status}'，应为 'active'"))


async def check_system_members(conn, report: Report, system_id: str) -> None:
    """6. system 哨兵部门成员检查。"""
    result = await conn.execute(
        sa.text("""
            SELECT u.email, u.display_name
            FROM app_user u
            WHERE u.department_id = :system_id
            ORDER BY u.email
        """),
        {"system_id": system_id},
    )
    members = result.fetchall()

    if len(members) == 0:
        report.add(CheckResult("system 部门成员", "WARN",
                               "system 部门无成员，system_service 用户可能未创建"))
    elif len(members) == 1:
        report.add(CheckResult("system 部门成员", "PASS",
                               f"仅 {members[0][1]} ({members[0][0]})，符合预期"))
    else:
        names = ", ".join(f"{m[1]} ({m[0]})" for m in members)
        report.add(CheckResult("system 部门成员", "WARN",
                               f"system 部门有 {len(members)} 个成员: {names}，"
                               "应仅 system_service 用户"))


# ── 主函数 ──────────────────────────────────────────────

async def main() -> int:
    db_url = os.getenv("IRIP_DATABASE_URL", "").replace(
        "postgresql+psycopg://", "postgresql+psycopg_async://", 1
    )
    if not db_url:
        print("ERROR: IRIP_DATABASE_URL not set")
        print("用法: cd irip && set -a && source .env && set +a && \\")
        print('  IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip" \\')
        print("  .venv/bin/python scripts/validate_department_tenant.py")
        return 2

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    report = Report()

    print("=" * 70)
    print("  IRIP 迁移后数据校验 — department_id 租户隔离")
    print("=" * 70)

    async with factory() as session:
        conn = session

        # 1. 哨兵部门
        print("\n[1/6] 哨兵部门检查...")
        root_id, system_id = await check_sentinels(conn, report)

        if not root_id:
            # 没有根部门无法继续
            print_report(report)
            return report.exit_code

        # 2. NULL department_id
        print("\n[2/6] NULL department_id 检查...")
        all_tables = A_TABLES + B_TABLES
        for table in all_tables:
            await check_null_dept(conn, table, report)

        # 3. 挂 root 检查
        print("\n[3/6] department_id 挂 root 检查...")
        # A 类表
        for table in A_TABLES:
            await check_root_dept(conn, table, root_id, report, is_a_class=True)

        # B 类敏感表
        for table in SENSITIVE_TABLES:
            await check_root_dept(conn, table, root_id, report, is_sensitive=True)

        # B 类非敏感表
        non_sensitive_b = [t for t in B_TABLES if t not in SENSITIVE_TABLES]
        for table in non_sensitive_b:
            await check_root_dept(conn, table, root_id, report)

        # 4. 孤儿引用
        print("\n[4/6] 孤儿引用检查...")
        for table in all_tables:
            await check_orphan_dept(conn, table, report)

        # 5. 部门树完整性
        print("\n[5/6] 部门树完整性检查...")
        await check_dept_tree(conn, report, root_id)

        # 6. system 部门成员
        if system_id:
            print("\n[6/6] system 部门成员检查...")
            await check_system_members(conn, report, system_id)

    await engine.dispose()

    # 打印报告
    print_report(report)
    return report.exit_code


def print_report(report: Report) -> None:
    """打印校验报告。"""
    print("\n" + "=" * 70)
    print("  校验报告")
    print("=" * 70)

    # 按严重程度分组
    fail_results = [r for r in report.results if r.severity == "FAIL"]
    warn_results = [r for r in report.results if r.severity == "WARN"]
    pass_results = [r for r in report.results if r.severity == "PASS"]
    info_results = [r for r in report.results if r.severity == "INFO"]

    # FAIL
    if fail_results:
        print(f"\n{'─' * 50}")
        print(f"FAIL ({len(fail_results)}) — 必须修复")
        print(f"{'─' * 50}")
        for r in fail_results:
            print(f"  [{r.name}]")
            if r.table:
                print(f"    表: {r.table}")
            if r.count is not None:
                print(f"    数量: {r.count}")
            print(f"    {r.detail}")
            print()

    # WARN
    if warn_results:
        print(f"{'─' * 50}")
        print(f"WARN ({len(warn_results)}) — 需人工审视")
        print(f"{'─' * 50}")
        for r in warn_results:
            print(f"  [{r.name}]")
            if r.table:
                print(f"    表: {r.table}")
            if r.count is not None:
                print(f"    数量: {r.count}")
            print(f"    {r.detail}")
            print()

    # PASS 汇总
    print(f"{'─' * 50}")
    print(f"PASS: {len(pass_results)}  |  WARN: {len(warn_results)}  |  FAIL: {len(fail_results)}  |  INFO: {len(info_results)}")
    print(f"{'─' * 50}")

    # 结论
    print()
    if report.has_fail:
        print("结论: 存在必须修复的问题（FAIL），请按上述报告处理后再上线。")
    elif report.has_warn:
        print("结论: 无致命问题，但存在需人工审视的告警（WARN），请确认后决定是否上线。")
    else:
        print("结论: 全部校验通过，department_id 租户隔离数据完整。")
    print("=" * 70)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
