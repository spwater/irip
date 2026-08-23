"""Unit tests for apps.worker.tasks.models — Celery model tasks.

Tests the model_train / model_predict / model_publish Celery tasks
and their async helpers with mocked services.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import apps.worker.tasks.models as models_mod
from apps.worker.tasks.models import (
    predict_model_job,
    publish_model_job,
    train_model_job,
)


@pytest.fixture(autouse=True)
def _mock_sysuser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock get_system_service_user_id to avoid DB access."""
    monkeypatch.setattr(
        "apps.worker.tasks.models.get_system_service_user_id",
        AsyncMock(return_value=UUID("00000000-0000-0000-0000-000000000001")),
    )


def _get_train_task():
    """Get the train_model_job task proxy."""
    return train_model_job


def _get_predict_task():
    """Get the predict_model_job task proxy."""
    return predict_model_job


def _get_publish_task():
    """Get the publish_model_job task proxy."""
    return publish_model_job


class TestTrainModelAsync:
    """Tests for _train_model_async."""

    async def test_successful_train(self) -> None:
        model_mock = MagicMock()
        model_mock.id = UUID("11111111-0000-0000-0000-000000000001")
        version_mock = MagicMock()
        version_mock.id = UUID("22222222-0000-0000-0000-000000000001")
        version_mock.version = "v1"
        version_mock.status = "validated"

        service_mock = AsyncMock()
        service_mock.create_model.return_value = model_mock
        service_mock.submit_for_validation.return_value = version_mock

        with (
            patch.object(models_mod, "_build_session_factory", return_value=MagicMock()),
            patch.object(models_mod, "_build_model_service", return_value=service_mock),
        ):
            result = await models_mod._train_model_async(
                {
                    "department_id": "00000000-0000-0000-0000-000000000001",
                    "user_id": "00000000-0000-0000-0000-000000000002",
                    "code": "model_a",
                    "display_name": "Model A",
                    "version_id": "22222222-0000-0000-0000-000000000001",
                }
            )

        assert result["model_id"] == str(model_mock.id)
        assert result["version_id"] == str(version_mock.id)
        assert result["version"] == "v1"
        assert result["status"] == "validated"
        service_mock.create_model.assert_called_once_with("model_a", "Model A")
        service_mock.submit_for_validation.assert_called_once_with(model_mock.id, version_mock.id)

    async def test_train_reuse_existing_model(self) -> None:
        """When create_model raises, fall back to list_models."""
        existing = MagicMock()
        existing.id = UUID("33333311-0000-0000-0000-000000000001")
        existing.code = "model_b"

        version_mock = MagicMock()
        version_mock.id = UUID("22222222-0000-0000-0000-000000000002")
        version_mock.version = "v2"
        version_mock.status = "validated"

        service_mock = AsyncMock()
        service_mock.create_model.side_effect = RuntimeError("already exists")
        service_mock.list_models.return_value = [existing]
        service_mock.submit_for_validation.return_value = version_mock

        with (
            patch.object(models_mod, "_build_session_factory", return_value=MagicMock()),
            patch.object(models_mod, "_build_model_service", return_value=service_mock),
        ):
            result = await models_mod._train_model_async(
                {
                    "department_id": "00000000-0000-0000-0000-000000000001",
                    "code": "model_b",
                    "display_name": "Model B",
                    "version_id": "22222222-0000-0000-0000-000000000002",
                }
            )

        assert result["model_id"] == str(existing.id)
        service_mock.list_models.assert_called_once()

    async def test_train_reuse_no_existing_raises(self) -> None:
        """When create_model raises and list_models has no match, re-raise."""
        service_mock = AsyncMock()
        service_mock.create_model.side_effect = RuntimeError("create failed")
        service_mock.list_models.return_value = []

        with (
            patch.object(models_mod, "_build_session_factory", return_value=MagicMock()),
            patch.object(models_mod, "_build_model_service", return_value=service_mock),
        ):
            with pytest.raises(RuntimeError, match="create failed"):
                await models_mod._train_model_async(
                    {
                        "department_id": "00000000-0000-0000-0000-000000000001",
                        "code": "model_c",
                        "display_name": "Model C",
                        "version_id": "22222222-0000-0000-0000-000000000003",
                    }
                )


class TestPredictModelAsync:
    """Tests for _predict_model_async."""

    async def test_successful_predict(self) -> None:
        result_mock = MagicMock()
        result_mock.model_id = UUID("11111111-0000-0000-0000-000000000001")
        result_mock.model_version_id = UUID("22222222-0000-0000-0000-000000000001")
        result_mock.version = "v1"
        result_mock.predictions = {"out": 42.0}
        result_mock.fact_id = None

        service_mock = AsyncMock()
        service_mock.predict.return_value = result_mock

        with (
            patch.object(models_mod, "_build_session_factory", return_value=MagicMock()),
            patch.object(models_mod, "_build_model_service", return_value=service_mock),
        ):
            result = await models_mod._predict_model_async(
                {
                    "department_id": "00000000-0000-0000-0000-000000000001",
                    "model_id": "11111111-0000-0000-0000-000000000001",
                    "inputs": {"x": 1.0},
                }
            )

        assert result["model_id"] == str(result_mock.model_id)
        assert result["model_version_id"] == str(result_mock.model_version_id)
        assert result["predictions"] == {"out": 42.0}
        assert result["fact_id"] is None
        service_mock.predict.assert_called_once()


class TestPublishModelAsync:
    """Tests for _publish_model_async."""

    async def test_successful_publish(self) -> None:
        model_mock = MagicMock()
        model_mock.id = UUID("11111111-0000-0000-0000-000000000001")
        model_mock.current_version_id = UUID("22222222-0000-0000-0000-000000000001")
        model_mock.status = "active"

        service_mock = AsyncMock()
        service_mock.publish.return_value = model_mock

        with (
            patch.object(models_mod, "_build_session_factory", return_value=MagicMock()),
            patch.object(models_mod, "_build_model_service", return_value=service_mock),
        ):
            result = await models_mod._publish_model_async(
                {
                    "department_id": "00000000-0000-0000-0000-000000000001",
                    "model_id": "11111111-0000-0000-0000-000000000001",
                    "version_id": "22222222-0000-0000-0000-000000000001",
                }
            )

        assert result["model_id"] == str(model_mock.id)
        assert result["current_version_id"] == str(model_mock.current_version_id)
        assert result["status"] == "active"
        service_mock.publish.assert_called_once()


class TestCeleryTaskWrappers:
    """Tests for the Celery task wrapper functions (train_model_job, etc.)."""

    def test_train_model_job_success(self) -> None:
        task = _get_train_task()
        payload = {
            "department_id": "00000000-0000-0000-0000-000000000001",
            "code": "m",
            "display_name": "M",
            "version_id": "22222222-0000-0000-0000-000000000001",
        }
        expected = {"model_id": "x", "version_id": "y", "version": "v1", "status": "ok"}
        with patch.object(models_mod, "asyncio") as mock_aio:
            mock_aio.run.return_value = expected
            result = task.run("job-1", payload)
        assert result == expected

    def test_train_model_job_retryable_error(self) -> None:
        task = _get_train_task()
        payload = {"department_id": "00000000-0000-0000-0000-000000000001"}
        with (
            patch.object(models_mod, "asyncio") as mock_aio,
            patch.object(task, "retry", side_effect=Exception("retry triggered")) as mock_retry,
        ):
            mock_aio.run.side_effect = ConnectionError("transient")
            with pytest.raises(Exception, match="retry triggered"):
                task.run("job-1", payload)
        mock_retry.assert_called_once()

    def test_predict_model_job_non_retryable_raises(self) -> None:
        task = _get_predict_task()
        payload = {"department_id": "00000000-0000-0000-0000-000000000001"}
        with patch.object(models_mod, "asyncio") as mock_aio:
            mock_aio.run.side_effect = ValueError("bad input")
            with pytest.raises(ValueError, match="bad input"):
                task.run("job-1", payload)

    def test_publish_model_job_retryable_error(self) -> None:
        task = _get_publish_task()
        payload = {"department_id": "00000000-0000-0000-0000-000000000001"}
        with (
            patch.object(models_mod, "asyncio") as mock_aio,
            patch.object(task, "retry", side_effect=Exception("retry triggered")) as mock_retry,
        ):
            mock_aio.run.side_effect = TimeoutError("timeout")
            with pytest.raises(Exception, match="retry triggered"):
                task.run("job-1", payload)
        mock_retry.assert_called_once()


class TestBuildHelpers:
    """Tests for _build_session_factory and _build_artifact_service."""

    def test_build_session_factory_async_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "IRIP_DATABASE_URL",
            "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test",
        )
        mock_factory = MagicMock()
        with patch(
            "packages.common.database.build_session_factory", return_value=mock_factory
        ) as bsf:
            result = models_mod._build_session_factory()
        assert result is mock_factory
        called_url = bsf.call_args[0][0]
        assert "psycopg_async" in called_url

    def test_build_session_factory_non_psycopg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql+asyncpg://user@host/db")
        mock_factory = MagicMock()
        with patch(
            "packages.common.database.build_session_factory", return_value=mock_factory
        ) as bsf:
            result = models_mod._build_session_factory()
        assert result is mock_factory
        called_url = bsf.call_args[0][0]
        assert called_url == "postgresql+asyncpg://user@host/db"
