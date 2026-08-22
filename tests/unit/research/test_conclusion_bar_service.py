"""Tests for ConclusionBarService (mock sessions; pure helpers + async flows)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.ai.providers import AIResponse
from packages.common.errors import AppError
from packages.research.timeline import conclusion_bar_service as mod
from packages.research.timeline.conclusion_bar_service import ConclusionBarService
from packages.research.timeline.contracts import (
    AssembleFinalConclusionCommand,
    PushBarItemCommand,
)

_AUTO = object()


def _make_session() -> MagicMock:
    session = MagicMock()
    session.added = []

    def add(obj: object) -> None:
        session.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

    session.add = add
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.delete = AsyncMock()
    return session


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actor_id: object | None = _AUTO,
) -> ConclusionBarService:
    actor = uuid4() if actor_id is _AUTO else actor_id
    service = ConclusionBarService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor,  # type: ignore[arg-type]
    )

    @asynccontextmanager
    async def _scoped(self: ConclusionBarService):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(ConclusionBarService, "_scoped_session", _scoped)
    return service


def _patch_repos(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, MagicMock]:
    bar_repo = MagicMock()
    result_repo = MagicMock()
    audit = MagicMock()
    audit.record = AsyncMock()
    monkeypatch.setattr(mod, "ConclusionBarRepository", bar_repo)
    monkeypatch.setattr(mod, "ResultRepository", result_repo)
    monkeypatch.setattr(mod, "AuditRecorder", audit)
    return bar_repo, result_repo, audit


def _item(
    block_type: str,
    snapshot: dict,
    *,
    source: dict | None = None,
    title: str = "t",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        turn_id=uuid4(),
        block_type=block_type,
        title=title,
        content_snapshot=snapshot,
        source_info=source if source is not None else {},
        created_at=None,
    )


class TestRequireActor:
    def test_actor_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(monkeypatch, _make_session(), actor_id=None)
        with pytest.raises(AppError) as exc_info:
            service._require_actor()
        assert exc_info.value.code == "forbidden"


class TestToRef:
    def test_to_ref_without_created_at(self) -> None:
        item = _item("table", {"columns": [], "rows": []})
        ref = ConclusionBarService._to_ref(item)
        assert ref.id == str(item.id)
        assert ref.block_type == "table"
        assert ref.created_at == ""

    def test_to_ref_with_created_at(self) -> None:
        import datetime as _dt

        item = _item("table", {})
        item.created_at = _dt.datetime(2024, 1, 1, tzinfo=_dt.UTC)
        ref = ConclusionBarService._to_ref(item)
        assert ref.created_at == "2024-01-01T00:00:00+00:00"


class TestExtractStructured:
    def _svc(self) -> ConclusionBarService:
        return ConclusionBarService(MagicMock(), uuid4(), uuid4())

    def test_echarts_xy_series(self) -> None:
        item = _item("echarts", {"series": [{"name": "s1", "data": [[1, 2], [3, 4]]}]})
        out = self._svc()._extract_structured(item)
        assert out["series"][0]["columns"] == ["x", "y"]
        assert out["series"][0]["rows"] == [[1, 2], [3, 4]]

    def test_echarts_value_series(self) -> None:
        item = _item("echarts", {"series": [{"name": "s1", "data": [10, 20]}]})
        out = self._svc()._extract_structured(item)
        assert out["series"][0]["columns"] == ["index", "value"]
        assert out["series"][0]["rows"] == [[1, 10], [2, 20]]

    def test_echarts_skips_non_dict_series(self) -> None:
        item = _item("chart_ref", {"series": ["not-a-dict"]})
        out = self._svc()._extract_structured(item)
        assert out["series"] == []

    def test_chart_title_metadata(self) -> None:
        item = _item("echarts", {"title": {"text": "图题"}})
        out = self._svc()._extract_structured(item)
        assert out["metadata"]["chart_title"] == "图题"

    def test_structured_passthrough(self) -> None:
        snapshot = {"metadata": {"a": 1}, "points": [1], "series": [{"x": 1}]}
        out = self._svc()._extract_structured(_item("structured", snapshot))
        assert out["metadata"] == {"a": 1}
        assert out["points"] == [1]
        assert out["series"] == [{"x": 1}]

    def test_table(self) -> None:
        item = _item("table", {"columns": ["c"], "rows": [[1]]})
        out = self._svc()._extract_structured(item)
        assert out["series"][0]["columns"] == ["c"]

    def test_text_dict(self) -> None:
        out = self._svc()._extract_structured(_item("text", {"text": "note"}))
        assert out["metadata"]["note"] == "note"

    def test_text_non_text_dict_uses_json_dump(self) -> None:
        out = self._svc()._extract_structured(_item("text", {"foo": "bar"}))
        assert out["metadata"]["note"] == '{"foo": "bar"}'

    def test_fallback_raw(self) -> None:
        out = self._svc()._extract_structured(_item("unknown", {"custom": 1}))
        assert out["metadata"]["raw"] == {"custom": 1}


class TestMergeStructured:
    def _svc(self) -> ConclusionBarService:
        return ConclusionBarService(MagicMock(), uuid4(), uuid4())

    def test_merge_with_turns(self) -> None:
        items = [
            _item(
                "table",
                {"columns": [], "rows": []},
                source={"question_text": "q1", "turn_number": 2, "run_id": uuid4()},
            ),
            _item(
                "table",
                {"columns": [], "rows": []},
                source={"turn_number": 3},
            ),
        ]
        merged = self._svc()._merge_structured(items, "标题")
        assert merged["metadata"]["title"] == "标题"
        assert merged["metadata"]["source_count"] == 2
        assert merged["metadata"]["analysis_questions"] == ["q1"]
        assert merged["metadata"]["source_turns"] == [2, 3]
        assert "汇总得出以下结论" in merged["metadata"]["summary"]
        assert len(merged["_tracing"]) == 2

    def test_merge_no_turns(self) -> None:
        items = [_item("table", {"columns": [], "rows": []})]
        merged = self._svc()._merge_structured(items, "t")
        assert "基于 1 个分析区块" in merged["metadata"]["summary"]


class TestPushItem:
    async def test_turn_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        session.get = AsyncMock(return_value=None)
        service = _make_service(monkeypatch, session)
        cmd = PushBarItemCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            block_type="table",
            title="t",
            content_snapshot={},
            source_info={},
        )
        with pytest.raises(AppError) as exc_info:
            await service.push_item(cmd)
        assert exc_info.value.code == "not_found"

    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        turn = SimpleNamespace(id=uuid4(), workspace_id=uuid4())
        session.get = AsyncMock(return_value=turn)
        service = _make_service(monkeypatch, session)
        bar_repo, _, audit = _patch_repos(monkeypatch)
        bar_repo.insert_item = AsyncMock(return_value=_item("table", {}))

        cmd = PushBarItemCommand(
            workspace_id=turn.workspace_id,
            turn_id=turn.id,
            block_type="table",
            title="t",
            content_snapshot={"columns": [], "rows": []},
            source_info={},
        )
        ref = await service.push_item(cmd)
        assert ref.block_type == "table"
        bar_repo.insert_item.assert_awaited_once()
        audit.record.assert_awaited_once()


class TestListItems:
    async def test_lists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, _ = _patch_repos(monkeypatch)
        bar_repo.list_items = AsyncMock(return_value=[_item("table", {})])
        out = await service.list_items(uuid4())
        assert len(out["items"]) == 1


class TestRemoveItem:
    async def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, _ = _patch_repos(monkeypatch)
        bar_repo.get_item = AsyncMock(return_value=None)
        with pytest.raises(AppError):
            await service.remove_item(uuid4(), uuid4())

    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, audit = _patch_repos(monkeypatch)
        item = _item("table", {})
        item.workspace_id = uuid4()
        bar_repo.get_item = AsyncMock(return_value=item)
        bar_repo.delete_item = AsyncMock()
        out = await service.remove_item(item.workspace_id, item.id)
        assert out["status"] == "removed"
        audit.record.assert_awaited_once()


class TestAssembleFinalConclusion:
    async def test_count_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, _ = _patch_repos(monkeypatch)
        bar_repo.get_items_by_ids = AsyncMock(return_value=[])
        cmd = AssembleFinalConclusionCommand(
            workspace_id=uuid4(), item_ids=(uuid4(),), title="t", idempotency_key="k"
        )
        with pytest.raises(AppError) as exc_info:
            await service.assemble_final_conclusion(cmd)
        assert exc_info.value.code == "not_found"

    async def test_workspace_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, _ = _patch_repos(monkeypatch)
        item = _item("table", {"columns": [], "rows": []})
        item.workspace_id = uuid4()
        cmd = AssembleFinalConclusionCommand(
            workspace_id=uuid4(), item_ids=(item.id,), title="t", idempotency_key="k"
        )
        bar_repo.get_items_by_ids = AsyncMock(return_value=[item])
        with pytest.raises(AppError) as exc_info:
            await service.assemble_final_conclusion(cmd)
        assert exc_info.value.code == "not_found"

    async def test_success_with_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, audit = _patch_repos(monkeypatch)
        item = _item("table", {"columns": [], "rows": []})
        wid = uuid4()
        item.workspace_id = wid
        bar_repo.get_items_by_ids = AsyncMock(return_value=[item])
        monkeypatch.setattr(service, "_summarize_title", AsyncMock(return_value="LLM标题"))

        cmd = AssembleFinalConclusionCommand(
            workspace_id=wid, item_ids=(item.id,), title="最终标题", idempotency_key="k"
        )
        out = await service.assemble_final_conclusion(cmd)
        assert out["item_count"] == 1
        assert "result_id" in out
        audit.record.assert_awaited_once()

    async def test_success_without_title_uses_summarize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        bar_repo, _, _ = _patch_repos(monkeypatch)
        item = _item("table", {"columns": [], "rows": []})
        wid = uuid4()
        item.workspace_id = wid
        bar_repo.get_items_by_ids = AsyncMock(return_value=[item])
        monkeypatch.setattr(service, "_summarize_title", AsyncMock(return_value="概括标题"))

        cmd = AssembleFinalConclusionCommand(
            workspace_id=wid, item_ids=(item.id,), title="", idempotency_key="k"
        )
        out = await service.assemble_final_conclusion(cmd)
        assert out["item_count"] == 1


class TestPublishConclusion:
    async def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        session.get = AsyncMock(return_value=None)
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch)
        with pytest.raises(AppError):
            await service.publish_conclusion(uuid4(), uuid4(), None, "k")

    async def test_no_revision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        concl = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), current_revision_id=None)
        session.get = AsyncMock(return_value=concl)
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch)
        with pytest.raises(AppError):
            await service.publish_conclusion(concl.workspace_id, concl.id, None, "k")

    async def test_success_json_statement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        concl = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), current_revision_id=uuid4())
        revision = SimpleNamespace(id=uuid4(), statement='{"metadata": {"title": "标题"}}')
        session.get = AsyncMock(side_effect=[concl, revision])
        service = _make_service(monkeypatch, session)
        _, _, audit = _patch_repos(monkeypatch)

        out = await service.publish_conclusion(concl.workspace_id, concl.id, None, "k")
        assert out["version_number"] == 1
        assert "result_id" in out
        audit.record.assert_awaited_once()

    async def test_success_non_json_and_title_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        concl = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), current_revision_id=uuid4())
        revision = SimpleNamespace(id=uuid4(), statement="纯文本")
        session.get = AsyncMock(side_effect=[concl, revision])
        service = _make_service(monkeypatch, session)
        _, _, _ = _patch_repos(monkeypatch)

        out = await service.publish_conclusion(concl.workspace_id, concl.id, "自定义标题", "k")
        assert out["version_number"] == 1


class TestListResults:
    async def test_lists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        r = SimpleNamespace(
            id=uuid4(), name="n", status="published", current_version=1, created_at=None
        )
        result_repo.list_results_by_workspace = AsyncMock(return_value=[r])
        out = await service.list_results(uuid4())
        assert len(out["items"]) == 1


class TestGetResultDetail:
    async def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        result_repo.get_result = AsyncMock(return_value=None)
        with pytest.raises(AppError):
            await service.get_result_detail(uuid4(), uuid4())

    async def test_success_no_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        r = SimpleNamespace(
            id=uuid4(), name="n", status="published", current_version=1, created_at=None
        )
        r.workspace_id = uuid4()
        result_repo.get_result = AsyncMock(return_value=r)
        result_repo.get_latest_result_version = AsyncMock(return_value=None)

        snap_result = MagicMock()
        snap_result.first.return_value = None
        # first execute -> snapshot query (sa.text), return snap_result
        session.execute = AsyncMock(return_value=snap_result)

        out = await service.get_result_detail(r.workspace_id, r.id)
        assert out["version"] is None
        assert out["source_facts"] == []

    async def test_success_with_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        r = SimpleNamespace(
            id=uuid4(), name="n", status="published", current_version=1, created_at=None
        )
        r.workspace_id = uuid4()
        version = SimpleNamespace(
            version_number=1,
            title="t",
            summary='{"k": 1}',
            release_notes=str(uuid4()),
            published_at=None,
            status="active",
        )
        result_repo.get_result = AsyncMock(return_value=r)
        result_repo.get_latest_result_version = AsyncMock(return_value=version)

        snap_result = MagicMock()
        snap_result.first.return_value = None
        session.execute = AsyncMock(return_value=snap_result)

        out = await service.get_result_detail(r.workspace_id, r.id)
        assert out["version"]["version_number"] == 1
        assert out["source_facts"] == []


class TestResultStatusMutations:
    async def test_withdraw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        r = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), status="published")
        result_repo.get_result = AsyncMock(return_value=r)
        await service.withdraw_result(r.workspace_id, r.id)
        assert r.status == "withdrawn"

    async def test_republish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        r = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), status="withdrawn")
        result_repo.get_result = AsyncMock(return_value=r)
        await service.republish_result(r.workspace_id, r.id)
        assert r.status == "published"

    async def test_delete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _, result_repo, _ = _patch_repos(monkeypatch)
        r = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), status="published")
        result_repo.get_result = AsyncMock(return_value=r)
        await service.delete_result(r.workspace_id, r.id)
        session.delete.assert_awaited_once()


class TestSummarizeTitle:
    def test_no_context_returns_fallback(self) -> None:
        svc = ConclusionBarService(MagicMock(), uuid4(), uuid4())
        import asyncio

        result = asyncio.run(svc._summarize_title({"metadata": {"title": "回退标题"}}))
        assert result == "回退标题"

    def test_non_str_fallback(self) -> None:
        svc = ConclusionBarService(MagicMock(), uuid4(), uuid4())
        import asyncio

        result = asyncio.run(svc._summarize_title({"metadata": {"title": 123}}))
        assert result == "最终结论"

    async def test_no_ai_config_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = ConclusionBarService(MagicMock(), uuid4(), uuid4())
        monkeypatch.setattr(
            "packages.ai.yaml_config.get_scenario_config",
            MagicMock(side_effect=FileNotFoundError("no config")),
        )
        assembled = {
            "metadata": {"analysis_questions": ["问题"], "summary": "摘要"},
            "_tracing": [{"title": "区块"}],
        }
        assert await svc._summarize_title(assembled) == "最终结论"

    async def test_success_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from packages.ai.yaml_config import ScenarioConfig

        svc = ConclusionBarService(MagicMock(), uuid4(), uuid4())
        config = ScenarioConfig(
            provider_name="test",
            base_url="http://x",
            api_key="k",
            model="m",
            thinking_enabled=False,
        )
        monkeypatch.setattr(
            "packages.ai.yaml_config.get_scenario_config",
            MagicMock(return_value=config),
        )
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=AIResponse(answer="概栆标题。"))
        monkeypatch.setattr(
            "packages.ai.openai_compatible.OpenAICompatibleProvider",
            MagicMock(return_value=provider),
        )
        assembled = {"metadata": {"analysis_questions": ["问题"]}}
        result = await svc._summarize_title(assembled)
        assert result == "概栆标题"

    async def test_exception_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = ConclusionBarService(MagicMock(), uuid4(), uuid4())
        monkeypatch.setattr(
            "packages.ai.yaml_config.get_scenario_config",
            MagicMock(side_effect=RuntimeError("x")),
        )
        assembled = {"metadata": {"analysis_questions": ["问题"]}}
        assert await svc._summarize_title(assembled) == "最终结论"
