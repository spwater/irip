"""Celery 模型训练 / 预测任务（IRIP V2-T04）。

包装模型生命周期服务为 Celery 任务。任务通过 asyncio.run()
在同步 Celery 上下文中执行异步模型服务。

模式与 V1 的 worker/tasks/ingestion.py、V2-T03 的 flows.py 一致：
- 从环境变量构建数据库会话工厂；
- 构建 ArtifactService（通过 S3Repository）与 ModelService；
- 调用 ModelService 的对应方法；
- 更新作业状态（RUNNING → SUCCEEDED/FAILED）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

from apps.worker.celery_app import celery_app


def _build_session_factory() -> Any:
    """从环境变量构建异步会话工厂。

    Returns:
        async_sessionmaker: 异步会话工厂。
    """
    from packages.common.database import build_session_factory

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url
    return build_session_factory(async_url)


def _build_artifact_service(factory: Any, department_id: UUID, user_id: UUID) -> Any:
    """构建工件服务实例。

    Args:
        factory: 异步会话工厂。
        department_id: 组织 ID。
        user_id: 操作人 ID。

    Returns:
        ArtifactService: 工件服务实例。
    """
    from packages.common.artifacts import ArtifactService
    from packages.common.s3_repository import S3Repository

    s3_endpoint = os.getenv("IRIP_S3_ENDPOINT", "http://localhost:59000")
    s3_access = os.getenv("IRIP_S3_ACCESS_KEY", "irip")
    s3_secret = os.getenv("IRIP_S3_SECRET_KEY", "irip_dev_password")
    s3_bucket = os.getenv("IRIP_S3_BUCKET", "irip")
    s3_repo = S3Repository(
        endpoint_url=s3_endpoint,
        access_key=s3_access,
        secret_key=s3_secret,
        bucket_name=s3_bucket,
    )
    return ArtifactService(
        s3_repo=s3_repo,
        session_factory=factory,
        department_id=department_id,
        uploaded_by=user_id,
    )


def _build_model_service(factory: Any, department_id: UUID, user_id: UUID) -> Any:
    """构建模型服务实例。

    注入 FactService 使模型预测结果写入溯源事实链（F-11）。

    Args:
        factory: 异步会话工厂。
        department_id: 组织 ID。
        user_id: 操作人 ID。

    Returns:
        ModelService: 模型服务实例。
    """
    from packages.facts.service import FactService
    from packages.models.service import ModelService

    artifact_service = _build_artifact_service(factory, department_id, user_id)
    fact_service = FactService(
        session_factory=factory,
        department_id=department_id,
        actor_id=user_id,
    )
    return ModelService(
        session_factory=factory,
        department_id=department_id,
        actor_id=user_id,
        artifact_service=artifact_service,
        fact_service=fact_service,
    )


async def _train_model_async(payload: dict) -> dict:
    """异步训练模型。

    从 payload 提取组织 ID、模型代码、显示名称与版本 ID，
    构建 ModelService，创建模型并提交验证。

    Args:
        payload: 任务载荷，包含：
            - department_id: 组织 ID
            - user_id: 操作人 ID
            - code: 模型代码
            - display_name: 模型显示名称
            - version_id: 版本 ID

    Returns:
        dict: 训练结果摘要。
    """

    department_id = UUID(str(payload["department_id"]))
    user_id = UUID(str(payload.get("user_id", payload["department_id"])))
    code: str = str(payload["code"])
    display_name: str = str(payload["display_name"])
    version_id = UUID(str(payload["version_id"]))

    factory = _build_session_factory()
    service = _build_model_service(factory, department_id, user_id)

    # 创建模型（若已存在则复用）
    try:
        model = await service.create_model(code, display_name)
        model_id = model.id
    except Exception:
        models = await service.list_models()
        existing = next((m for m in models if m.code == code), None)
        if existing is None:
            raise
        model_id = existing.id

    version = await service.submit_for_validation(model_id, version_id)

    return {
        "model_id": str(model_id),
        "version_id": str(version.id),
        "version": version.version,
        "status": version.status,
    }


async def _predict_model_async(payload: dict) -> dict:
    """异步执行模型预测。

    从 payload 提取组织 ID、模型 ID 与输入参数，
    构建 ModelService，执行预测。

    Args:
        payload: 任务载荷，包含：
            - department_id: 组织 ID
            - user_id: 操作人 ID
            - model_id: 模型 ID
            - inputs: 输入参数字典

    Returns:
        dict: 预测结果摘要。
    """
    department_id = UUID(str(payload["department_id"]))
    user_id = UUID(str(payload.get("user_id", payload["department_id"])))
    model_id = UUID(str(payload["model_id"]))
    inputs: dict[str, Any] = dict(payload.get("inputs", {}))

    factory = _build_session_factory()
    service = _build_model_service(factory, department_id, user_id)

    result = await service.predict(model_id, inputs)

    return {
        "model_id": str(result.model_id),
        "model_version_id": str(result.model_version_id),
        "version": result.version,
        "predictions": dict(result.predictions),
        "fact_id": str(result.fact_id) if result.fact_id else None,
    }


async def _publish_model_async(payload: dict) -> dict:
    """异步发布模型版本。

    Args:
        payload: 任务载荷，包含 department_id, user_id, model_id, version_id。

    Returns:
        dict: 发布结果摘要。
    """
    department_id = UUID(str(payload["department_id"]))
    user_id = UUID(str(payload.get("user_id", payload["department_id"])))
    model_id = UUID(str(payload["model_id"]))
    version_id = UUID(str(payload["version_id"]))

    factory = _build_session_factory()
    service = _build_model_service(factory, department_id, user_id)

    model = await service.publish(model_id, version_id)

    return {
        "model_id": str(model.id),
        "current_version_id": (
            str(model.current_version_id) if model.current_version_id is not None else None
        ),
        "status": model.status,
    }


@celery_app.task(name="irip.model.train")
def train_model_job(job_id: str, payload: dict) -> dict:
    """Celery 任务：训练模型（创建 + 提交验证）。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 任务载荷字典。

    Returns:
        dict: 训练结果摘要。
    """
    try:
        return asyncio.run(_train_model_async(payload))
    except Exception as exc:
        return {
            "error": str(exc),
            "job_id": job_id,
            "payload": payload,
        }


@celery_app.task(name="irip.model.predict")
def predict_model_job(job_id: str, payload: dict) -> dict:
    """Celery 任务：执行模型预测。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 任务载荷字典。

    Returns:
        dict: 预测结果摘要。
    """
    try:
        return asyncio.run(_predict_model_async(payload))
    except Exception as exc:
        return {
            "error": str(exc),
            "job_id": job_id,
            "payload": payload,
        }


@celery_app.task(name="irip.model.publish")
def publish_model_job(job_id: str, payload: dict) -> dict:
    """Celery 任务：发布模型版本。

    Args:
        job_id: 作业 UUID 字符串。
        payload: 任务载荷字典。

    Returns:
        dict: 发布结果摘要。
    """
    try:
        return asyncio.run(_publish_model_async(payload))
    except Exception as exc:
        return {
            "error": str(exc),
            "job_id": job_id,
            "payload": payload,
        }
