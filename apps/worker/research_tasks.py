"""可信执行 Celery 任务注册（阶段 2 新增）。

Celery 任务：
- execute_analysis_run(run_id): 执行分析 Run（调用 ResearchOrchestrator.execute_run）
- check_run_heartbeat(): 扫描活跃 Run 心跳，超时标记 failed（Beat 调度，每 30 秒）
- promote_queued_runs(): 检查队列并提升等待 Run（Beat 调度，每 5 秒）

任务命名约定：<domain>.<verb>（如 research.run.execute）

参照 apps/worker/celery_app.py 的模式。
"""

import asyncio
import logging
import os
from typing import Any
from uuid import UUID

from apps.worker.celery_app import celery_app
from packages.common.redis_url import get_redis_url

logger = logging.getLogger("research.tasks")

#: 研究域心跳超时阈值（秒），超过此时间无心跳的 Run 标记为 failed。
HEARTBEAT_TIMEOUT_SECONDS: int = int(os.getenv("RESEARCH_HEARTBEAT_TIMEOUT_SECONDS", "90"))


def _build_orchestrator() -> Any:
    """从环境变量构建 ResearchOrchestrator 实例。

    Worker 进程中通过此函数注入全部执行层依赖：
    - session_factory（数据库会话工厂）
    - ModelGateway（模型网关）— 从 ai_config 表读取研发助手模型配置
    - ContextRouter（上下文路由器）
    - RunArtifactService（工件服务）
    - ResearchMemoryService（研究记忆服务）

    Returns:
        ResearchOrchestrator: 已注入全部依赖的编排器实例。
    """
    import redis as redis_lib

    from packages.common.database import build_session_factory, get_database_url
    from packages.research.execution.models_trusted import ModelConfig, TaskType
    from packages.research.execution.orchestrator import ResearchOrchestrator
    from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
    from packages.research.execution.scheduler import ResearchScheduler
    from packages.research.memory_service import ResearchMemoryService
    from packages.research.planning.context_router import ContextRouter
    from packages.research.planning.model_gateway import ModelGateway
    from packages.research.products.artifact_service import RunArtifactService

    db_url = get_database_url()
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url
    factory = build_session_factory(async_url)

    redis_url = get_redis_url()
    redis_client = redis_lib.from_url(redis_url)

    # 从 YAML 配置读取研发助手模型配置，构建真实 AI provider
    from packages.ai.openai_compatible import OpenAICompatibleProvider
    from packages.ai.yaml_config import get_scenario_config

    ai_provider = None
    research_model_name = None
    try:
        config = get_scenario_config("research")
        research_model_name = config.model
        ai_provider = OpenAICompatibleProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=research_model_name,
            thinking_enabled=config.thinking_enabled,
        )
        logger.info(
            "AI provider initialized: model=%s, base_url=%s, thinking=%s",
            research_model_name,
            config.base_url,
            config.thinking_enabled,
        )
    except Exception as exc:
        logger.warning("Failed to load AI config: %s, using mock provider", exc)

    # 构建模型注册表：所有任务类型使用研发助手模型
    if research_model_name:
        model_registry = {
            task: ModelConfig(
                provider="openai_compatible",
                model=research_model_name,
                version="custom",
                context_limit=128000,
            )
            for task in TaskType
        }
    else:
        model_registry = ModelGateway.get_default_registry()

    # 构建模型网关
    model_gateway = ModelGateway(
        provider=ai_provider,
        audit_recorder=None,
        model_registry=model_registry,
    )

    # 构建上下文路由器（无状态）
    context_router = ContextRouter()

    # 构建调度器
    scheduler = ResearchScheduler(redis_client=redis_client)

    # 构建工件服务
    from apps.api.main import _build_s3_repo

    s3_repo = _build_s3_repo()
    artifact_service = RunArtifactService(
        session_factory=factory,
        s3_repo=s3_repo,
    )

    # 构建研究记忆服务
    memory_service = ResearchMemoryService(session_factory=factory)

    # 构建编排器
    orchestrator = ResearchOrchestrator(
        repo=ResearchRepositoryTrusted,
        model_gateway=model_gateway,
        context_router=context_router,
        artifact_service=artifact_service,
        memory_service=memory_service,
        scheduler=scheduler,
        session_factory=factory,
    )
    return orchestrator


@celery_app.task(
    name="research.run.execute_legacy",
    bind=True,
    soft_time_limit=1500,
    time_limit=1800,
)
def execute_analysis_run(self: object, run_id: str) -> str:
    """Celery 任务（legacy）：执行分析 Run。

    由旧的 AnalysisRunService.submit_run / promote_queued_runs 通过 send_task 触发。
    在 Worker 进程中调用 ResearchOrchestrator.execute_run 执行 DAG 步骤。

    命名注意：timeline 的新版任务已占用 ``research.run.execute``（收 principal），
    为避免 Celery 同名任务 import 顺序依赖（include 里 research_timeline_tasks 后加载
    会覆盖本注册），此处改名 ``research.run.execute_legacy``，保留旧 DAG 引擎以兼容
    存量排队 Run。

    执行前检查部门并发上限，超限时抛异常触发重试。

    Args:
        self: Celery 任务实例（bind=True）。
        run_id: Run UUID 字符串。

    Returns:
        str: Run UUID（用于结果追踪）。
    """
    logger.info("Starting analysis run execution: run_id=%s", run_id)

    # 部门并发上限检查
    import sqlalchemy as sa

    from packages.common.database import build_session_factory, get_database_url
    from packages.research.entities import ResearchWorkspace
    from packages.research.execution.entities_trusted import ResearchAnalysisRun

    db_url = get_database_url()
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url
    factory = build_session_factory(async_url)

    import redis as redis_lib

    from packages.jobs.dept_concurrency import DeptConcurrencyLimiter

    redis_url = get_redis_url()
    redis_client = redis_lib.from_url(redis_url)
    limiter = DeptConcurrencyLimiter(redis_client)

    dept_id_str: str = ""
    acquired: bool = False

    async def _get_dept() -> None:
        nonlocal dept_id_str
        async with factory() as session:
            row = await session.scalar(
                sa.select(ResearchWorkspace.department_id)
                .join(
                    ResearchAnalysisRun,
                    ResearchAnalysisRun.workspace_id == ResearchWorkspace.id,
                )
                .where(ResearchAnalysisRun.id == UUID(run_id))
            )
            if row:
                dept_id_str = str(row)

    asyncio.run(_get_dept())

    if dept_id_str:
        acquired = limiter.acquire(dept_id_str)
        if not acquired:
            raise RuntimeError(f"部门并发上限已达，Run {run_id} 将在下次调度时重试")

    try:
        orchestrator = _build_orchestrator()

        async def _execute() -> None:
            await orchestrator.execute_run(UUID(run_id))

        asyncio.run(_execute())
        logger.info("Analysis run execution completed: run_id=%s", run_id)
        return run_id
    finally:
        if acquired and dept_id_str:
            limiter.release(dept_id_str)


@celery_app.task(name="research.heartbeat", soft_time_limit=30, time_limit=60)
def check_run_heartbeat() -> int:
    """Celery Beat 调度任务：检查活跃 Run 心跳。

    每 30 秒执行。扫描活跃 Run 的心跳时间戳，
    超过 HEARTBEAT_TIMEOUT_SECONDS（默认 90 秒）无心跳的 Run 标记为 failed 并释放槽位。

    Returns:
        int: 被回收的 Run 数。
    """
    import redis as redis_lib

    redis_url = get_redis_url()
    r = redis_lib.from_url(redis_url)

    import sqlalchemy as sa

    from packages.common.database import build_session_factory, get_database_url
    from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
    from packages.research.execution.scheduler import ResearchScheduler

    db_url = get_database_url()
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url
    factory = build_session_factory(async_url)
    scheduler = ResearchScheduler(redis_client=r)

    async def _check() -> int:
        expired_run_ids = await scheduler.check_heartbeats()
        count = 0
        for run_id_str in expired_run_ids:
            try:
                run_id = UUID(run_id_str)
            except ValueError:
                continue
            async with factory() as session:
                async with session.begin():
                    await ResearchRepositoryTrusted.update_run_status(
                        session,
                        run_id,
                        "failed",
                        completed_at=sa.func.now(),  # type: ignore[arg-type]
                        error_summary="心跳超时，Worker 可能崩溃",
                    )
                    count += 1
        return count

    return asyncio.run(_check())


@celery_app.task(name="research.promote_queued", soft_time_limit=30, time_limit=60)
def promote_queued_runs() -> int:
    """Celery Beat 调度任务：检查队列并提升等待 Run。

    每 5 秒执行。检查是否有空闲槽位，有则从等待队列取出最早的 Run 提升。

    Returns:
        int: 提升的 Run 数。
    """
    import redis as redis_lib

    from packages.research.execution.scheduler import ResearchScheduler

    redis_url = get_redis_url()
    r = redis_lib.from_url(redis_url)
    scheduler = ResearchScheduler(redis_client=r)

    async def _promote() -> int:
        promoted = await scheduler.check_and_promote()
        # 对每个提升的 Run 发送执行任务（检查 DB 状态，跳过已取消/失败的）
        from packages.common.database import build_session_factory, get_database_url
        from packages.research.execution.repository_trusted import ResearchRepositoryTrusted

        db_url = get_database_url()
        if db_url.startswith("postgresql+psycopg://"):
            async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
        else:
            async_url = db_url
        factory = build_session_factory(async_url)

        valid_promoted = 0
        for run_id_str in promoted:
            # 检查 Run 状态，跳过已取消/失败的
            async with factory() as session:
                run = await ResearchRepositoryTrusted.get_run(session, UUID(run_id_str))
                if run is None or run.status in (
                    "failed",
                    "cancelled",
                    "succeeded",
                    "partially_succeeded",
                ):
                    logger.info(
                        "Skipping promoted run %s (status=%s)",
                        run_id_str,
                        run.status if run else "not_found",
                    )
                    continue
            celery_app.send_task(
                "research.run.execute_legacy",
                kwargs={"run_id": run_id_str},
                queue="irip-research",
            )
            valid_promoted += 1
        return valid_promoted

    return asyncio.run(_promote())


@celery_app.task(name="research.cleanup_warm", soft_time_limit=30, time_limit=60)
def cleanup_warm_containers() -> int:
    """Celery Beat 调度任务：清理过期保温容器。

    每 60 秒执行。沙箱执行链路已搁置，保温容器功能未启用，
    此任务为 no-op 占位，避免 Beat 调度未注册任务报错。

    Returns:
        int: 0（当前无操作）。
    """
    return 0
