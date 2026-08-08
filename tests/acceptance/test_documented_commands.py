"""V3-T05 验收测试：验证文档命令和文档完整性。

本测试验证：
1. README.md 中所有文档命令存在且可解析。
2. 文档内部链接可解析（指向的文件存在）。
3. 必需指南文档存在（install/upgrade/backup/restore/particle/rom/mapping/adapter）。

不执行命令本身（避免依赖 Docker/网络），仅验证命令文本和文档链接的完整性。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _read_file(relative_path: str) -> str:
    """读取项目内文件，返回全文文本。"""
    file_path = PROJECT_ROOT / relative_path
    if not file_path.exists():
        pytest.fail(f"文件不存在: {relative_path}")
    return file_path.read_text(encoding="utf-8")


def _extract_bash_commands(markdown: str) -> list[str]:
    """从 Markdown 文本中提取 bash 代码块中的命令行。"""
    commands: list[str] = []
    # 匹配 ```bash ... ``` 代码块
    bash_blocks = re.findall(r"```bash\n(.*?)```", markdown, re.DOTALL)
    for block in bash_blocks:
        for line in block.strip().splitlines():
            line = line.strip()
            # 跳过空行和注释行
            if not line or line.startswith("#"):
                continue
            commands.append(line)
    return commands


def _extract_markdown_links(markdown: str) -> list[tuple[str, str]]:
    """从 Markdown 文本中提取 [text](path) 形式的内部链接。

    返回 (link_text, link_path) 列表，仅保留相对路径链接（跳过 http/https）。
    """
    links: list[tuple[str, str]] = []
    # 匹配 [text](path) — 仅非 http/https 的相对路径
    all_links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", markdown)
    for text, path in all_links:
        if path.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append((text, path))
    return links


# ---------------------------------------------------------------------------
# 必需文档存在性检查
# ---------------------------------------------------------------------------

REQUIRED_DOCS = [
    "README.md",
    "docs/architecture/system-overview.md",
    "docs/architecture/domain-invariants.md",
    "docs/user-guide/particle-size.md",
    "docs/user-guide/grate-cooler-rom.md",
    "docs/data-onboarding/mapping-profile.md",
    "docs/model-onboarding/model-adapter.md",
    "docs/operations/install-upgrade.md",
    "docs/operations/monitoring.md",
    "docs/operations/backup-restore.md",
    "docs/acceptance/final-release.md",
]


@pytest.mark.acceptance
@pytest.mark.parametrize("doc_path", REQUIRED_DOCS, ids=REQUIRED_DOCS)
def test_required_document_exists(doc_path: str) -> None:
    """验证所有必需文档存在。"""
    file_path = PROJECT_ROOT / doc_path
    assert file_path.exists(), f"必需文档缺失: {doc_path}"
    assert file_path.stat().st_size > 0, f"文档为空: {doc_path}"


# ---------------------------------------------------------------------------
# 发布门脚本存在性检查
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_release_gate_script_exists() -> None:
    """验证发布门脚本存在且可执行。"""
    script_path = PROJECT_ROOT / "scripts" / "release-gate.sh"
    assert script_path.exists(), "发布门脚本缺失: scripts/release-gate.sh"
    content = script_path.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash"), "发布门脚本应以 shebang 开头"
    assert "set -euo pipefail" in content, "发布门脚本应启用严格模式"


# ---------------------------------------------------------------------------
# README 命令验证
# ---------------------------------------------------------------------------

README_PATH = "README.md"


@pytest.mark.acceptance
def test_readme_contains_install_commands() -> None:
    """验证 README 包含安装命令。"""
    content = _read_file(README_PATH)
    assert "docker compose up" in content, "README 缺少 docker compose up 命令"
    assert "docker compose run --rm bootstrap" in content, "README 缺少 bootstrap 命令"
    assert ".env" in content, "README 缺少环境变量配置说明"


@pytest.mark.acceptance
def test_readme_contains_service_urls() -> None:
    """验证 README 包含服务 URL。"""
    content = _read_file(README_PATH)
    assert "8000" in content, "README 缺少 API 端口 (8000)"
    assert "5173" in content, "README 缺少 Web 开发端口 (5173)"


@pytest.mark.acceptance
def test_readme_contains_stop_start_commands() -> None:
    """验证 README 包含停止/启动命令。"""
    content = _read_file(README_PATH)
    assert "docker compose stop" in content, "README 缺少 docker compose stop"
    assert "docker compose start" in content, "README 缺少 docker compose start"
    assert "docker compose down" in content, "README 缺少 docker compose down"


@pytest.mark.acceptance
def test_readme_contains_test_commands() -> None:
    """验证 README 包含测试命令。"""
    content = _read_file(README_PATH)
    assert "make lint" in content or "ruff check" in content, "README 缺少 lint 命令"
    assert "pytest" in content, "README 缺少 pytest 命令"
    assert "release-gate.sh" in content, "README 缺少发布门脚本引用"


@pytest.mark.acceptance
def test_readme_contains_prerequisites() -> None:
    """验证 README 包含前提条件。"""
    content = _read_file(README_PATH)
    assert "Python" in content and "3.12" in content, "README 缺少 Python 版本要求"
    assert "Node" in content and "22" in content, "README 缺少 Node 版本要求"
    assert "PostgreSQL" in content, "README 缺少 PostgreSQL 要求"
    assert "Redis" in content, "README 缺少 Redis 要求"
    assert "MinIO" in content, "README 缺少 MinIO 要求"


@pytest.mark.acceptance
def test_readme_contains_bootstrap_credentials() -> None:
    """验证 README 包含 Bootstrap 凭据说明。"""
    content = _read_file(README_PATH)
    assert "admin@irip.local" in content, "README 缺少管理员邮箱"
    assert "Admin-IRIP-2026" in content, "README 缺少管理员密码"


@pytest.mark.acceptance
def test_readme_contains_sample_data_commands() -> None:
    """验证 README 包含示例数据加载命令。"""
    content = _read_file(README_PATH)
    assert "examples" in content, "README 缺少示例数据说明"
    assert "particle-size" in content or "grate-cooler-rom" in content, "README 缺少示例场景引用"


# ---------------------------------------------------------------------------
# README 内部链接验证
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_readme_internal_links_resolvable() -> None:
    """验证 README 中所有内部链接指向的文件存在。"""
    content = _read_file(README_PATH)
    links = _extract_markdown_links(content)
    assert len(links) > 0, "README 应包含至少一个内部文档链接"

    broken: list[str] = []
    for text, path in links:
        # 去除可能的锚点 (#xxx)
        clean_path = path.split("#")[0]
        if not clean_path:
            continue
        target = PROJECT_ROOT / clean_path
        if not target.exists():
            broken.append(f"[{text}]({path})")

    assert not broken, f"README 中以下内部链接无法解析: {broken}"


# ---------------------------------------------------------------------------
# 各文档内部链接验证
# ---------------------------------------------------------------------------

DOCS_WITH_LINKS = [
    "docs/architecture/system-overview.md",
    "docs/architecture/domain-invariants.md",
    "docs/user-guide/particle-size.md",
    "docs/user-guide/grate-cooler-rom.md",
    "docs/data-onboarding/mapping-profile.md",
    "docs/model-onboarding/model-adapter.md",
    "docs/operations/install-upgrade.md",
    "docs/operations/monitoring.md",
    "docs/acceptance/final-release.md",
]


@pytest.mark.acceptance
@pytest.mark.parametrize("doc_path", DOCS_WITH_LINKS, ids=DOCS_WITH_LINKS)
def test_doc_internal_links_resolvable(doc_path: str) -> None:
    """验证各文档中所有内部链接指向的文件存在。"""
    content = _read_file(doc_path)
    links = _extract_markdown_links(content)

    broken: list[str] = []
    for text, path in links:
        clean_path = path.split("#")[0]
        if not clean_path:
            continue
        # 处理相对于 docs/ 子目录的路径
        if clean_path.startswith("docs/"):
            target = PROJECT_ROOT / clean_path
        else:
            # 相对于当前文档所在目录
            doc_dir = (PROJECT_ROOT / doc_path).parent
            target = doc_dir / clean_path
        if not target.exists():
            broken.append(f"[{text}]({path}) — file not found at {target}")

    assert not broken, f"{doc_path} 中以下内部链接无法解析: {broken}"


# ---------------------------------------------------------------------------
# 必需指南内容验证
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_install_upgrade_doc_contains_docker_commands() -> None:
    """验证安装升级指南包含 Docker Compose 命令。"""
    content = _read_file("docs/operations/install-upgrade.md")
    assert "docker compose up" in content, "安装指南缺少 docker compose up"
    assert "alembic upgrade head" in content, "安装指南缺少数据库迁移命令"
    assert "bootstrap" in content, "安装指南缺少 bootstrap 说明"


@pytest.mark.acceptance
def test_install_upgrade_doc_contains_rollback() -> None:
    """验证安装升级指南包含回滚程序。"""
    content = _read_file("docs/operations/install-upgrade.md")
    assert "回滚" in content or "rollback" in content.lower(), "安装指南缺少回滚程序说明"


@pytest.mark.acceptance
def test_backup_restore_doc_exists_and_has_content() -> None:
    """验证备份恢复文档存在且包含关键内容。"""
    content = _read_file("docs/operations/backup-restore.md")
    assert "备份" in content, "备份恢复文档缺少备份说明"
    assert "恢复" in content, "备份恢复文档缺少恢复说明"
    assert "SHA-256" in content or "sha256" in content.lower(), "备份恢复文档缺少完整性校验说明"


@pytest.mark.acceptance
def test_particle_size_doc_contains_full_workflow() -> None:
    """验证粒度分析指南包含完整工作流。"""
    content = _read_file("docs/user-guide/particle-size.md")
    assert "标准变量" in content, "粒度指南缺少标准变量说明"
    assert "事实" in content, "粒度指南缺少事实说明"
    assert "证据集" in content, "粒度指南缺少证据集说明"
    assert "推导" in content, "粒度指南缺少推导说明"
    assert "参数" in content and "审批" in content, "粒度指南缺少参数审批说明"


@pytest.mark.acceptance
def test_grate_cooler_rom_doc_contains_full_workflow() -> None:
    """验证篦冷机 ROM 指南包含完整工作流。"""
    content = _read_file("docs/user-guide/grate-cooler-rom.md")
    assert "训练" in content, "ROM 指南缺少训练说明"
    assert "评估" in content or "验证" in content, "ROM 指南缺少评估说明"
    assert "发布" in content, "ROM 指南缺少发布说明"
    assert "预测" in content, "ROM 指南缺少预测说明"
    assert "适用域" in content, "ROM 指南缺少适用域说明"
    assert "回滚" in content, "ROM 指南缺少回滚说明"


@pytest.mark.acceptance
def test_mapping_profile_doc_contains_field_mapping() -> None:
    """验证数据上线指南包含字段映射说明。"""
    content = _read_file("docs/data-onboarding/mapping-profile.md")
    assert "MappingProfile" in content, "数据上线指南缺少 MappingProfile 说明"
    assert "字段映射" in content, "数据上线指南缺少字段映射说明"
    assert "连接器" in content, "数据上线指南缺少连接器说明"


@pytest.mark.acceptance
def test_model_adapter_doc_contains_protocol() -> None:
    """验证模型上线指南包含 ModelAdapter 协议说明。"""
    content = _read_file("docs/model-onboarding/model-adapter.md")
    assert "ModelAdapter" in content, "模型上线指南缺少 ModelAdapter 协议"
    assert "契约" in content or "contract" in content.lower(), "模型上线指南缺少模型契约说明"
    assert "命令行" in content or "CLI" in content, "模型上线指南缺少命令行适配器说明"


# ---------------------------------------------------------------------------
# 发布门脚本内容验证
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_release_gate_contains_all_steps() -> None:
    """验证发布门脚本包含所有必需步骤。"""
    content = _read_file("scripts/release-gate.sh")
    required_steps = [
        "ruff check",
        "mypy",
        "pytest",
        "pnpm",  # lint/test/build
        "docker compose up",
    ]
    for step in required_steps:
        assert step in content, f"发布门脚本缺少步骤: {step}"


@pytest.mark.acceptance
def test_release_gate_contains_cleanup() -> None:
    """验证发布门脚本包含清理步骤。"""
    content = _read_file("scripts/release-gate.sh")
    assert "docker compose down" in content, "发布门脚本缺少清理步骤"
    assert "trap" in content, "发布门脚本缺少 trap 清理机制"


@pytest.mark.acceptance
def test_release_gate_contains_compose_project_name() -> None:
    """验证发布门脚本使用隔离的 Compose 项目名。"""
    content = _read_file("scripts/release-gate.sh")
    assert "COMPOSE_PROJECT_NAME" in content, "发布门脚本应使用隔离的 COMPOSE_PROJECT_NAME"
    assert "irip-release-gate" in content, "发布门脚本应使用 irip-release-gate 项目名"


# ---------------------------------------------------------------------------
# 最终验收文档验证
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_final_release_doc_contains_version() -> None:
    """验证最终验收文档包含版本号。"""
    content = _read_file("docs/acceptance/final-release.md")
    assert "0.8.0" in content, "最终验收文档缺少版本号"


@pytest.mark.acceptance
def test_final_release_doc_contains_known_limitations() -> None:
    """验证最终验收文档包含已知限制。"""
    content = _read_file("docs/acceptance/final-release.md")
    assert "已知限制" in content, "最终验收文档缺少已知限制"


@pytest.mark.acceptance
def test_final_release_doc_contains_feature_checklist() -> None:
    """验证最终验收文档包含功能清单。"""
    content = _read_file("docs/acceptance/final-release.md")
    assert "V0" in content, "最终验收文档缺少 V0 功能清单"
    assert "V1" in content, "最终验收文档缺少 V1 功能清单"
    assert "V2" in content, "最终验收文档缺少 V2 功能清单"
    assert "V3" in content, "最终验收文档缺少 V3 功能清单"
