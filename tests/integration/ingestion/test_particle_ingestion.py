"""粒子粒度数据端到端摄入集成测试（IRIP Task 16，标准层空表清理后精简版）。

生成 60 个实验 + 2 个重复文件的 fixture 数据，
运行 IngestionPipeline.ingest_batch，
验证事实创建与去重结果。

原测试设置完整的 L1 标准链与映射配置（variable / variable_version /
fact_template / fact_template_version / mapping_profile /
mapping_profile_version），这些表已在 migration 0057 中 DROP。
ingest_file / ingest_batch 签名简化为 (file_path, object_id)，
不再接受 mapping_profile_version_id / template_version_id 参数。
质量评估统一返回空通过结果，不再验证 blocked/warning 计数。
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.ids import new_id
from packages.connectors.ingestion_service import IngestionPipeline
from packages.facts.quality import QualityEngine
from packages.facts.service import FactService
from packages.standards.objects import IndustrialObject


@pytest.fixture
async def particle_ingestion_setup(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine,
) -> dict:
    """创建粒子粒度摄入所需的工业对象。

    原函数创建完整的 L1 标准链与映射配置，这些表已在 migration 0057
    中 DROP。当前仅创建工业对象，供 ingest_file 引用。

    返回所有创建实体的 ID，供测试使用。
    测试后自动清理。
    """
    org_id = test_user.department_id  # type: ignore[attr-defined]
    actor_id = test_user.user_id  # type: ignore[attr-defined]

    object_id = new_id()
    now = datetime.now(UTC)

    async with session_scope(async_session_factory) as session:
        # 创建工业对象
        obj = IndustrialObject(
            id=object_id,
            department_id=org_id,
            object_type="lab",
            code=f"ps_obj_{object_id.hex[:8]}",
            display_name="粒度实验室",
            status="active",
            created_at=now,
            updated_at=now,
            lock_version=0,
        )
        session.add(obj)
        await session.flush()

    yield {
        "object_id": object_id,
        "department_id": org_id,
        "actor_id": actor_id,
    }

    # 清理
    with sync_engine.connect() as conn:
        # 清理事实
        conn.execute(
            sa.text(
                "DELETE FROM fact_data_index WHERE fact_id IN ("
                "SELECT id FROM fact WHERE department_id = :oid)"
            ),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM fact WHERE department_id = :oid"),
            {"oid": org_id},
        )
        # 清理对象
        conn.execute(
            sa.text("DELETE FROM industrial_object WHERE department_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


@pytest.fixture
async def particle_pipeline(
    async_session_factory: async_sessionmaker[AsyncSession],
    particle_ingestion_setup: dict,
) -> IngestionPipeline:
    """创建摄入管线实例。"""
    fact_service = FactService(
        session_factory=async_session_factory,
        department_id=particle_ingestion_setup["department_id"],
        actor_id=particle_ingestion_setup["actor_id"],
    )
    quality_engine = QualityEngine()
    return IngestionPipeline(
        session_factory=async_session_factory,
        fact_service=fact_service,
        quality_engine=quality_engine,
        department_id=particle_ingestion_setup["department_id"],
        actor_id=particle_ingestion_setup["actor_id"],
    )


def _collect_xlsx_files(fixture_dir: Path) -> tuple[Path, ...]:
    """收集 fixture 目录中的所有 .xlsx 文件（排除 manifest 和 ground_truth）。

    Args:
        fixture_dir: fixture 数据目录。

    Returns:
        tuple[Path, ...]: XLSX 文件路径元组（按文件名排序）。
    """
    files = sorted(f for f in fixture_dir.iterdir() if f.suffix == ".xlsx")
    return tuple(files)


def _load_ground_truth(fixture_dir: Path) -> dict:
    """加载 ground_truth.json。

    Args:
        fixture_dir: fixture 数据目录。

    Returns:
        dict: ground truth 字典。
    """
    with open(fixture_dir / "ground_truth.json", encoding="utf-8") as fh:
        return json.load(fh)


class TestParticleIngestion:
    """粒子粒度数据端到端摄入集成测试。"""

    @pytest.mark.asyncio
    async def test_ingest_single_file(
        self,
        particle_pipeline: IngestionPipeline,
        particle_ingestion_setup: dict,
        tmp_path: Path,
    ) -> None:
        """单文件摄入 → 创建一个事实。"""
        from examples.particle_size.generate import generate_particle_fixture

        fixture_dir = tmp_path / "fixture"
        generate_particle_fixture(fixture_dir, seed=20260715)

        xlsx_files = _collect_xlsx_files(fixture_dir)
        assert len(xlsx_files) >= 62

        # 摄入第一个文件
        result = await particle_pipeline.ingest_file(
            file_path=xlsx_files[0],
            object_id=particle_ingestion_setup["object_id"],
        )

        assert result.error is None
        assert len(result.fact_ids) == 1
        assert result.deduplicated is False

    @pytest.mark.asyncio
    async def test_duplicate_file_returns_existing(
        self,
        particle_pipeline: IngestionPipeline,
        particle_ingestion_setup: dict,
        tmp_path: Path,
    ) -> None:
        """两个相同文件 → 第二个返回已有事实（deduplicated=True）。"""
        from examples.particle_size.generate import generate_particle_fixture

        fixture_dir = tmp_path / "fixture"
        generate_particle_fixture(fixture_dir, seed=20260715)

        xlsx_files = _collect_xlsx_files(fixture_dir)

        # 摄入第一个文件
        result1 = await particle_pipeline.ingest_file(
            file_path=xlsx_files[0],
            object_id=particle_ingestion_setup["object_id"],
        )
        assert result1.deduplicated is False
        assert len(result1.fact_ids) == 1

        # 找到第一个文件的重复（EXP-001-DUP1）
        dup_file = fixture_dir / "EXP-001-DUP1.xlsx"
        assert dup_file.exists()

        result2 = await particle_pipeline.ingest_file(
            file_path=dup_file,
            object_id=particle_ingestion_setup["object_id"],
        )
        assert result2.deduplicated is True
        assert result2.fact_ids == result1.fact_ids

    @pytest.mark.asyncio
    async def test_ingest_batch_full(
        self,
        particle_pipeline: IngestionPipeline,
        particle_ingestion_setup: dict,
        tmp_path: Path,
    ) -> None:
        """批量摄入 62 个文件 → 60 个事实（2 去重）。"""
        from examples.particle_size.generate import generate_particle_fixture

        fixture_dir = tmp_path / "fixture"
        generate_particle_fixture(fixture_dir, seed=20260715)

        xlsx_files = _collect_xlsx_files(fixture_dir)
        assert len(xlsx_files) == 62

        results = await particle_pipeline.ingest_batch(
            file_paths=xlsx_files,
            object_id=particle_ingestion_setup["object_id"],
        )

        # 62 个结果
        assert len(results) == 62

        # 2 个去重
        deduplicated = [r for r in results if r.deduplicated]
        assert len(deduplicated) == 2

        # 60 个非去重（新创建的事实）
        non_dedup = [r for r in results if not r.deduplicated]
        assert len(non_dedup) == 60

        # 无摄入错误
        errors = [r for r in non_dedup if r.error is not None]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_deduplicated_results_have_same_fact_ids(
        self,
        particle_pipeline: IngestionPipeline,
        particle_ingestion_setup: dict,
        tmp_path: Path,
    ) -> None:
        """去重结果的 fact_ids 与原始文件的 fact_ids 相同。"""
        from examples.particle_size.generate import generate_particle_fixture

        fixture_dir = tmp_path / "fixture"
        generate_particle_fixture(fixture_dir, seed=20260715)

        # 摄入 EXP-001 和 EXP-001-DUP1
        result_original = await particle_pipeline.ingest_file(
            file_path=fixture_dir / "EXP-001.xlsx",
            object_id=particle_ingestion_setup["object_id"],
        )
        result_dup = await particle_pipeline.ingest_file(
            file_path=fixture_dir / "EXP-001-DUP1.xlsx",
            object_id=particle_ingestion_setup["object_id"],
        )

        assert result_original.deduplicated is False
        assert result_dup.deduplicated is True
        assert result_original.fact_ids == result_dup.fact_ids

        # 同理 EXP-031 和 EXP-031-DUP1
        result_original2 = await particle_pipeline.ingest_file(
            file_path=fixture_dir / "EXP-031.xlsx",
            object_id=particle_ingestion_setup["object_id"],
        )
        result_dup2 = await particle_pipeline.ingest_file(
            file_path=fixture_dir / "EXP-031-DUP1.xlsx",
            object_id=particle_ingestion_setup["object_id"],
        )

        assert result_original2.deduplicated is False
        assert result_dup2.deduplicated is True
        assert result_original2.fact_ids == result_dup2.fact_ids
