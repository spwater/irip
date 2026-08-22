"""OpenTelemetry 分布式追踪配置（P2-I9）。

在 FastAPI / SQLAlchemy / Celery 层注入 trace spans，
将追踪数据导出到 OTLP collector 或 Jaeger。

启用方式：
  IRIP_OTEL_ENDPOINT=http://jaeger:4317 IRIP_ENV=production

未配置 IRIP_OTEL_ENDPOINT 时不启用追踪（开发环境零开销）。

.. note::

    ``opentelemetry-*`` 包 **不在** ``pyproject.toml`` 的运行时依赖中。
    要启用追踪，需手动安装以下包::

        pip install opentelemetry-sdk opentelemetry-exporter-otlp \\
                    opentelemetry-instrumentation-fastapi \\
                    opentelemetry-instrumentation-sqlalchemy

    未安装时，所有 ``init_tracing`` / ``instrument_*`` 调用会
    静默跳过（``except ImportError`` 分支），不影响应用正常运行。

    Docker Compose 中 Jaeger 服务定义在 ``compose.base.yaml`` 的
    ``monitoring`` profile 下（``profiles: ["monitoring"]``），
    默认不启动；需要时用 ``--profile monitoring`` 拉起。
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_otel_initialized = False


def init_tracing(service_name: str = "irip-api") -> None:
    """初始化 OpenTelemetry 追踪。

    在 FastAPI lifespan 中调用一次。
    未配置 IRIP_OTEL_ENDPOINT 时静默跳过（开发环境零开销）。

    Args:
        service_name: 服务名称（如 "irip-api", "irip-worker"）。
    """
    global _otel_initialized
    if _otel_initialized:
        return
    _otel_initialized = True

    endpoint = os.getenv("IRIP_OTEL_ENDPOINT", "")
    if not endpoint:
        logger.debug("OpenTelemetry disabled (IRIP_OTEL_ENDPOINT not set)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": os.getenv("IRIP_VERSION", "dev"),
                "deployment.environment": os.getenv("IRIP_ENV", "development"),
            }
        )

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        logger.info("OpenTelemetry initialized: service=%s, endpoint=%s", service_name, endpoint)

    except ImportError:
        logger.warning(
            "OpenTelemetry packages not installed. "
            "Install: pip install opentelemetry-sdk opentelemetry-exporter-otlp "
            "opentelemetry-instrumentation-fastapi "
            "opentelemetry-instrumentation-sqlalchemy"
        )
    except Exception:
        logger.warning("Failed to initialize OpenTelemetry", exc_info=True)


def instrument_fastapi(app: Any) -> None:
    """为 FastAPI 应用注入追踪 span。

    在 lifespan 启动后调用。

    Args:
        app: FastAPI 应用实例。
    """
    endpoint = os.getenv("IRIP_OTEL_ENDPOINT", "")
    if not endpoint:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.debug("FastAPI instrumentation enabled")
    except ImportError:
        pass
    except Exception:
        logger.warning("Failed to instrument FastAPI", exc_info=True)


def instrument_sqlalchemy(engine: Any) -> None:
    """为 SQLAlchemy engine 注入追踪 span。

    在 engine 创建后调用。

    Args:
        engine: SQLAlchemy engine 实例。
    """
    endpoint = os.getenv("IRIP_OTEL_ENDPOINT", "")
    if not endpoint:
        return

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor.instrument(engine=engine)
        logger.debug("SQLAlchemy instrumentation enabled")
    except ImportError:
        pass
    except Exception:
        logger.warning("Failed to instrument SQLAlchemy", exc_info=True)
