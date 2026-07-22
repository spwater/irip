"""溯源与推导单元测试 fixtures。

从 tests/unit/facts/conftest.py 导入 fact_service 和 fact_setup fixtures，
供 provenance 单元测试复用已有的 L1→L2 测试数据搭建。
"""

# 导入 fact_service 和 fact_setup fixtures（pytest fixture 跨目录共享）
from tests.unit.facts.conftest import (  # noqa: F401
    fact_service,
    fact_setup,
)
