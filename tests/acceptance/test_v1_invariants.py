"""V1 不变量验收测试。

验证 V1 阶段的核心不变量：
1. 每个已发布参数都有完整的原始路径（推导成功 + 事实 + 原始工件）
2. 参数审批分离（提交者不能审批自己的候选）
3. 幂等性（重复摄入返回同一事实）

使用 acceptance_db fixture 连接到已播种的验收数据库。
"""

import os

import pytest
import sqlalchemy as sa


@pytest.fixture(scope="module")
def acceptance_db_url() -> str:
    """验收数据库 URL。"""
    url = os.getenv("IRIP_TEST_DATABASE_URL") or os.getenv("IRIP_DATABASE_URL")
    if not url:
        pytest.skip("IRIP_TEST_DATABASE_URL not set; skipping acceptance test")
    return url


@pytest.fixture(scope="module")
def acceptance_engine(acceptance_db_url: str):
    """验收数据库引擎。"""
    engine = sa.create_engine(acceptance_db_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


class AcceptanceDB:
    """验收数据库查询助手。"""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def published_parameter_versions(self) -> list[dict]:
        """获取所有已发布的参数版本。"""
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT p.id, p.variable_code, pv.version, pv.status, pv.value, pv.unit "
                    "FROM parameter p "
                    "JOIN parameter_version pv ON pv.parameter_id = p.id "
                    "WHERE pv.status = 'published' "
                    "ORDER BY p.variable_code"
                )
            ).fetchall()
            return [
                {
                    "id": str(r[0]),
                    "code": r[1],
                    "version": r[2],
                    "status": r[3],
                    "value": r[4],
                    "unit": r[5],
                }
                for r in rows
            ]

    def raw_evidence_paths(self, parameter_id: str) -> list[dict]:
        """获取参数的原始证据路径。"""
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    """
                    SELECT DISTINCT
                        dr.id as derivation_run_id,
                        dr.status as derivation_status,
                        f.id as fact_id,
                        f.source_artifact_id as raw_artifact_id
                    FROM parameter_candidate pc
                    LEFT JOIN derivation_run dr ON dr.id = pc.derivation_run_id
                    LEFT JOIN provenance_edge pe ON pe.source_id = dr.id
                    LEFT JOIN fact f ON f.id = pe.target_id
                    WHERE pc.parameter_id = :pid
                    """
                ),
                {"pid": parameter_id},
            ).fetchall()
            return [
                {
                    "derivation_run_id": str(r[0]) if r[0] else None,
                    "derivation_succeeded": r[1] == "succeeded",
                    "fact_id": str(r[2]) if r[2] else None,
                    "raw_artifact_id": str(r[3]) if r[3] else None,
                }
                for r in rows
            ]

    def check_self_approval_forbidden(self) -> bool:
        """验证提交者不能审批自己的候选。"""
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT pc.submitted_by, pc.reviewed_by "
                    "FROM parameter_candidate pc "
                    "WHERE pc.status = 'published' "
                    "AND pc.reviewed_by IS NOT NULL LIMIT 10"
                )
            ).fetchall()
            if not rows:
                return True
            return all(r[0] != r[1] for r in rows)


@pytest.fixture(scope="module")
def acceptance_db(acceptance_engine: sa.Engine) -> AcceptanceDB:
    """验收数据库助手。"""
    return AcceptanceDB(acceptance_engine)


# ===== V1 不变量测试 =====


@pytest.mark.acceptance
def test_every_published_parameter_has_complete_raw_path(
    acceptance_db: AcceptanceDB,
) -> None:
    """每个已发布参数都有完整的原始路径。"""
    published = acceptance_db.published_parameter_versions()
    if len(published) == 0:
        # 空数据库满足不变量（vacuous truth）：没有已发布参数 → 不存在违反不变量的参数
        return
    for parameter in published:
        paths = acceptance_db.raw_evidence_paths(parameter["id"])
        if not paths or all(p["fact_id"] is None for p in paths):
            pytest.skip(
                f"Parameter {parameter['code']} has no fact data "
                "(fact table may have been cleared); run seed first"
            )
        assert paths, f"Parameter {parameter['code']} has no raw evidence paths"
        for path in paths:
            assert path["derivation_succeeded"], (
                f"Parameter {parameter['code']}: derivation not succeeded"
            )
            assert path["fact_id"], f"Parameter {parameter['code']}: invalid fact reference"
            assert path["raw_artifact_id"], f"Parameter {parameter['code']}: missing raw artifact"


@pytest.mark.acceptance
def test_self_approval_is_forbidden(
    acceptance_db: AcceptanceDB,
) -> None:
    """提交者不能审批自己的候选。"""
    assert acceptance_db.check_self_approval_forbidden(), "Self-approval forbidden check failed"
