#!/usr/bin/env python3
"""IRIP 源码统计自动生成脚本（F-22: 文档自动生成）。

从源码自动统计：
- 组件数（packages/components/builtin/ 下的组件类）
- AI 工具数（packages/ai/tools.py 中注册的工具）
- 路由数（apps/api/routers/ 下的 APIRouter 端点）
- 迁移 head（alembic 最新的迁移版本）

用法：
    python scripts/generate-stats.py [--output <file>]

输出 JSON 格式统计信息，可用于验收报告自动生成。
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

#: 项目根目录。
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def count_builtin_components() -> int:
    """统计内置组件数。

    扫描 packages/components/builtin/ 下的 Python 文件，
    统计包含 execute 方法的类（组件约定）。
    """
    builtin_dir: Path = _PROJECT_ROOT / "packages" / "components" / "builtin"
    if not builtin_dir.exists():
        return 0

    count: int = 0
    for pyfile in builtin_dir.rglob("*.py"):
        if pyfile.name == "__init__.py":
            continue
        try:
            tree: ast.AST = ast.parse(pyfile.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == "execute":
                        count += 1
                        break
    return count


def count_ai_tools() -> int:
    """统计 AI 工具数。

    扫描 packages/ai/tools.py 中 ToolRegistry.register 调用。
    """
    tools_file: Path = _PROJECT_ROOT / "packages" / "ai" / "tools.py"
    if not tools_file.exists():
        return 0

    try:
        content: str = tools_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return 0

    # 统计 register 调用或 @tool 装饰器
    register_count: int = len(re.findall(r"\.register\s*\(", content))
    decorator_count: int = len(re.findall(r"@tool\b", content))
    return max(register_count, decorator_count)


def count_routes() -> int:
    """统计 API 路由端点数。

    扫描 apps/api/routers/ 下的 Python 文件，
    统计 @router.get/post/put/delete/patch 装饰器。
    """
    routers_dir: Path = _PROJECT_ROOT / "apps" / "api" / "routers"
    if not routers_dir.exists():
        return 0

    count: int = 0
    for pyfile in routers_dir.glob("*.py"):
        if pyfile.name == "__init__.py":
            continue
        try:
            content: str = pyfile.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # 统计路由装饰器
        count += len(re.findall(r"@\w+_router\.(get|post|put|delete|patch)\s*\(", content))
        count += len(re.findall(r"@\w+\.(get|post|put|delete|patch)\s*\(", content))
    return count


def get_migration_head() -> str:
    """获取最新的 alembic 迁移版本。
    Returns:
        str: 迁移 head revision，获取失败时返回 "unknown"。
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config: Config = Config()
        config.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
        script_dir: ScriptDirectory = ScriptDirectory.from_config(config)
        heads: list[str] = [
            rev.revision for rev in script_dir.get_revisions("heads")
        ]
        return ", ".join(heads) if heads else "none"
    except Exception:
        return "unknown"


def generate_stats() -> dict:
    """生成全部统计信息。
    Returns:
        dict: 包含组件数、AI 工具数、路由数、迁移 head 的字典。
    """
    return {
        "builtin_components": count_builtin_components(),
        "ai_tools": count_ai_tools(),
        "api_routes": count_routes(),
        "migration_head": get_migration_head(),
    }


def main() -> int:
    """主入口。"""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="IRIP 源码统计自动生成（F-22）"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="输出文件路径（默认 stdout）",
    )
    args: argparse.Namespace = parser.parse_args()

    stats: dict = generate_stats()
    output: str = json.dumps(stats, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"统计信息已写入 {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
