#!/usr/bin/env python3
"""IRIP 验收报告自动生成脚本（F-22 / H-10）。

基于源码统计和 CI 检查结果生成验收报告 Markdown。
与 generate-stats.py 配合使用，针对 commit SHA 生成可追溯的验收报告。

H-10 改动：
- 删除硬编码 PASS；
- 从 JUnit XML 和 coverage 报告中读取真实结果；
- 运行 Ruff/Mypy 等检查获取实际状态；
- 缺失证据时标记 UNKNOWN/FAIL。

用法：
  python scripts/generate-acceptance.py --commit <sha> [--output acceptance.md]
"""

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

#: 项目根目录。
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]


def get_git_commit() -> str:
    """获取当前 git commit SHA。

    Returns:
        str: 短 commit SHA，git 不可用时返回 "unknown"。
    """
    try:
        result: subprocess.CompletedProcess = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def get_git_branch() -> str:
    """获取当前 git 分支名。

    Returns:
        str: 分支名，git 不可用时返回 "unknown"。
    """
    try:
        result: subprocess.CompletedProcess = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _run_check(cmd: list[str], cwd: Path | None = None) -> str:
    """运行命令并返回 PASS/FAIL/UNKNOWN。

    Args:
        cmd: 命令列表。
        cwd: 工作目录。

    Returns:
        str: "PASS" / "FAIL" / "UNKNOWN"。
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or PROJECT_ROOT,
            timeout=300,
        )
        return "PASS" if result.returncode == 0 else "FAIL"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "UNKNOWN"


def _read_junit_results(xml_path: Path) -> str:
    """从 JUnit XML 读取测试结果。

    Args:
        xml_path: JUnit XML 文件路径。

    Returns:
        str: "PASS" / "FAIL" / "UNKNOWN"。
    """
    if not xml_path.exists():
        return "UNKNOWN"
    try:
        tree: ET.ElementTree = ET.parse(xml_path)
        root: ET.Element = tree.getroot()
        failures: int = int(root.attrib.get("failures", 0))
        errors: int = int(root.attrib.get("errors", 0))
        if failures > 0 or errors > 0:
            return "FAIL"
        return "PASS"
    except (ET.ParseError, ValueError, OSError):
        return "UNKNOWN"


def _read_coverage(cov_path: Path) -> str:
    """从 coverage XML 读取覆盖率状态。

    Args:
        cov_path: coverage XML 文件路径。

    Returns:
        str: "PASS (XX%)" / "FAIL (XX%)" / "UNKNOWN"。
    """
    if not cov_path.exists():
        return "UNKNOWN"
    try:
        tree: ET.ElementTree = ET.parse(cov_path)
        root: ET.Element = tree.getroot()
        line_rate: str = root.attrib.get("line-rate", "0")
        pct: float = float(line_rate) * 100
        threshold: float = 40.0
        status: str = "PASS" if pct >= threshold else "FAIL"
        return f"{status} ({pct:.1f}%)"
    except (ET.ParseError, ValueError, OSError):
        return "UNKNOWN"


def _collect_quality_status() -> dict[str, str]:
    """收集所有质量门检查项的实际状态。

    从 JUnit XML、coverage 报告和实时命令执行中读取结果。
    缺失证据时标记 UNKNOWN。

    Returns:
        dict[str, str]: 检查项名称 -> 状态字符串。
    """
    results: dict[str, str] = {}

    # Ruff lint + format
    results["Ruff E/F/I"] = _run_check(["ruff", "check", "apps", "packages", "tests"])
    ruff_fmt: str = _run_check(["ruff", "format", "--check", "apps", "packages", "tests"])
    if results["Ruff E/F/I"] == "PASS" and ruff_fmt == "PASS":
        results["Ruff Format"] = "PASS"
    elif results["Ruff E/F/I"] == "FAIL" or ruff_fmt == "FAIL":
        results["Ruff Format"] = "FAIL"
    else:
        results["Ruff Format"] = "UNKNOWN"

    # Mypy type check
    results["Mypy Type Check"] = _run_check(["mypy", "packages", "apps/api"])

    # Error code exhaustiveness
    results["Error Code Exhaustiveness"] = _run_check(["python", "-c", _ERROR_CODE_CHECK_SCRIPT])

    # Docker Compose config (if docker available)
    docker_available: bool = shutil_which("docker") is not None
    if docker_available:
        results["Docker Compose Config"] = _run_check(["docker", "compose", "config", "--quiet"])
    else:
        results["Docker Compose Config"] = "UNKNOWN"

    # TypeScript tsc --noEmit (if node available)
    web_dir: Path = PROJECT_ROOT / "apps" / "web"
    if (web_dir / "package.json").exists():
        results["TypeScript tsc --noEmit"] = _run_check(
            ["pnpm", "exec", "tsc", "--noEmit"],
            cwd=web_dir,
        )
    else:
        results["TypeScript tsc --noEmit"] = "UNKNOWN"

    # JUnit XML results (if available from CI artifacts)
    artifacts_dir: Path = PROJECT_ROOT / "test-results"
    for name, filename in [
        ("Integration Tests", "integration-results.xml"),
        ("Security Tests", "security-results.xml"),
        ("Recovery Tests", "recovery-results.xml"),
    ]:
        results[name] = _read_junit_results(artifacts_dir / filename)

    # Coverage
    results["Coverage"] = _read_coverage(PROJECT_ROOT / "coverage.xml")

    return results


def shutil_which(cmd: str) -> str | None:
    """查找命令路径（shutil.which 包装）。"""
    import shutil

    return shutil.which(cmd)


#: 错误码穷尽性检查脚本。
_ERROR_CODE_CHECK_SCRIPT: str = (
    "import re, sys, pathlib\n"
    "from packages.common.error_codes import ErrorCode\n"
    "code_pattern = re.compile(r'code=[\"\\']([a-z_]+)[\"\\']')\n"
    "found_codes = set()\n"
    "for pyfile in pathlib.Path('.').rglob('*.py'):\n"
    "    if '.venv' in str(pyfile) or 'node_modules' in str(pyfile):\n"
    "        continue\n"
    "    if '__pycache__' in str(pyfile):\n"
    "        continue\n"
    "    if 'tests/' in str(pyfile) or str(pyfile).startswith('tests'):\n"
    "        continue\n"
    "    try:\n"
    "        text = pyfile.read_text(encoding='utf-8')\n"
    "    except Exception:\n"
    "        continue\n"
    "    for match in code_pattern.finditer(text):\n"
    "        start = max(0, match.start() - 200)\n"
    "        context = text[start:match.end()]\n"
    "        if 'AppError' in context:\n"
    "            found_codes.add(match.group(1))\n"
    "registered = ErrorCode.all_codes()\n"
    "unregistered = found_codes - registered\n"
    "if unregistered:\n"
    "    sys.exit(1)\n"
)


def main() -> int:
    """脚本入口：生成验收报告 Markdown。

    Returns:
        int: 退出码（0=成功）。
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="IRIP 验收报告自动生成（F-22 / H-10）"
    )
    parser.add_argument(
        "--commit",
        type=str,
        default=None,
        help="指定 commit SHA（默认从 git 获取）",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="输出文件路径（默认 stdout）",
    )
    args: argparse.Namespace = parser.parse_args()

    commit: str = args.commit or get_git_commit()
    branch: str = get_git_branch()
    timestamp: str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 调用 generate-stats.py 获取统计 JSON
    try:
        import importlib.util

        stats_module_path: Path = PROJECT_ROOT / "scripts" / "generate-stats.py"
        spec = importlib.util.spec_from_file_location("generate_stats", stats_module_path)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            stats_md: str = module.generate_markdown()
        else:
            raise ImportError("Cannot load generate-stats.py")
    except Exception:
        stats_md = "*统计生成失败，请手动运行 `python scripts/generate-stats.py`*"

    # 收集质量门实际状态（H-10: 不再硬编码 PASS）
    quality_status: dict[str, str] = _collect_quality_status()

    # 构建质量门表格
    quality_rows: list[str] = []
    for check_name, status in quality_status.items():
        quality_rows.append(f"| {check_name} | {status} |")

    lines: list[str] = [
        "# IRIP 验收报告",
        "",
        f"> **Commit**: `{commit}`",
        f"> **Branch**: `{branch}`",
        f"> **生成时间**: {timestamp}",
        "> **生成方式**: CI 自动生成（`scripts/generate-acceptance.py`）",
        "",
        "## 版本状态",
        "",
        "本项目处于 **内部 Alpha** 阶段，**不可用于生产发布**。",
        "",
        "## 能力标记说明",
        "",
        "| 标记 | 含义 |",
        "|------|------|",
        "| Proposed | 已设计未实现 |",
        "| Partial | 部分实现，功能不完整 |",
        "| Implemented | 已实现，未验收 |",
        "| Verified | 已实现且通过验收测试 |",
        "| Deprecated | 已弃用，不再维护 |",
        "",
        "## 源码统计",
        "",
        stats_md,
        "",
        "## CI 质量门状态",
        "",
        "| 检查项 | 状态 |",
        "|--------|------|",
    ]
    lines.extend(quality_rows)
    lines.extend(
        [
            "",
            "> H-10: 质量门状态从 JUnit XML / coverage / 实时命令执行中读取，",
            "> 缺失证据标记为 UNKNOWN。",
            "",
            "---",
            "",
            f"> 本报告由 CI 针对 commit `{commit}` 自动生成。",
        ]
    )

    output: str = "\n".join(lines)

    if args.output:
        output_path: Path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        print(f"验收报告已写入: {output_path}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
