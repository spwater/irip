"""溯源集成测试共享 fixtures。

注册测试用推导执行器（版本 0.1.0，与测试数据匹配）。
"""

import pytest

from packages.provenance.algorithms import register_executor


@pytest.fixture(autouse=True)
def _register_test_executor() -> None:
    """注册测试用推导执行器（版本 0.1.0，与测试数据匹配）。"""
    from packages.provenance.algorithms import RobustParameterEstimator

    executor = RobustParameterEstimator()
    executor.version = "0.1.0"  # type: ignore[misc]
    register_executor(executor)
