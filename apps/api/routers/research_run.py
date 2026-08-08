"""可信执行 API 路由（阶段 2 新增）。

端点分组（research_run_router, prefix=/api/v1/research）：
  POST   /workspaces/{id}/plans                        — 生成分析计划
  GET    /workspaces/{id}/plans                         — 列出计划
  GET    /workspaces/{id}/plans/{plan_id}                — 获取计划详情
  POST   /workspaces/{id}/plans/{plan_id}/confirm        — 确认计划
  POST   /workspaces/{id}/runs                           — 提交 Run
  GET    /workspaces/{id}/runs                           — 列出 Run
  GET    /workspaces/{id}/runs/{run_id}                   — 获取 Run 详情
  POST   /workspaces/{id}/runs/{run_id}/cancel            — 取消 Run
  GET    /workspaces/{id}/runs/{run_id}/steps             — 获取步骤状态
  GET    /workspaces/{id}/runs/{run_id}/artifacts          — 列出工件
  GET    /workspaces/{id}/runs/{run_id}/artifacts/{aid}    — 获取工件
  GET    /workspaces/{id}/runs/{run_id}/queue-status       — 排队状态
  GET    /workspaces/{id}/runs/{run_id}/events             — SSE 端点
  POST   /workspaces/{id}/conversation                    — 发送 AI 消息
  GET    /workspaces/{id}/conversation                     — 获取对话历史

所有写端点使用 require_permission("research:use") 权限依赖。
参照 apps/api/routers/research.py 的 DI 占位 + Pydantic 模型模式。
"""

import json
import os
from typing import Annotated, Any
from uuid import UUID

import redis as redis_lib
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.research.conversation_service import AIConversationService
from packages.research.execution.run_service import AnalysisRunService
from packages.research.planning.plan_service import PlanService

#: 需 research:use 权限的当前用户依赖。
ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]


# ---- DI 占位 ----


def get_plan_service() -> PlanService:
    """获取 PlanService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_plan_service must be overridden via dependency_overrides")


PlanServiceDep = Annotated[PlanService, Depends(get_plan_service)]


def get_run_service() -> AnalysisRunService:
    """获取 AnalysisRunService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_run_service must be overridden via dependency_overrides")


RunServiceDep = Annotated[AnalysisRunService, Depends(get_run_service)]


def get_conversation_service() -> AIConversationService:
    """获取 AIConversationService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_conversation_service must be overridden via dependency_overrides"
    )


ConversationServiceDep = Annotated[AIConversationService, Depends(get_conversation_service)]


# ---- 路由实例 ----

research_run_router = APIRouter(prefix="/api/v1/research", tags=["research-run"])


# ============================================================
# 请求模型
# ============================================================


class GeneratePlanRequest(BaseModel):
    """生成分析计划请求。"""

    snapshot_id: UUID


class AnalyzeDataRequest(BaseModel):
    """数据分析请求（Step 2: LLM 按建议分析数据）。"""

    plan_id: UUID
    snapshot_id: UUID
    edited_advice: str | None = None


class ExtractInsightRequest(BaseModel):
    """Insight 提取请求（Step 3: 从分析结果提取 Insight）。"""

    plan_id: UUID
    snapshot_id: UUID


class SubmitRunRequest(BaseModel):
    """提交 Run 请求。"""

    plan_version_id: UUID
    snapshot_id: UUID


class SendMessageRequest(BaseModel):
    """发送 AI 消息请求。"""

    message: str = Field(..., min_length=1, max_length=8192)
    run_id: UUID | None = None


# ============================================================
# 响应模型
# ============================================================


class PlanResponse(BaseModel):
    """计划响应。"""

    plan_id: str
    workspace_id: str
    version_number: int
    status: str
    step_count: int


class PlanDetailResponse(BaseModel):
    """计划详情响应。"""

    plan_id: str
    workspace_id: str
    version_number: int
    status: str
    dag_structure: dict[str, Any]
    coverage_declaration: dict[str, Any] | None = None
    created_at: str | None = None
    confirmed_at: str | None = None


class PlanListResponse(BaseModel):
    """计划列表响应。"""

    items: list[PlanResponse]


class RunResponse(BaseModel):
    """Run 响应。"""

    run_id: str
    workspace_id: str
    run_number: int
    status: str
    queue_position: int | None = None


class RunListResponse(BaseModel):
    """Run 列表响应。"""

    items: list[RunResponse]


class StepProgressResponse(BaseModel):
    """步骤进度响应。"""

    step_id: str
    step_key: str
    step_index: int
    status: str
    method: str
    analysis_mode: str | None = None
    coverage_rate: float | None = None
    llm_read_rate: float | None = None
    is_sampled: bool = False
    attempt_count: int = 0
    error_message: str | None = None


class RunProgressResponse(BaseModel):
    """Run 进度响应。"""

    run_id: str
    status: str
    total_steps: int
    completed_steps: int
    steps: list[StepProgressResponse]
    coverage_declaration: dict[str, Any] | None = None
    started_at: str | None = None
    completed_at: str | None = None


class ArtifactResponse(BaseModel):
    """工件响应。"""

    artifact_id: str
    run_id: str
    step_id: str | None
    artifact_type: str
    artifact_key: str
    storage_path: str
    content_hash: str | None = None
    size_bytes: int | None = None
    is_publishable: bool = False
    created_at: str | None = None


class ArtifactListResponse(BaseModel):
    """工件列表响应。"""

    items: list[ArtifactResponse]


class QueueStatusResponse(BaseModel):
    """排队状态响应。"""

    position: int
    ahead_count: int
    estimated_wait_seconds: int


class ConversationMessageResponse(BaseModel):
    """对话消息响应。"""

    message_id: str
    workspace_id: str
    role: str
    content: dict[str, Any]
    run_id: str | None = None
    created_at: str | None = None


class ConversationListResponse(BaseModel):
    """对话列表响应。"""

    items: list[ConversationMessageResponse]


class EligibilityResponse(BaseModel):
    """发布资格校验响应。"""

    is_eligible: bool
    failed_step_keys: list[str] = []
    source_run_partial: bool = False
    message: str = ""


# ============================================================
# 辅助函数
# ============================================================


def _plan_ref_to_response(ref: Any) -> PlanResponse:
    """将 PlanVersionRef 转为响应模型。"""
    return PlanResponse(
        plan_id=str(ref.plan_id),
        workspace_id=str(ref.workspace_id),
        version_number=ref.version_number,
        status=ref.status,
        step_count=ref.step_count,
    )


def _plan_detail_to_response(detail: Any) -> PlanDetailResponse:
    """将 PlanDetail 转为响应模型。"""
    return PlanDetailResponse(
        plan_id=str(detail.plan_id),
        workspace_id=str(detail.workspace_id),
        version_number=detail.version_number,
        status=detail.status,
        dag_structure=detail.dag_structure,
        coverage_declaration=detail.coverage_declaration,
        created_at=detail.created_at.isoformat() if detail.created_at else None,
        confirmed_at=detail.confirmed_at.isoformat() if detail.confirmed_at else None,
    )


def _run_ref_to_response(ref: Any) -> RunResponse:
    """将 RunRef 转为响应模型。"""
    return RunResponse(
        run_id=str(ref.run_id),
        workspace_id=str(ref.workspace_id),
        run_number=ref.run_number,
        status=ref.status,
        queue_position=ref.queue_position,
    )


def _step_progress_to_response(s: Any) -> StepProgressResponse:
    """将 StepProgress 转为响应模型。"""
    return StepProgressResponse(
        step_id=str(s.step_id),
        step_key=s.step_key,
        step_index=s.step_index,
        status=s.status,
        method=s.method,
        analysis_mode=s.analysis_mode,
        coverage_rate=s.coverage_rate,
        llm_read_rate=s.llm_read_rate,
        is_sampled=s.is_sampled,
        attempt_count=s.attempt_count,
        error_message=s.error_message,
    )


def _run_progress_to_response(p: Any) -> RunProgressResponse:
    """将 RunProgress 转为响应模型。"""
    coverage_dict = p.coverage_declaration.to_dict() if p.coverage_declaration else None
    return RunProgressResponse(
        run_id=str(p.run_id),
        status=p.status,
        total_steps=p.total_steps,
        completed_steps=p.completed_steps,
        steps=[_step_progress_to_response(s) for s in p.steps],
        coverage_declaration=coverage_dict,
        started_at=p.started_at.isoformat() if p.started_at else None,
        completed_at=p.completed_at.isoformat() if p.completed_at else None,
    )


def _artifact_to_response(a: Any) -> ArtifactResponse:
    """将 ArtifactRef 转为响应模型。"""
    return ArtifactResponse(
        artifact_id=str(a.artifact_id),
        run_id=str(a.run_id),
        step_id=str(a.step_id) if a.step_id else None,
        artifact_type=a.artifact_type,
        artifact_key=a.artifact_key,
        storage_path=a.storage_path,
        content_hash=a.content_hash,
        size_bytes=a.size_bytes,
        is_publishable=a.is_publishable,
        created_at=a.created_at.isoformat() if a.created_at else None,
    )


def _message_to_response(m: Any) -> ConversationMessageResponse:
    """将 ConversationMessage 转为响应模型。"""
    return ConversationMessageResponse(
        message_id=str(m.message_id),
        workspace_id=str(m.workspace_id),
        role=m.role,
        content=m.content,
        run_id=str(m.run_id) if m.run_id else None,
        created_at=m.created_at.isoformat() if m.created_at else None,
    )


# ============================================================
# 端点 — 计划
# ============================================================


@research_run_router.post(
    "/workspaces/{workspace_id}/plans",
    response_model=PlanResponse,
    status_code=201,
)
async def generate_plan(
    workspace_id: UUID,
    body: GeneratePlanRequest,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> PlanResponse:
    """生成分析计划（AI 检查数据 → 生成 DAG 步骤）。"""
    ref = await service.generate_plan(workspace_id, body.snapshot_id)
    return _plan_ref_to_response(ref)


@research_run_router.post(
    "/workspaces/{workspace_id}/analyze-data",
)
async def analyze_data(
    workspace_id: UUID,
    body: AnalyzeDataRequest,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> Any:
    """Step 2: 基于分析建议执行数据分析。"""
    result = await service.analyze_data(
        workspace_id=workspace_id,
        plan_id=body.plan_id,
        snapshot_id=body.snapshot_id,
        edited_advice=body.edited_advice,
    )
    return result


@research_run_router.post(
    "/workspaces/{workspace_id}/extract-insight",
)
async def extract_insight(
    workspace_id: UUID,
    body: ExtractInsightRequest,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> Any:
    """Step 3: 从分析结果提取 Insight 候选。"""
    result = await service.extract_insight(
        workspace_id=workspace_id,
        plan_id=body.plan_id,
        snapshot_id=body.snapshot_id,
    )
    return result


@research_run_router.get(
    "/workspaces/{workspace_id}/plans",
    response_model=PlanListResponse,
)
async def list_plans(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> PlanListResponse:
    """列出工作空间的全部计划版本。"""
    refs = await service.list_plans(workspace_id)
    return PlanListResponse(items=[_plan_ref_to_response(r) for r in refs])


@research_run_router.get(
    "/workspaces/{workspace_id}/plans/{plan_id}",
    response_model=PlanDetailResponse,
)
async def get_plan(
    workspace_id: UUID,
    plan_id: UUID,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> PlanDetailResponse:
    """获取计划详情。"""
    detail = await service.get_plan(workspace_id, plan_id)
    return _plan_detail_to_response(detail)


@research_run_router.post(
    "/workspaces/{workspace_id}/plans/{plan_id}/confirm",
    response_model=PlanResponse,
)
async def confirm_plan(
    workspace_id: UUID,
    plan_id: UUID,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> PlanResponse:
    """确认分析计划。"""
    ref = await service.confirm_plan(workspace_id, plan_id)
    return _plan_ref_to_response(ref)


class RevisePlanRequest(BaseModel):
    steps: list[dict[str, Any]] = Field(..., description="修订后的步骤列表")


@research_run_router.put(
    "/workspaces/{workspace_id}/plans/{plan_id}",
    response_model=PlanResponse,
)
async def revise_plan(
    workspace_id: UUID,
    plan_id: UUID,
    body: RevisePlanRequest,
    current_user: ResearchUserDep,
    service: PlanServiceDep,
) -> PlanResponse:
    """修订分析计划（创建新版本，旧版本标记为 superseded）。"""
    ref = await service.revise_plan(workspace_id, plan_id, body.steps)
    return _plan_ref_to_response(ref)


# ============================================================
# 端点 — Run
# ============================================================


@research_run_router.post(
    "/workspaces/{workspace_id}/runs",
    response_model=RunResponse,
    status_code=201,
)
async def submit_run(
    workspace_id: UUID,
    body: SubmitRunRequest,
    current_user: ResearchUserDep,
    service: RunServiceDep,
) -> RunResponse:
    """提交分析 Run。"""
    ref = await service.submit_run(workspace_id, body.plan_version_id, body.snapshot_id)
    return _run_ref_to_response(ref)


@research_run_router.get(
    "/workspaces/{workspace_id}/runs",
    response_model=RunListResponse,
)
async def list_runs(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: RunServiceDep,
) -> RunListResponse:
    """列出工作空间的全部 Run。"""
    refs = await service.list_runs(workspace_id)
    return RunListResponse(items=[_run_ref_to_response(r) for r in refs])


@research_run_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}",
    response_model=RunProgressResponse,
)
async def get_run(
    workspace_id: UUID,
    run_id: UUID,
    current_user: ResearchUserDep,
    service: RunServiceDep,
) -> RunProgressResponse:
    """获取 Run 详情 + 进度。"""
    progress = await service.get_run_progress(run_id)
    return _run_progress_to_response(progress)


@research_run_router.post(
    "/workspaces/{workspace_id}/runs/{run_id}/cancel",
    status_code=204,
)
async def cancel_run(
    workspace_id: UUID,
    run_id: UUID,
    current_user: ResearchUserDep,
    service: RunServiceDep,
) -> None:
    """取消 Run。"""
    await service.cancel_run(run_id)


@research_run_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/steps",
    response_model=RunProgressResponse,
)
async def get_run_steps(
    workspace_id: UUID,
    run_id: UUID,
    current_user: ResearchUserDep,
    service: RunServiceDep,
) -> RunProgressResponse:
    """获取 Run 步骤状态。"""
    progress = await service.get_run_progress(run_id)
    return _run_progress_to_response(progress)


@research_run_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/artifacts",
    response_model=ArtifactListResponse,
)
async def list_run_artifacts(
    workspace_id: UUID,
    run_id: UUID,
    current_user: ResearchUserDep,
    artifact_type: str | None = Query(None, description="按类型过滤"),
) -> ArtifactListResponse:
    """列出 Run 的工件。"""
    from apps.api.routers.research_run import _get_artifact_service

    artifact_service = _get_artifact_service()
    artifacts = await artifact_service.list_artifacts(run_id, artifact_type=artifact_type)
    return ArtifactListResponse(items=[_artifact_to_response(a) for a in artifacts])


@research_run_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/artifacts/{artifact_id}",
)
async def get_run_artifact(
    workspace_id: UUID,
    run_id: UUID,
    artifact_id: UUID,
    current_user: ResearchUserDep,
) -> dict[str, Any]:
    """获取工件内容。"""
    from apps.api.routers.research_run import _get_artifact_service

    artifact_service = _get_artifact_service()
    content = await artifact_service.get_artifact(artifact_id)
    if content is None:
        return {"error": {"code": "not_found", "message": "工件不存在"}}
    return {
        "artifact_id": str(content.artifact_id),
        "artifact_type": content.artifact_type,
        "artifact_key": content.artifact_key,
        "content_hash": content.content_hash,
        "size": len(content.content),
    }


@research_run_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/queue-status",
    response_model=QueueStatusResponse,
)
async def get_queue_status(
    workspace_id: UUID,
    run_id: UUID,
    current_user: ResearchUserDep,
    service: RunServiceDep,
) -> QueueStatusResponse:
    """获取排队状态。"""
    pos = await service.get_queue_position(run_id)
    return QueueStatusResponse(
        position=pos.position,
        ahead_count=pos.ahead_count,
        estimated_wait_seconds=pos.estimated_wait_seconds,
    )


# ============================================================
# 端点 — SSE
# ============================================================


@research_run_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/events",
)
async def run_events(
    workspace_id: UUID,
    run_id: UUID,
    current_user: ResearchUserDep,
) -> EventSourceResponse:
    """SSE 端点：实时推送 Run 进度事件。

    订阅 Redis pub/sub 频道 research:run:{run_id}:events，
    将事件转发给前端。
    """
    redis_url = os.getenv("IRIP_REDIS_URL", "redis://localhost:6379/0")
    channel = f"research:run:{run_id}:events"

    async def event_generator() -> Any:
        """SSE 事件生成器。"""
        r = redis_lib.from_url(redis_url)  # type: ignore[no-untyped-call]
        pubsub = r.pubsub()
        pubsub.subscribe(channel)

        try:
            for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event", "message")
                        event_data = parsed.get("data", "{}")
                        yield {"event": event_type, "data": event_data}
                    except json.JSONDecodeError:
                        yield {"event": "message", "data": data}
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    return EventSourceResponse(event_generator())


# ============================================================
# 端点 — 对话
# ============================================================


@research_run_router.post(
    "/workspaces/{workspace_id}/conversation",
    response_model=ConversationMessageResponse,
)
async def send_message(
    workspace_id: UUID,
    body: SendMessageRequest,
    current_user: ResearchUserDep,
    service: ConversationServiceDep,
) -> ConversationMessageResponse:
    """发送 AI 消息并获取回复。"""
    msg = await service.send_message(workspace_id, body.message, body.run_id)
    return _message_to_response(msg)


@research_run_router.get(
    "/workspaces/{workspace_id}/conversation",
    response_model=ConversationListResponse,
)
async def list_messages(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: ConversationServiceDep,
    run_id: UUID | None = Query(None, description="按 Run ID 过滤"),  # noqa: B008
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),  # noqa: B008
) -> ConversationListResponse:
    """获取对话历史。"""
    messages = await service.list_messages(workspace_id, run_id=run_id, limit=limit)
    return ConversationListResponse(items=[_message_to_response(m) for m in messages])


# ============================================================
# 辅助 — 工件服务获取
# ============================================================


_artifact_service_instance: object | None = None


def _set_artifact_service(service: object) -> None:
    """设置工件服务实例（由 Composition 注册时调用）。"""
    global _artifact_service_instance
    _artifact_service_instance = service


def _get_artifact_service() -> Any:
    """获取工件服务实例。"""
    global _artifact_service_instance
    if _artifact_service_instance is None:
        raise NotImplementedError(
            "ArtifactService not registered. Call _set_artifact_service first."
        )
    return _artifact_service_instance
