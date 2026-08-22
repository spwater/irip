"""Tests for FactDataLoader: fact row/sample loading and context formatting."""

from __future__ import annotations

import builtins
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.research.timeline.fact_data_loader import FactDataLoader

#: apps.api.main imports packages.common.metrics which requires prometheus_client.
#: test_success_builds_provider monkeypatches apps.api.main._build_s3_repo, triggering
#: the full import chain. Skip when prometheus_client is unavailable.
try:
    import prometheus_client  # noqa: F401

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False


def _make_ref(source_name: str, source_id: uuid4 | None = None) -> SimpleNamespace:
    return SimpleNamespace(source_name=source_name, source_id=source_id or uuid4())


def _make_session(refs: list) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = refs
    session.execute = AsyncMock(return_value=result)
    return session


def _make_loader(factory: MagicMock | None = None) -> FactDataLoader:
    return FactDataLoader(
        session_factory=factory or MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
    )


class TestBuildFactProvider:
    def test_import_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "packages.research.lineage.core_adapter":
                raise ImportError("simulated import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert loader._build_fact_provider() is None

    @pytest.mark.skipif(
        not _HAS_PROMETHEUS,
        reason="apps.api.main requires prometheus_client",
    )
    def test_success_builds_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        s3_repo = MagicMock()
        monkeypatch.setattr("apps.api.main._build_s3_repo", MagicMock(return_value=s3_repo))

        fact_query_cls = MagicMock()
        monkeypatch.setattr("packages.facts.query_service.FactQueryService", fact_query_cls)
        core_cls = MagicMock()
        monkeypatch.setattr("packages.research.lineage.core_adapter.CoreFactProviderImpl", core_cls)

        provider = loader._build_fact_provider()

        assert provider is core_cls.return_value
        core_cls.assert_called_once_with(query_service=fact_query_cls.return_value)


class TestLoadFactRows:
    async def test_no_refs_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        session = _make_session([])

        result = await loader.load_fact_rows(session, uuid4())
        assert result == []

    async def test_provider_none_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: None)
        session = _make_session([_make_ref("src1")])

        result = await loader.load_fact_rows(session, uuid4())
        assert result == []

    async def test_loads_dict_data_with_series(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        provider = MagicMock()
        provider.get_fact_data = AsyncMock(
            return_value={
                "metadata": {"k": "v"},
                "points": [1, 2, 3],
                "series": [
                    {"name": "s1", "columns": ["a"], "rows": list(range(10))},
                    "not-a-dict",
                ],
            }
        )
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: provider)
        ref = _make_ref("src1")
        session = _make_session([ref])

        rows = await loader.load_fact_rows(session, uuid4())

        assert len(rows) == 1
        row = rows[0]
        assert row["source_name"] == "src1"
        assert row["metadata"] == {"k": "v"}
        assert row["points"] == [1, 2, 3]
        assert len(row["series"]) == 1
        assert row["series"][0]["name"] == "s1"
        assert row["series"][0]["rows_sample"] == list(range(5))

    async def test_provider_exception_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        provider = MagicMock()
        provider.get_fact_data = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: provider)
        session = _make_session([_make_ref("src1")])

        rows = await loader.load_fact_rows(session, uuid4())

        assert len(rows) == 1
        assert rows[0]["source_name"] == "src1"

    async def test_non_dict_data_skips_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        provider = MagicMock()
        provider.get_fact_data = AsyncMock(return_value="not-a-dict")
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: provider)
        session = _make_session([_make_ref("src1")])

        rows = await loader.load_fact_rows(session, uuid4())
        assert rows == [{"source_name": "src1"}]


class TestLoadFactSamples:
    async def test_no_refs_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        session = _make_session([])
        assert await loader.load_fact_samples(session, uuid4()) is None

    async def test_provider_none_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: None)
        session = _make_session([_make_ref("src1")])
        assert await loader.load_fact_samples(session, uuid4()) is None

    async def test_loads_samples_preserving_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        provider = MagicMock()
        provider.get_fact_data = AsyncMock(return_value={"p": 1})
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: provider)
        sid = uuid4()
        # source_name empty -> label falls back to str(source_id)
        ref = SimpleNamespace(source_name="", source_id=sid)
        session = _make_session([ref])

        samples = await loader.load_fact_samples(session, uuid4())

        assert samples is not None
        assert samples[0]["label"] == str(sid)
        assert samples[0]["data"] == {"p": 1}

    async def test_exception_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        provider = MagicMock()
        provider.get_fact_data = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: provider)
        session = _make_session([_make_ref("src1")])

        assert await loader.load_fact_samples(session, uuid4()) is None

    async def test_all_non_dict_data_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        provider = MagicMock()
        provider.get_fact_data = AsyncMock(return_value="not-a-dict")
        monkeypatch.setattr(loader, "_build_fact_provider", lambda: provider)
        session = _make_session([_make_ref("src1")])

        assert await loader.load_fact_samples(session, uuid4()) is None


class TestLoadFactContextString:
    async def test_no_samples_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        monkeypatch.setattr(loader, "load_fact_samples", AsyncMock(return_value=None))

        assert await loader.load_fact_context_string(MagicMock(), uuid4()) is None

    async def test_formats_json_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = _make_loader()
        monkeypatch.setattr(
            loader,
            "load_fact_samples",
            AsyncMock(return_value=[{"label": "一", "data": {"x": "要"}}]),
        )

        text = await loader.load_fact_context_string(MagicMock(), uuid4())

        assert "样品: 一" in text
        assert json.dumps({"x": "要"}, ensure_ascii=False) in text
        assert "```json" in text
