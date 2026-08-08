"""流程执行结果入库编排逻辑。

从 ``flows.py`` 提取的 ``persist_run_as_fact`` 端点核心编排：
从成功的节点执行中提取三类数据（points + series + header），
创建 Fact 记录，每个 point 作为一条 raw observation。
如果执行时传了 path 且是 PDF 文件，同时上传 PDF 到 artifact 存储。
"""

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.common.artifacts import ArtifactService
from packages.common.errors import AppError
from packages.components.flow.flow_fact_service import FlowFactService
from packages.facts.service import CreateFactCommand, FactService

_logger = logging.getLogger(__name__)


async def persist_run_as_fact_handler(
    service: Any,
    current_user: Any,
    run_id: UUID,
    body: Any,
) -> Any:
    """将流程执行结果写入实验事实。

    Args:
        service: FlowRuntimeService 实例。
        current_user: 当前用户（需有 user_id, department_id）。
        run_id: 流程运行 ID。
        body: PersistFactRequest 请求体。

    Returns:
        PersistFactResponse 响应。
    """
    from apps.api.routers.flows import PersistFactResponse

    # 构建流程事实入库服务
    flow_fact_svc = FlowFactService(
        session_factory=service.session_factory,
        department_id=service.department_id,
        actor_id=current_user.user_id,
    )

    # 1. 获取执行记录和节点输出
    run, executions = await service.get_run(run_id)

    succeeded_nodes = [e for e in executions if e.status == "succeeded" and e.output_summary]
    if not succeeded_nodes:
        raise AppError(
            code="validation_failed",
            message="无成功的节点执行记录",
            retryable=False,
        )

    # 2. 从节点输出提取三类数据（points + series），兼容旧格式
    points: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    header: dict[str, Any] = {}
    source_path: str = ""
    for exec_record in succeeded_nodes:
        meta = exec_record.output_summary.get("_metadata", {})
        if meta.get("points") or meta.get("series"):
            points = meta.get("points") or []
            series = meta.get("series") or []
            header = meta.get("header", meta.get("metadata", {}))
            break

    if not points and not series:
        raise AppError(
            code="validation_failed",
            message="执行结果中无可用的数据",
            retryable=False,
        )

    # 2a. 如果传入了编辑后的自定义数据，覆盖提取的数据
    _logger.info(
        "persist_fact custom_data=%s, points=%d, series=%d",
        body.custom_data is not None,
        len(points),
        len(series),
    )
    if body.custom_data:
        _logger.info(
            "custom_data keys=%s, points_len=%d, series_len=%d",
            list(body.custom_data.keys()),
            len(body.custom_data.get("points", [])),
            len(body.custom_data.get("series", [])),
        )
        if isinstance(body.custom_data.get("points"), list):
            points = body.custom_data["points"]
        if isinstance(body.custom_data.get("series"), list):
            series = body.custom_data["series"]
        if isinstance(body.custom_data.get("metadata"), dict):
            header = body.custom_data["metadata"]
    _logger.info("after override: points=%d, series=%d", len(points), len(series))

    # 3. 从 input_snapshot 获取源文件路径
    input_snapshot = run.input_snapshot or {}
    source_path = str(input_snapshot.get("path", ""))

    # 3a. 解析源文件名
    source_filename: str = ""
    if source_path.startswith("artifact:"):
        try:
            _art_id = UUID(source_path[len("artifact:") :])
            source_filename = await flow_fact_svc.resolve_artifact_filename(_art_id) or ""
        except Exception:
            _logger.warning("unexpected error", exc_info=True)
    elif source_path:
        source_filename = Path(source_path).name

    # 4. 上传原始 PDF + 提取数据 JSON 到 artifact 存储
    pdf_artifact_id: UUID | None = None
    data_artifact_id: UUID | None = None

    try:
        from apps.api.main import _build_s3_repo

        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service.session_factory,
            department_id=service.department_id,
            uploaded_by=current_user.user_id,
        )

        # 4a. 保存原始文件
        if source_path.startswith("artifact:"):
            try:
                _candidate_artifact_id = UUID(source_path[len("artifact:") :])
                if await flow_fact_svc.check_artifact_exists(_candidate_artifact_id):
                    pdf_artifact_id = _candidate_artifact_id
            except ValueError:
                pass
        elif source_path:
            file_path = Path(source_path)
            if file_path.exists():  # noqa: ASYNC240
                raw_data = file_path.read_bytes()  # noqa: ASYNC240
                suffix = file_path.suffix.lower()
                media_types = {
                    ".pdf": "application/pdf",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ".xls": "application/vnd.ms-excel",
                    ".docx": (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    ".doc": "application/msword",
                    ".txt": "text/plain",
                    ".csv": "text/csv",
                }
                raw_media_type = media_types.get(suffix, "application/octet-stream")
                raw_ref = await artifact_svc.put_bytes(
                    data=raw_data,
                    media_type=raw_media_type,
                    filename=file_path.name,
                )
                pdf_artifact_id = raw_ref.artifact_id

        # 4b. 上传提取的数据 JSON
        export_payload = json.dumps(
            {"metadata": header, "points": points, "series": series},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        data_ref = await artifact_svc.put_bytes(
            data=export_payload,
            media_type="application/json",
            filename=f"extract_{run_id}.json",
        )
        data_artifact_id = data_ref.artifact_id
    except Exception:
        _logger.warning("unexpected error", exc_info=True)

    # 6. 查询任务信息快照
    snapshot = await flow_fact_svc.get_task_snapshot(
        flow_version_id=run.flow_version_id,
        input_snapshot=run.input_snapshot or {},
    )

    # 7. 创建事实（使用任务所属部门，而非当前用户部门）
    fact_service = FactService(
        session_factory=service.session_factory,
        department_id=run.department_id,
        actor_id=current_user.user_id,
    )

    file_stem = Path(source_filename).stem if source_filename else ""
    subject_id = (
        f"{snapshot.task_name or ''}-{file_stem}"
        if file_stem
        else (snapshot.task_name or str(run_id))
    )
    group_name = snapshot.task_name or ""

    command = CreateFactCommand(
        fact_type="experiment_run",
        department_id=run.department_id,
        object_id=body.object_id,
        subject_id=subject_id,
        started_at=run.started_at or run.created_at,
        ended_at=run.completed_at,
        idempotency_key=f"flow-run-{run_id}-{body.object_id}-{int(run.created_at.timestamp())}",
        created_by=current_user.user_id,
        task_code=snapshot.task_code,
        task_name=group_name,
        department_name=snapshot.department_name,
        operator=snapshot.operator,
        run_operator=snapshot.run_operator,
        equipment_name=snapshot.equipment_name,
        flow_run_id=run_id,
        source_artifact_id=pdf_artifact_id or data_artifact_id,
    )

    ref = await fact_service.create(command)

    # 写入通用数据索引
    await flow_fact_svc.write_fact_data_index(ref.fact_id, points)

    return PersistFactResponse(
        fact_id=ref.fact_id,
        subject_id=ref.subject_id,
        raw_count=len(points) + sum(len(s.get("rows", [])) for s in series),
        artifact_id=data_artifact_id,
    )
