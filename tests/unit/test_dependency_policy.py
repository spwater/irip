"""依赖安全策略测试。

覆盖 P1 供应链修复（Task 1）的不变量：
- ``uv.lock`` 中 sqlparse 版本 >= 0.6.0（修复已知漏洞的下限约束）；
- ``apps/web/package.json`` 中 echarts 版本 >= 6.1.0（修复前端已知漏洞）。

这些测试作为依赖升级的门禁：升级前应失败，升级后应通过。
"""

import json
import re
from pathlib import Path

from packaging.version import Version


def test_sqlparse_lock_is_patched() -> None:
    """uv.lock 中 sqlparse 版本应 >= 0.6.0。"""
    lock = Path("uv.lock").read_text()
    match = re.search(r'name = "sqlparse"\nversion = "([^"]+)"', lock)
    assert match and Version(match.group(1)) >= Version("0.6.0")


def test_echarts_requirement_is_patched() -> None:
    """apps/web/package.json 中 echarts 版本应 >= 6.1.0。"""
    package = json.loads(Path("apps/web/package.json").read_text())
    assert Version(package["dependencies"]["echarts"].lstrip("^~>=<")) >= Version("6.1.0")
