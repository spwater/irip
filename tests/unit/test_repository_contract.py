"""仓库结构契约测试。

确保项目关键根目录与清单文件存在——这是所有后续任务（T02+）的地基契约。
对应实施计划 Task 1 Step 1。
"""

from pathlib import Path


def test_required_project_roots_exist() -> None:
    root = Path(__file__).parents[2]
    required = ["apps/api", "apps/worker", "apps/web", "packages/common", "tests"]
    assert all((root / path).is_dir() for path in required)
    assert (root / "pyproject.toml").is_file()
    assert (root / "apps/web/package.json").is_file()
