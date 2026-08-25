"""Conclusion bar service: push / list / remove / assemble final conclusion.

Mirrors ConclusionService (ScopedSessionMixin, session_factory + dept_id +
actor_id).  The assemble step merges checked bar items into a single
structured JSON ``{metadata, points, series, _tracing}`` and persists it as a
ResearchConclusion (source_type="assembled") with one ResearchConclusionRevision
whose ``statement`` holds the assembled JSON.

Key invariants:
  - push_item validates turn ownership before insert.
  - remove_item validates workspace ownership before delete.
  - assemble_final_conclusion loads checked items, normalises each to
    {metadata, points, series}, merges them, and records full tracing.
  - All mutations are audited via AuditRecorder.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.entities import ResearchResult, ResearchResultVersion
from packages.research.repository.result import ResultRepository
from packages.research.timeline.conclusion_bar_repository import ConclusionBarRepository
from packages.research.timeline.contracts import (
    AssembleFinalConclusionCommand,
    BarItemRef,
    PushBarItemCommand,
)
from packages.research.timeline.entities import (
    ResearchConclusion,
    ResearchConclusionBarItem,
    ResearchConclusionRevision,
    ResearchTurn,
)

logger = logging.getLogger("research.conclusion_bar_service")


class ConclusionBarService(ScopedSessionMixin):
    """Service for the conclusion bar: push / list / remove / assemble.

    Depends on session_factory, department_id, actor_id (same as
    ConclusionService).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
    ) -> None:
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._rls_dept_id: UUID | None = None

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._factory

    def _require_actor(self) -> UUID:
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    # ============================================================
    # Public API
    # ============================================================

    async def push_item(self, command: PushBarItemCommand) -> BarItemRef:
        """Push a report block snapshot to the conclusion bar.

        Validates that the turn belongs to the workspace, then inserts a
        bar item row and audits the action.

        Args:
            command: PushBarItemCommand with snapshot + source_info.

        Returns:
            BarItemRef for the inserted item.

        Raises:
            AppError: not_found if turn doesn't belong to workspace.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Verify turn belongs to workspace
            turn = await session.get(ResearchTurn, command.turn_id)
            if turn is None or turn.workspace_id != command.workspace_id:
                raise AppError(
                    code="not_found",
                    message="轮次不存在或不属于该工作空间",
                    retryable=False,
                    fields={"turn_id": str(command.turn_id)},
                )

            # 2. Insert bar item
            item = await ConclusionBarRepository.insert_item(
                session,
                workspace_id=command.workspace_id,
                turn_id=command.turn_id,
                block_type=command.block_type,
                title=command.title,
                content_snapshot=command.content_snapshot,
                source_info=command.source_info,
                created_by=actor_id,
            )

            # 3. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion_bar.push",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion_bar_item",
                    resource_id=item.id,
                    payload={
                        "block_type": command.block_type,
                        "turn_id": str(command.turn_id),
                    },
                ),
            )

            return self._to_ref(item)

    async def list_items(self, workspace_id: UUID) -> dict[str, Any]:
        """List all bar items for a workspace (newest first).

        Args:
            workspace_id: Workspace ID.

        Returns:
            Dict with "items" list of BarItemRef-shaped dicts.
        """
        async with self._scoped_session() as session:
            items = await ConclusionBarRepository.list_items(session, workspace_id)
            return {"items": [self._to_ref(item).to_dict() for item in items]}

    async def remove_item(
        self,
        workspace_id: UUID,
        item_id: UUID,
    ) -> dict[str, Any]:
        """Remove a bar item (hard delete).

        Validates workspace ownership before delete.

        Args:
            workspace_id: Workspace ID for ownership check.
            item_id: Bar item ID to remove.

        Returns:
            Dict with item_id and status.

        Raises:
            AppError: not_found if item doesn't exist or doesn't belong to ws.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            item = await ConclusionBarRepository.get_item(session, item_id)
            if item is None or item.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="结论栏条目不存在",
                    retryable=False,
                    fields={"item_id": str(item_id)},
                )

            await ConclusionBarRepository.delete_item(session, item_id)

            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion_bar.remove",
                    actor_user_id=actor_id,
                    resource_type="research_conclusion_bar_item",
                    resource_id=item_id,
                ),
            )

        return {"item_id": str(item_id), "status": "removed"}

    async def assemble_final_conclusion(
        self,
        command: AssembleFinalConclusionCommand,
    ) -> dict[str, Any]:
        """Assemble checked bar items into a final conclusion.

        Loads items by IDs, validates they all belong to the workspace,
        normalises each to {metadata, points, series}, merges them into a
        single structured JSON, and persists as a ResearchConclusion
        (source_type="assembled") with one ResearchConclusionRevision whose
        ``statement`` holds the assembled JSON.

        Args:
            command: AssembleFinalConclusionCommand.

        Returns:
            Dict with conclusion_id, statement, item_count.

        Raises:
            AppError: not_found if any item is missing or cross-workspace.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Load checked items (preserves input order)
            item_ids = list(command.item_ids)
            items = await ConclusionBarRepository.get_items_by_ids(session, item_ids)

            # 2. Validate count + workspace ownership
            if len(items) != len(item_ids):
                raise AppError(
                    code="not_found",
                    message="部分结论栏条目不存在",
                    retryable=False,
                    fields={},
                )
            for it in items:
                if it.workspace_id != command.workspace_id:
                    raise AppError(
                        code="not_found",
                        message="结论栏条目不属于该工作空间",
                        retryable=False,
                        fields={"item_id": str(it.id)},
                    )

            # 3. Normalise + merge
            assembled = self._merge_structured(items, command.title)

            # 3.5 LLM 概括标题
            result_name = command.title or await self._summarize_title(assembled)
            assembled.setdefault("metadata", {})["title"] = result_name

            # 4. 直接生成 ResearchResult（跳过 Conclusion 中间步骤）
            statement = json.dumps(assembled, ensure_ascii=False)
            content_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()

            result = ResearchResult(
                workspace_id=command.workspace_id,
                owner_user_id=actor_id,
                name=result_name,
                status="published",
                current_version=0,
                current_acl_type="private",
                current_explicit_user_ids=[],
                lock_version=0,
            )
            session.add(result)
            await session.flush()

            version = ResearchResultVersion(
                result_id=result.id,
                version_number=1,
                title=result_name,
                summary=statement,
                tags=[],
                release_notes="",
                dataset_version_refs=[],
                view_version_refs=[],
                insight_version_refs=[],
                evidence_snapshot_ids=[],
                analysis_run_ids=[],
                source_run_statuses={},
                publisher=actor_id,
                content_hash=content_hash,
                published_permission_envelope={},
                status="active",
            )
            session.add(version)
            await session.flush()

            await session.execute(
                sa.update(ResearchResult)
                .where(ResearchResult.id == result.id)
                .values(current_version=1, updated_at=sa.func.now())
            )

            # 5. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.assemble",
                    actor_user_id=actor_id,
                    resource_type="research_result",
                    resource_id=result.id,
                    payload={
                        "item_count": len(items),
                        "idempotency_key": command.idempotency_key,
                    },
                ),
            )

        return {
            "result_id": str(result.id),
            "statement": statement,
            "item_count": len(items),
        }

    # ============================================================
    # Publish & Results (Requirement 3)
    # ============================================================

    async def publish_conclusion(
        self,
        workspace_id: UUID,
        conclusion_id: UUID,
        title: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Publish a ResearchConclusion as a simplified ResearchResult.

        Creates a ResearchResult (status="published") with one
        ResearchResultVersion (version_number=1) whose ``summary`` holds the
        parsed structured data from the conclusion's current revision statement.
        Does NOT go through the full PublicationService flow.

        Args:
            workspace_id: Workspace ID for ownership check.
            conclusion_id: The conclusion to publish.
            title: Optional result title (falls back to metadata.title or "最终结论").
            idempotency_key: Client-supplied idempotency key.

        Returns:
            Dict with result_id and version_number.

        Raises:
            AppError: not_found if conclusion or its revision is missing.
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. Load conclusion + verify workspace ownership
            conclusion = await session.get(ResearchConclusion, conclusion_id)
            if conclusion is None or conclusion.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="结论不存在或不属于该工作空间",
                    retryable=False,
                    fields={"conclusion_id": str(conclusion_id)},
                )

            # 2. Load current revision
            revision: ResearchConclusionRevision | None = None
            if conclusion.current_revision_id:
                revision = await session.get(
                    ResearchConclusionRevision,
                    conclusion.current_revision_id,
                )
            if revision is None:
                raise AppError(
                    code="not_found",
                    message="结论没有有效版本",
                    retryable=False,
                    fields={"conclusion_id": str(conclusion_id)},
                )

            # 3. Parse revision.statement as JSON (if possible)
            statement_text = revision.statement
            try:
                statement_json: Any = json.loads(statement_text)
            except (json.JSONDecodeError, TypeError):
                statement_json = {"statement": statement_text}

            # 4. Determine result name (title param > metadata.title > "最终结论")
            default_title = "最终结论"
            if isinstance(statement_json, dict):
                meta = statement_json.get("metadata")
                if isinstance(meta, dict) and meta.get("title"):
                    default_title = str(meta["title"])
            result_name = title or default_title

            # 5. Create ResearchResult (stable identity)
            result = ResearchResult(
                workspace_id=workspace_id,
                owner_user_id=actor_id,
                name=result_name,
                status="published",
                current_version=0,
                current_acl_type="private",
                current_explicit_user_ids=[],
                lock_version=0,
            )
            session.add(result)
            await session.flush()

            # 6. Create ResearchResultVersion (v1, immutable)
            summary_str = json.dumps(statement_json, ensure_ascii=False)
            content_hash = hashlib.sha256(summary_str.encode("utf-8")).hexdigest()
            version = ResearchResultVersion(
                result_id=result.id,
                version_number=1,
                title=result_name,
                summary=summary_str,
                tags=[],
                release_notes=str(conclusion_id),
                dataset_version_refs=[],
                view_version_refs=[],
                insight_version_refs=[],
                evidence_snapshot_ids=[],
                analysis_run_ids=[],
                source_run_statuses={},
                publisher=actor_id,
                content_hash=content_hash,
                published_permission_envelope={},
                status="active",
            )
            session.add(version)
            await session.flush()

            # 7. Update ResearchResult.current_version = 1
            await session.execute(
                sa.update(ResearchResult)
                .where(ResearchResult.id == result.id)
                .values(current_version=1, updated_at=sa.func.now())
            )

            # 8. Audit
            await AuditRecorder.record(
                session,
                AuditEventData(
                    department_id=self._dept_id,
                    action="research.conclusion.publish",
                    actor_user_id=actor_id,
                    resource_type="research_result",
                    resource_id=result.id,
                    payload={
                        "conclusion_id": str(conclusion_id),
                        "idempotency_key": idempotency_key,
                    },
                ),
            )

        return {
            "result_id": str(result.id),
            "version_number": 1,
        }

    async def list_results(self, workspace_id: UUID) -> dict[str, Any]:
        """List all ResearchResults for a workspace (newest first).

        Args:
            workspace_id: Workspace ID.

        Returns:
            Dict with "items" list of {id, name, status, current_version, created_at}.
        """
        async with self._scoped_session() as session:
            results = await ResultRepository.list_results_by_workspace(session, workspace_id)
            return {
                "items": [
                    {
                        "id": str(r.id),
                        "name": r.name,
                        "status": r.status,
                        "current_version": r.current_version,
                        "current_acl_type": r.current_acl_type,
                        "created_at": r.created_at.isoformat() if r.created_at else "",
                    }
                    for r in results
                ]
            }

    async def get_result_detail(
        self,
        workspace_id: UUID,
        result_id: UUID,
    ) -> dict[str, Any]:
        """Get a single ResearchResult detail + latest version summary.

        Args:
            workspace_id: Workspace ID for ownership check.
            result_id: ResearchResult ID.

        Returns:
            Dict with result fields + version detail (parsed structured data).

        Raises:
            AppError: not_found if result doesn't exist or cross-workspace.
        """
        async with self._scoped_session() as session:
            result = await ResultRepository.get_result(session, result_id)
            if result is None or result.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="成果不存在或不属于该工作空间",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )

            version = await ResultRepository.get_latest_result_version(session, result_id)

            # Parse version.summary as structured data (if possible)
            summary_data: Any = None
            source_conclusion_id = ""
            if version is not None:
                if version.summary:
                    try:
                        summary_data = json.loads(version.summary)
                    except (json.JSONDecodeError, TypeError):
                        summary_data = {"statement": version.summary}
                # release_notes stores the source conclusion_id
                if version.release_notes:
                    source_conclusion_id = version.release_notes

            # 提取 workspace 最新快照中的 fact 列表（用于展示"用到的数据"）
            source_facts: list[dict[str, Any]] = []
            snap_row = await session.execute(
                sa.text(
                    "SELECT source_refs, field_manifest FROM research_evidence_snapshot "
                    "WHERE workspace_id = :wid ORDER BY snapshot_number DESC LIMIT 1"
                ),
                {"wid": str(workspace_id)},
            )
            snap = snap_row.first()
            if snap is not None:
                refs = snap[0] if snap[0] else []
                manifest = snap[1] if snap[1] else {}
                for ref in refs:
                    fid = str(ref.get("id", ""))
                    if not fid:
                        continue
                    # 先从 fact 表查名称，查不到用 field_manifest 的第一个字段名
                    fact_row = await session.execute(
                        sa.text(
                            "SELECT subject_id, task_name, equipment_name, "
                            "run_operator FROM fact "
                            "WHERE id = :fid LIMIT 1"
                        ),
                        {"fid": fid},
                    )
                    fr = fact_row.first()
                    if fr is not None:
                        source_facts.append(
                            {
                                "fact_id": fid,
                                "name": fr[0] or fid[:8],
                                "task_name": fr[1] or "",
                                "equipment_name": fr[2] or "",
                                "operator": fr[3] or "",
                                "data_summary": "",
                            }
                        )
                    else:
                        # fact 表无记录，用 field_manifest 的第一个字段名做名称
                        fm_val = manifest.get(fid, []) if isinstance(manifest, dict) else []
                        first_field = fm_val[0] if isinstance(fm_val, list) and fm_val else fid[:8]
                        source_facts.append(
                            {
                                "fact_id": fid,
                                "name": first_field,
                                "task_name": "",
                                "equipment_name": "",
                            }
                        )

            return {
                "id": str(result.id),
                "name": result.name,
                "status": result.status,
                "current_version": result.current_version,
                "current_acl_type": result.current_acl_type,
                "created_at": result.created_at.isoformat() if result.created_at else "",
                "source_facts": source_facts,
                "version": {
                    "version_number": version.version_number if version else 0,
                    "title": version.title if version else "",
                    "summary": summary_data,
                    "source_conclusion_id": source_conclusion_id,
                    "published_at": version.published_at.isoformat()
                    if version and version.published_at
                    else "",
                    "status": version.status if version else "",
                }
                if version
                else None,
            }

    async def withdraw_result(self, workspace_id: UUID, result_id: UUID) -> None:
        """Withdraw a published result (status -> withdrawn)."""
        async with self._scoped_session() as session:
            result = await ResultRepository.get_result(session, result_id)
            if result is None or result.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="成果不存在或不属于该工作空间",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )
            result.status = "withdrawn"
            await session.commit()

    async def republish_result(self, workspace_id: UUID, result_id: UUID) -> None:
        """Re-publish a withdrawn result (status -> published)."""
        async with self._scoped_session() as session:
            result = await ResultRepository.get_result(session, result_id)
            if result is None or result.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="成果不存在或不属于该工作空间",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )
            result.status = "published"
            await session.commit()

    async def delete_result(self, workspace_id: UUID, result_id: UUID) -> None:
        """Delete a result permanently."""
        async with self._scoped_session() as session:
            result = await ResultRepository.get_result(session, result_id)
            if result is None or result.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="成果不存在或不属于该工作空间",
                    retryable=False,
                    fields={"result_id": str(result_id)},
                )
            await session.delete(result)
            await session.commit()

    # ============================================================
    # Internal helpers
    # ============================================================

    async def _summarize_title(self, assembled: dict[str, Any]) -> str:
        """根据结构化数据直接生成简短标题（不调 LLM）。

        规则：
        - chart/echarts 类型：优先用 chart_title（ECharts option 里的 title.text）
        - text 类型：用 content_snapshot 里的文本前 20 字
        - 其他：用 item.title（推送到结论栏时的区块标题）
        - 多个 item 时取第一个有意义的标题

        Args:
            assembled: 组装后的 {metadata, points, series, _tracing} dict。

        Returns:
            简短标题字符串（≤ 20 字）。
        """
        fallback = assembled.get("metadata", {}).get("title", "") or "最终结论"
        if not isinstance(fallback, str):
            fallback = "最终结论"

        meta = assembled.get("metadata", {})
        tracing = assembled.get("_tracing", [])

        # 1. 优先用 chart_title（echarts option 里的 title.text）
        chart_title = meta.get("chart_title", "")
        if isinstance(chart_title, str) and chart_title.strip():
            title = chart_title.strip()
            if len(title) > 20:
                title = title[:20]
            return title

        # 2. 遍历 tracing 找第一个有意义的标题
        for t in tracing:
            if not isinstance(t, dict):
                continue
            block_type = t.get("block_type", "")
            item_title = t.get("title", "")
            question_text = t.get("question_text", "")
            snapshot = t.get("content_snapshot", {})

            # chart 类型用 item_title（echarts 的 title 通常有意义）
            if block_type in ("echarts", "chart_ref", "chart"):
                if item_title and isinstance(item_title, str) and item_title.strip():
                    title = item_title.strip()
                    return str(title[:20]) if len(title) > 20 else str(title)

            # table 类型：优先用 preceding_text（表格前面的文字），回退到 question_text
            if block_type == "table":
                preceding = t.get("preceding_text", "")
                if preceding and isinstance(preceding, str) and preceding.strip():
                    title = preceding.strip()
                    return str(title[:20]) if len(title) > 20 else str(title)
                if question_text and isinstance(question_text, str) and question_text.strip():
                    title = question_text.strip()
                    return str(title[:20]) if len(title) > 20 else str(title)

            # text 类型：用 content_snapshot 里的文本前 20 字
            if block_type == "text":
                text_val = ""
                if isinstance(snapshot, dict):
                    text_val = snapshot.get("text", "")
                elif isinstance(snapshot, str):
                    text_val = snapshot
                if text_val and isinstance(text_val, str) and text_val.strip():
                    title = text_val.strip()
                    return str(title[:20]) if len(title) > 20 else str(title)
            return str(item_title)

        # 3. 用 analysis_questions 的第一个问题
        questions = meta.get("analysis_questions", [])
        if questions and isinstance(questions[0], str) and questions[0].strip():
            q = questions[0].strip()
            if len(q) > 20:
                return q[:20]
            return q

        return fallback

    @staticmethod
    def _to_ref(item: ResearchConclusionBarItem) -> BarItemRef:
        """Convert an ORM row to a BarItemRef (stringified fields)."""
        created_at = item.created_at
        created_str = created_at.isoformat() if created_at else ""
        return BarItemRef(
            id=str(item.id),
            workspace_id=str(item.workspace_id),
            turn_id=str(item.turn_id),
            block_type=item.block_type,
            title=item.title,
            content_snapshot=dict(item.content_snapshot or {}),
            source_info=dict(item.source_info or {}),
            created_at=created_str,
        )

    def _extract_structured(
        self,
        item: ResearchConclusionBarItem,
    ) -> dict[str, Any]:
        """Normalise a bar item to a {metadata, points, series} structure.

        - echarts/chart_ref: pull series[].data into the ``series`` array,
          each entry named after the series name with columns/data.
        - structured: already {metadata, points, series} — pass through.
        - table: {columns, rows} → a single series entry.
        - text: stored as a metadata note.
        """
        snapshot: dict[str, Any] = dict(item.content_snapshot or {})
        block_type = item.block_type
        result: dict[str, Any] = {"metadata": {}, "points": [], "series": []}

        if block_type in ("echarts", "chart_ref"):
            series_list = snapshot.get("series")
            if isinstance(series_list, list):
                for s in series_list:
                    if not isinstance(s, dict):
                        continue
                    name = s.get("name") or item.title
                    data = s.get("data")
                    # echarts series data is typically [value] or [[x, y], ...]
                    columns: list[str] = []
                    rows: list[Any] = []
                    if isinstance(data, list):
                        if data and isinstance(data[0], list):
                            # [[x, y], ...]
                            columns = ["x", "y"]
                            rows = [list(r) for r in data if isinstance(r, list)]
                        else:
                            # [v1, v2, ...]
                            columns = ["index", "value"]
                            rows = [[i + 1, v] for i, v in enumerate(data)]
                    result["series"].append({"name": str(name), "columns": columns, "rows": rows})
            # chart title as metadata
            title = snapshot.get("title")
            if isinstance(title, dict) and title.get("text"):
                result["metadata"]["chart_title"] = title["text"]
            return result

        if block_type == "structured":
            metadata = snapshot.get("metadata")
            if isinstance(metadata, dict):
                result["metadata"] = metadata
            points = snapshot.get("points")
            if isinstance(points, list):
                result["points"] = points
            series = snapshot.get("series")
            if isinstance(series, list):
                result["series"] = series
            return result

        if block_type == "table":
            tbl_columns: list[Any] = list(snapshot.get("columns") or [])
            tbl_rows: list[Any] = list(snapshot.get("rows") or [])
            result["series"].append(
                {
                    "name": item.title,
                    "columns": tbl_columns,
                    "rows": tbl_rows,
                }
            )
            return result

        if block_type == "text":
            # text snapshot is wrapped as {"text": "..."}; extract the note
            if isinstance(snapshot, dict) and "text" in snapshot:
                text_val = snapshot["text"]
            elif isinstance(snapshot, str):
                text_val = snapshot
            else:
                text_val = json.dumps(snapshot, ensure_ascii=False)
            result["metadata"]["note"] = text_val
            return result

        # Fallback: keep snapshot as-is in metadata
        result["metadata"]["raw"] = snapshot
        return result

    def _merge_structured(
        self,
        items: list[ResearchConclusionBarItem],
        title: str,
    ) -> dict[str, Any]:
        """Merge multiple bar items into one assembled structure.

        Combines metadata (deduped keys), concatenates points and series,
        and builds a ``_tracing`` array with each item's provenance.

        Enhanced metadata (Requirement 2):
          - ``analysis_questions``: deduped list of question_text from source_info.
          - ``source_turns``: deduped list of turn_number from source_info.
          - ``source_runs``: deduped list of run_id from source_info (if present).
          - ``summary``: auto-generated one-line description, e.g.
            "基于 3 个分析区块（来自轮次 #1、#2），汇总得出以下结论。"
          - ``_tracing`` remains at the outermost level (not inside metadata).
        """
        merged_metadata: dict[str, Any] = {"title": title}
        merged_points: list[Any] = []
        merged_series: list[Any] = []
        tracing: list[dict[str, Any]] = []
        analysis_questions: list[str] = []
        source_turns: list[int] = []
        source_runs: list[str] = []

        for item in items:
            extracted = self._extract_structured(item)
            # Merge metadata (later items don't overwrite earlier title)
            for k, v in extracted["metadata"].items():
                if k == "title":
                    continue
                merged_metadata.setdefault(k, v)
            merged_points.extend(extracted["points"])
            merged_series.extend(extracted["series"])

            source = dict(item.source_info or {})

            # Extract analysis question text (deduped, order-preserved)
            question_text = source.get("question_text")
            if isinstance(question_text, str) and question_text.strip():
                if question_text not in analysis_questions:
                    analysis_questions.append(question_text)

            # Extract turn numbers (deduped)
            turn_number = source.get("turn_number")
            if isinstance(turn_number, int) and turn_number not in source_turns:
                source_turns.append(turn_number)

            # Extract run IDs (deduped, only if present)
            run_id = source.get("run_id")
            if run_id is not None:
                run_id_str = str(run_id)
                if run_id_str not in source_runs:
                    source_runs.append(run_id_str)

            tracing.append(
                {
                    "bar_item_id": str(item.id),
                    "turn_number": source.get("turn_number"),
                    "block_type": item.block_type,
                    "title": item.title,
                    "question_text": source.get("question_text", ""),
                    "preceding_text": source.get("preceding_text", ""),
                    "content_snapshot": dict(item.content_snapshot or {}),
                }
            )

        merged_metadata["source_count"] = len(items)
        merged_metadata["assembled_at"] = datetime.now(UTC).isoformat()
        merged_metadata["analysis_questions"] = analysis_questions
        merged_metadata["source_turns"] = source_turns
        merged_metadata["source_runs"] = source_runs

        # Auto-generate summary (pure string concatenation, no LLM)
        sorted_turns = sorted(source_turns)
        turns_str = "、".join(f"#{t}" for t in sorted_turns)
        if turns_str:
            merged_metadata["summary"] = (
                f"基于 {len(items)} 个分析区块（来自轮次 {turns_str}），汇总得出以下结论。"
            )
        else:
            merged_metadata["summary"] = f"基于 {len(items)} 个分析区块，汇总得出以下结论。"

        return {
            "metadata": merged_metadata,
            "points": merged_points,
            "series": merged_series,
            "_tracing": tracing,
        }
