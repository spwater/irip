"""可信执行（阶段 2）测试。

纯逻辑测试，不需要 DB 连接或 Docker。
覆盖：ORM 实体、迁移、ContextRouter、Scheduler、DAG 拓扑排序、
Run 状态机、ModelGateway、CoverageDeclaration、模块隔离。
"""

from uuid import uuid4

import pytest

# ============================================================
# 1. ORM 实体验证
# ============================================================


class TestTrustedEntities:
    """验证 6 个新 ORM 实体。"""

    def test_entities_importable(self):
        """6 个实体可导入。"""
        from packages.research.entities_trusted import (
            ResearchAiConversation,
            ResearchAnalysisPlanVersion,
            ResearchAnalysisRun,
            ResearchAnalysisStep,
            ResearchMemoryDocument,
            ResearchRunArtifact,
        )

        assert ResearchAnalysisPlanVersion.__tablename__ == "research_analysis_plan_version"
        assert ResearchAnalysisRun.__tablename__ == "research_analysis_run"
        assert ResearchAnalysisStep.__tablename__ == "research_analysis_step"
        assert ResearchRunArtifact.__tablename__ == "research_run_artifact"
        assert ResearchAiConversation.__tablename__ == "research_ai_conversation"
        assert ResearchMemoryDocument.__tablename__ == "research_memory_document"

    def test_entities_inherit_base(self):
        """实体继承 Base。"""
        from packages.research.entities_trusted import ResearchAnalysisRun

        from packages.common.database import Base

        assert issubclass(ResearchAnalysisRun, Base)

    def test_table_names_have_research_prefix(self):
        """所有表名以 research_ 前缀。"""
        from packages.research.entities_trusted import (
            ResearchAiConversation,
            ResearchAnalysisPlanVersion,
            ResearchAnalysisRun,
            ResearchAnalysisStep,
            ResearchMemoryDocument,
            ResearchRunArtifact,
        )

        for cls in [
            ResearchAnalysisPlanVersion,
            ResearchAnalysisRun,
            ResearchAnalysisStep,
            ResearchRunArtifact,
            ResearchAiConversation,
            ResearchMemoryDocument,
        ]:
            assert cls.__tablename__.startswith("research_")

    def test_plan_version_has_dag_structure_jsonb(self):
        """PlanVersion 有 dag_structure JSONB 列。"""
        from packages.research.entities_trusted import ResearchAnalysisPlanVersion

        col = ResearchAnalysisPlanVersion.__table__.c.dag_structure
        assert col is not None
        assert not col.nullable

    def test_run_has_status_column(self):
        """Run 有 status 列。"""
        from packages.research.entities_trusted import ResearchAnalysisRun

        col = ResearchAnalysisRun.__table__.c.status
        assert col is not None


# ============================================================
# 2. 迁移 0075
# ============================================================


class TestMigration0075:
    """验证迁移 0075。"""

    def test_revision_numbers(self):
        """revision=0075, down_revision=0074。"""
        # Alembic 文件名以数字开头，Python 模块名不能直接导入
        # 直接读文件内容验证
        import pathlib

        path = (
            pathlib.Path(__file__).parent.parent
            / "migrations"
            / "versions"
            / "0075_research_trusted_execution.py"
        )
        text = path.read_text()
        assert 'revision = "0075"' in text
        assert 'down_revision = "0074"' in text

    def test_upgrade_creates_6_tables(self):
        """upgrade 创建 6 张表。"""
        import pathlib

        path = (
            pathlib.Path(__file__).parent.parent
            / "migrations"
            / "versions"
            / "0075_research_trusted_execution.py"
        )
        text = path.read_text()
        tables = [
            "research_analysis_plan_version",
            "research_analysis_run",
            "research_analysis_step",
            "research_run_artifact",
            "research_ai_conversation",
            "research_memory_document",
        ]
        for t in tables:
            assert t in text, f"表 {t} 未在迁移中定义"

    def test_downgrade_drops_6_tables(self):
        """downgrade 删除 6 张表。"""
        import pathlib

        path = (
            pathlib.Path(__file__).parent.parent
            / "migrations"
            / "versions"
            / "0075_research_trusted_execution.py"
        )
        text = path.read_text()
        tables = [
            "research_analysis_plan_version",
            "research_analysis_run",
            "research_analysis_step",
            "research_run_artifact",
            "research_ai_conversation",
            "research_memory_document",
        ]
        for t in tables:
            assert f"DROP TABLE IF EXISTS {t}" in text, f"表 {t} 未在 downgrade 中删除"


# ============================================================
# 3. ContextRouter
# ============================================================


class TestContextRouter:
    """上下文路由器：500K 预算 + 模式选择 + 分块 + 覆盖率。"""

    def test_budget_hard_limit_500k(self):
        """500K 硬上限。"""
        from packages.research.context_router import DATA_BUDGET_HARD_LIMIT, ContextRouter

        router = ContextRouter()
        budget = router.calculate_budget(model_context_limit=999999)
        assert budget == DATA_BUDGET_HARD_LIMIT
        assert DATA_BUDGET_HARD_LIMIT == 500_000

    def test_budget_subtracts_overhead(self):
        """预算 = model_context - system - context - output - safety。"""
        from packages.research.context_router import ContextRouter

        router = ContextRouter()
        budget = router.calculate_budget(
            model_context_limit=128000,
            system_and_tool_tokens=2000,
            research_context_tokens=10000,
            reserved_output_tokens=4000,
            safety_margin=5000,
        )
        assert budget == 128000 - 2000 - 10000 - 4000 - 5000

    def test_budget_capped_at_500k(self):
        """大模型上下文也受 500K 上限。"""
        from packages.research.context_router import ContextRouter

        router = ContextRouter()
        budget = router.calculate_budget(model_context_limit=2000000)
        assert budget == 500_000

    def test_budget_never_negative(self):
        """预算不为负。"""
        from packages.research.context_router import ContextRouter

        router = ContextRouter()
        budget = router.calculate_budget(
            model_context_limit=100,
            system_and_tool_tokens=200,
            research_context_tokens=300,
            reserved_output_tokens=400,
            safety_margin=500,
        )
        assert budget == 0

    def test_chunk_data_empty_returns_empty(self):
        """空数据分块返回空列表。"""
        from packages.research.context_router import ContextRouter

        router = ContextRouter()
        chunks = router.chunk_data("", 1000)
        assert chunks == []

    def test_chunk_data_by_token_budget(self):
        """按 token 预算切分。"""
        from packages.research.context_router import ContextRouter

        router = ContextRouter()
        data = "x" * 10000  # 约 2857 tokens
        chunks = router.chunk_data(data, 1000)
        assert len(chunks) >= 2
        # 每块不超过预算
        for chunk in chunks:
            assert chunk.token_count <= 1000

    def test_mode_full_compute(self):
        """全量计算模式：requires_full=True, per_record_semantic=False。"""
        from packages.research.context_router import ContextRouter
        from packages.research.models_trusted import AnalysisMode, DataProfile, PlanStep

        router = ContextRouter()
        step = PlanStep(
            step_key="s1",
            question="计算均值",
            requires_full=True,
            per_record_semantic=False,
            cross_record_reasoning=False,
        )
        profile = DataProfile(snapshot_id=uuid4(), total_tokens_estimate=100)
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.FULL_COMPUTE.value

    def test_mode_chunked_when_over_budget(self):
        """超预算 + 逐条语义 → 分块全量扫描。"""
        from packages.research.context_router import ContextRouter
        from packages.research.models_trusted import AnalysisMode, DataProfile, PlanStep

        router = ContextRouter()
        step = PlanStep(
            step_key="s1",
            question="逐条分析",
            requires_full=True,
            per_record_semantic=True,
            allows_sampling=False,
        )
        profile = DataProfile(snapshot_id=uuid4(), total_tokens_estimate=999999)
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.CHUNKED_FULL_SCAN.value

    def test_mode_direct_when_within_budget(self):
        """预算内 + 逐条语义 → 直接全量上下文。"""
        from packages.research.context_router import ContextRouter
        from packages.research.models_trusted import AnalysisMode, DataProfile, PlanStep

        router = ContextRouter()
        step = PlanStep(
            step_key="s1", question="逐条分析", requires_full=True, per_record_semantic=True
        )
        profile = DataProfile(snapshot_id=uuid4(), total_tokens_estimate=100)
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.DIRECT_FULL_CONTEXT.value

    def test_no_silent_sampling(self):
        """不允许抽样时不静默抽样。"""
        from packages.research.context_router import ContextRouter
        from packages.research.models_trusted import AnalysisMode, DataProfile, PlanStep

        router = ContextRouter()
        step = PlanStep(
            step_key="s1",
            question="跨记录推理",
            requires_full=True,
            cross_record_reasoning=True,
            allows_sampling=False,
        )
        profile = DataProfile(snapshot_id=uuid4(), total_tokens_estimate=999999)
        mode, reason = router.analyze_step(step, profile)
        # 不允许抽样 + 超预算 → 分块全量扫描（不是检索探索）
        assert mode == AnalysisMode.CHUNKED_FULL_SCAN.value


# ============================================================
# 4. ResearchScheduler
# ============================================================


class TestResearchScheduler:
    """调度器：20 用户许可 + 公平队列 + 心跳。"""

    def test_max_concurrent_users_default_20(self):
        """默认 20 并发用户。"""
        from packages.research.scheduler import MAX_CONCURRENT_USERS

        assert MAX_CONCURRENT_USERS == 20

    def test_acquire_slot_success_under_limit(self):
        """活跃用户 < 20 → 获取成功。"""
        from unittest.mock import MagicMock

        from packages.research.scheduler import ResearchScheduler

        redis = MagicMock()
        redis.get.return_value = None  # 用户无活跃 Run
        redis.scard.return_value = 5  # 5 个活跃用户
        scheduler = ResearchScheduler(redis)
        import asyncio

        success, pos = asyncio.new_event_loop().run_until_complete(
            scheduler.acquire_slot("user1", "run1")
        )
        assert success is True
        assert pos == 0

    def test_acquire_slot_fails_at_21(self):
        """21 个用户 → 排队。"""
        from unittest.mock import MagicMock

        from packages.research.scheduler import MAX_CONCURRENT_USERS, ResearchScheduler

        redis = MagicMock()
        redis.get.return_value = None
        redis.scard.return_value = MAX_CONCURRENT_USERS  # 已满
        redis.zrank.return_value = 0  # 队列第 1 位
        scheduler = ResearchScheduler(redis)
        import asyncio

        success, pos = asyncio.new_event_loop().run_until_complete(
            scheduler.acquire_slot("user21", "run21")
        )
        assert success is False
        assert pos == 1

    def test_same_user_second_run_rejected(self):
        """同一用户第二个活跃 Run 被拒绝。"""
        from unittest.mock import MagicMock

        from packages.research.scheduler import ResearchScheduler

        redis = MagicMock()
        redis.get.return_value = "existing_run_id"  # 已有活跃 Run
        scheduler = ResearchScheduler(redis)
        import asyncio

        success, pos = asyncio.new_event_loop().run_until_complete(
            scheduler.acquire_slot("user1", "run2")
        )
        assert success is False
        assert pos == -1

    def test_release_slot_frees_resources(self):
        """释放槽位调用 srem + delete。"""
        from unittest.mock import MagicMock

        from packages.research.scheduler import ResearchScheduler

        redis = MagicMock()
        scheduler = ResearchScheduler(redis)
        import asyncio

        asyncio.new_event_loop().run_until_complete(scheduler.release_slot("user1", "run1"))
        redis.srem.assert_called_once()
        assert redis.delete.call_count == 2  # user key + heartbeat


# ============================================================
# 5. DAG 拓扑排序
# ============================================================


class TestDAGTopologicalSort:
    """DAG Kahn 拓扑排序。"""

    def _make_orchestrator(self):
        """创建 orchestrator 实例（最小依赖）。"""
        from packages.research.orchestrator import ResearchOrchestrator

        orch = ResearchOrchestrator.__new__(ResearchOrchestrator)
        return orch

    def test_linear_dag(self):
        """线性 DAG: A → B → C 排序正确。"""
        orch = self._make_orchestrator()
        steps = [
            {"step_key": "C", "dependencies": ["B"]},
            {"step_key": "B", "dependencies": ["A"]},
            {"step_key": "A", "dependencies": []},
        ]
        result = orch._topological_sort(steps)
        assert result is not None
        keys = [s["step_key"] for s in result]
        assert keys == ["A", "B", "C"]

    def test_parallel_dag(self):
        """并行 DAG: A → B, A → C, B → D, C → D。"""
        orch = self._make_orchestrator()
        steps = [
            {"step_key": "D", "dependencies": ["B", "C"]},
            {"step_key": "C", "dependencies": ["A"]},
            {"step_key": "B", "dependencies": ["A"]},
            {"step_key": "A", "dependencies": []},
        ]
        result = orch._topological_sort(steps)
        assert result is not None
        keys = [s["step_key"] for s in result]
        assert keys[0] == "A"
        assert keys[-1] == "D"
        # B 和 C 都在 A 之后 D 之前
        assert keys.index("B") > keys.index("A")
        assert keys.index("C") > keys.index("A")
        assert keys.index("D") > keys.index("B")
        assert keys.index("D") > keys.index("C")

    def test_cycle_returns_none(self):
        """有环时返回 None。"""
        orch = self._make_orchestrator()
        steps = [
            {"step_key": "A", "dependencies": ["B"]},
            {"step_key": "B", "dependencies": ["A"]},
        ]
        result = orch._topological_sort(steps)
        assert result is None

    def test_single_step(self):
        """单步骤排序正确。"""
        orch = self._make_orchestrator()
        steps = [{"step_key": "A", "dependencies": []}]
        result = orch._topological_sort(steps)
        assert result is not None
        assert len(result) == 1
        assert result[0]["step_key"] == "A"


# ============================================================
# 6. Run 状态机
# ============================================================


class TestRunStateMachine:
    """Run 状态机验证。"""

    def test_all_status_values(self):
        """7 个状态值正确。"""
        from packages.research.models_trusted import RunStatus

        assert RunStatus.QUEUED.value == "queued"
        assert RunStatus.PLANNING.value == "planning"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.PARTIALLY_SUCCEEDED.value == "partially_succeeded"
        assert RunStatus.SUCCEEDED.value == "succeeded"
        assert RunStatus.FAILED.value == "failed"
        assert RunStatus.CANCELLED.value == "cancelled"

    def test_step_status_values(self):
        """6 个步骤状态值正确。"""
        from packages.research.models_trusted import StepStatus

        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.SUCCEEDED.value == "succeeded"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"
        assert StepStatus.CANCELLED.value == "cancelled"

    def test_plan_status_values(self):
        """3 个计划状态值正确。"""
        from packages.research.models_trusted import PlanStatus

        assert PlanStatus.DRAFT.value == "draft"
        assert PlanStatus.CONFIRMED.value == "confirmed"
        assert PlanStatus.SUPERSEDED.value == "superseded"


# ============================================================
# 7. CoverageDeclaration
# ============================================================


class TestCoverageDeclaration:
    """覆盖声明。"""

    def test_display_string_format(self):
        """展示字符串格式正确。"""
        from packages.research.models_trusted import CoverageDeclaration

        cov = CoverageDeclaration(
            analysis_mode="mixed",
            data_coverage_rate=1.0,
            llm_read_rate=0.75,
            is_sampled=False,
        )
        s = cov.to_display_string()
        assert "混合分析" in s
        assert "数据覆盖率 100%" in s
        assert "LLM 阅读率 75%" in s
        assert "是否抽样: 否" in s

    def test_to_dict(self):
        """to_dict 包含所有字段。"""
        from packages.research.models_trusted import CoverageDeclaration

        cov = CoverageDeclaration(
            analysis_mode="full_compute",
            data_coverage_rate=1.0,
            llm_read_rate=0.0,
            is_sampled=False,
        )
        d = cov.to_dict()
        assert d["analysis_mode"] == "full_compute"
        assert d["data_coverage_rate"] == 1.0
        assert d["llm_read_rate"] == 0.0
        assert d["is_sampled"] is False

    def test_data_coverage_and_llm_read_independent(self):
        """数据覆盖率与 LLM 阅读率独立。"""
        from packages.research.models_trusted import CoverageDeclaration

        cov = CoverageDeclaration(
            analysis_mode="full_compute",
            data_coverage_rate=1.0,
            llm_read_rate=0.0,
        )
        assert cov.data_coverage_rate != cov.llm_read_rate


# ============================================================
# 8. ModelGateway
# ============================================================


class TestModelGateway:
    """模型网关。"""

    def test_500k_limit_constant(self):
        """500K 硬上限常量存在。"""
        from packages.research.context_router import DATA_BUDGET_HARD_LIMIT

        assert DATA_BUDGET_HARD_LIMIT == 500_000

    def test_model_response_has_metadata(self):
        """ModelResponse 包含调用元数据。"""
        from packages.research.models_trusted import ModelResponse

        resp = ModelResponse(
            answer="test",
            provider="openai",
            model="gpt-4o",
            model_version="2024-08",
            tokens_used=100,
        )
        assert resp.provider == "openai"
        assert resp.model == "gpt-4o"
        assert resp.tokens_used == 100

    def test_model_response_failover_flag(self):
        """故障切换标记。"""
        from packages.research.models_trusted import ModelResponse

        resp = ModelResponse(answer="test", failover_used=True)
        assert resp.failover_used is True


# ============================================================
# 9. 数据模型验证
# ============================================================


class TestDataModels:
    """dataclass 验证。"""

    def test_plan_step_frozen(self):
        """PlanStep frozen。"""
        from packages.research.models_trusted import PlanStep

        step = PlanStep(step_key="s1", question="test")
        with pytest.raises(AttributeError):
            step.step_key = "modified"

    def test_plan_step_defaults(self):
        """PlanStep 默认值。"""
        from packages.research.models_trusted import PlanStep

        step = PlanStep(step_key="s1", question="test")
        assert step.method == "python"
        assert step.requires_full is True
        assert step.allows_sampling is False
        assert step.dependencies == []

    def test_resource_limits_defaults(self):
        """ResourceLimits 默认值：2 CPU / 4GB / 20min。"""
        from packages.research.models_trusted import ResourceLimits

        limits = ResourceLimits()
        assert limits.cpu_count == 2.0
        assert limits.memory_mb == 4096
        assert limits.timeout_seconds == 1200  # 20 分钟

    def test_scope_boundary_defaults(self):
        """ScopeBoundary 默认方法集合。"""
        from packages.research.models_trusted import ScopeBoundary

        scope = ScopeBoundary(snapshot_id=uuid4(), question_version=1)
        assert "python" in scope.methods_allowed
        assert "llm" in scope.methods_allowed
        assert scope.knowledge_base_used is False

    def test_queue_position(self):
        """QueuePosition 数据正确。"""
        from packages.research.models_trusted import QueuePosition

        pos = QueuePosition(position=3, ahead_count=2, estimated_wait_seconds=480)
        assert pos.position == 3
        assert pos.ahead_count == 2

    def test_execution_result(self):
        """ExecutionResult 默认值。"""
        from packages.research.models_trusted import ExecutionResult

        result = ExecutionResult(exit_code=0)
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.stdout == ""


# ============================================================
# 10. 模块隔离
# ============================================================


class TestModuleIsolation:
    """模块隔离验证。"""

    def test_core_tables_no_fk_to_research_trusted(self):
        """核心表无到 research_trusted_* 的 FK。"""
        from packages.facts.entities import Fact
        from packages.provenance.entities import DerivationRun, EvidenceSet

        for cls in [Fact, EvidenceSet, DerivationRun]:
            for fk in cls.__table__.foreign_keys:
                target = fk.target_fullname
                assert "research_" not in target, f"核心表 {cls.__name__} 有到研究表的 FK: {target}"

    def test_research_tables_exist_in_metadata(self):
        """研究表注册到 Base.metadata。"""
        import packages.research.entities_trusted  # noqa
        from packages.common.database import Base

        table_names = set(Base.metadata.tables.keys())
        research_tables = {
            "research_analysis_plan_version",
            "research_analysis_run",
            "research_analysis_step",
            "research_run_artifact",
            "research_ai_conversation",
            "research_memory_document",
        }
        for t in research_tables:
            assert t in table_names, f"表 {t} 未注册到 Base.metadata"


# ============================================================
# 11. API 路由验证
# ============================================================


class TestAPIRoutes:
    """API 路由验证。"""

    def test_router_importable(self):
        """research_run_router 可导入。"""
        from apps.api.routers.research_run import research_run_router

        assert research_run_router is not None

    def test_router_prefix(self):
        """路由前缀正确。"""
        from apps.api.routers.research_run import research_run_router

        assert research_run_router.prefix == "/api/v1/research"

    def test_router_has_18_endpoints(self):
        """18 个端点。"""
        from apps.api.routers.research_run import research_run_router

        routes = [r for r in research_run_router.routes if hasattr(r, "methods") and r.methods]
        assert len(routes) == 18

    def test_main_includes_router(self):
        """main.py 条件注册 research_run_router。"""
        import pathlib

        main_path = pathlib.Path(__file__).parent.parent / "apps" / "api" / "main.py"
        text = main_path.read_text()
        assert "research_run_router" in text

    def test_composition_registers_research_run(self):
        """composition/__init__.py 注册 research_run。"""
        import pathlib

        comp_path = (
            pathlib.Path(__file__).parent.parent / "apps" / "api" / "composition" / "__init__.py"
        )
        text = comp_path.read_text()
        assert "register_research_run" in text


# ============================================================
# 12. 范围越界检测
# ============================================================


class TestScopeBoundary:
    """范围越界检测。"""

    def _make_orchestrator(self):
        from packages.research.orchestrator import ResearchOrchestrator

        return ResearchOrchestrator.__new__(ResearchOrchestrator)

    def test_snapshot_changed_is_violation(self):
        """快照变更检测为越界。"""
        from packages.research.models_trusted import ScopeBoundary

        orch = self._make_orchestrator()
        scope = ScopeBoundary(snapshot_id=uuid4(), question_version=1)
        result = orch._check_scope(scope, uuid4(), 1, "python", "standard")
        assert not result.is_within_scope
        assert result.violation_type == "snapshot_changed"

    def test_question_changed_is_violation(self):
        """研究问题变更检测为越界。"""
        from packages.research.models_trusted import ScopeBoundary

        orch = self._make_orchestrator()
        snap_id = uuid4()
        scope = ScopeBoundary(snapshot_id=snap_id, question_version=1)
        result = orch._check_scope(scope, snap_id, 2, "python", "standard")
        assert not result.is_within_scope
        assert result.violation_type == "question_changed"

    def test_within_scope_no_violation(self):
        """范围内不越界。"""
        from packages.research.models_trusted import ScopeBoundary

        orch = self._make_orchestrator()
        snap_id = uuid4()
        scope = ScopeBoundary(snapshot_id=snap_id, question_version=1)
        result = orch._check_scope(scope, snap_id, 1, "python", "standard")
        assert result.is_within_scope

    def test_resource_upgraded_is_violation(self):
        """资源升级为越界。"""
        from packages.research.models_trusted import ScopeBoundary

        orch = self._make_orchestrator()
        snap_id = uuid4()
        scope = ScopeBoundary(snapshot_id=snap_id, question_version=1, resource_tier="standard")
        result = orch._check_scope(scope, snap_id, 1, "python", "heavy")
        assert not result.is_within_scope
        assert result.violation_type == "resource_upgraded"
