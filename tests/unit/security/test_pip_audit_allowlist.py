"""pip_audit_with_allowlist 辅助脚本单元测试。

验证 allowlist → ``--ignore-vuln`` 参数的提取与映射契约（B5）：
从 ``security/vulnerability-allowlist.yaml`` 提取 vuln id，转成
``--ignore-vuln <id>`` 对，取代 CI 中的硬编码豁免。
"""

import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "security"),
)
from pip_audit_with_allowlist import build_ignore_args, extract_vuln_ids  # noqa: E402


def test_extract_vuln_ids_from_real_allowlist() -> None:
    """从项目真实 allowlist 提取 PYSEC-2026-1845。"""
    from pathlib import Path

    allowlist = Path(__file__).parents[3] / "security" / "vulnerability-allowlist.yaml"
    ids = extract_vuln_ids(allowlist)
    assert ids == ["PYSEC-2026-1845"]


def test_build_ignore_args_maps_ids_to_flag_pairs() -> None:
    """多个 id 映射为逐一 ``--ignore-vuln <id>`` 对。"""
    args = build_ignore_args(["PYSEC-A", "PYSEC-B"])
    assert args == ["--ignore-vuln", "PYSEC-A", "--ignore-vuln", "PYSEC-B"]


def test_build_ignore_args_empty() -> None:
    """空 id 列表不产生任何参数。"""
    assert build_ignore_args([]) == []
