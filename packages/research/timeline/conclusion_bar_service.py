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
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin, build_session_factory
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
                    fm = manifest.get(fid, {}) if isinstance(manifest, dict) else {}
                    if not isinstance(fm, dict):
                        fm = {}
                    source_facts.append(
                        {
                            "fact_id": fid,
                            "name": fm.get("name", fm.get("task_name", fid[:8])),
                            "task_name": fm.get("task_name", ""),
                            "equipment_name": fm.get("equipment_name", ""),
                        }
                    )

            return {
                "id": str(result.id),
                "name": result.name,
                "status": result.status,
                "current_version": result.current_version,
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

    async def withdraw_result(
        self, workspace_id: UUID, result_id: UUID
    ) -> None:
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

    async def republish_result(
        self, workspace_id: UUID, result_id: UUID
    ) -> None:
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

    async def delete_result(
        self, workspace_id: UUID, result_id: UUID
    ) -> None:
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
        """用 LLM 根据结构化数据概括一个简短标题。

        Args:
            assembled: 组装后的 {metadata, points, series, _tracing} dict。

        Returns:
            简短标题字符串（≤ 30 字）。LLM 调用失败时回退到 metadata.title 或 "最终结论"。
        """
        fallback = assembled.get("metadata", {}).get("title", "最终结论")
        if not isinstance(fallback, str):
            fallback = "最终结论"

        # 提取关键信息供 LLM 概括
        meta = assembled.get("metadata", {})
        questions = meta.get("analysis_questions", [])
        summary = meta.get("summary", "")
        tracing = assembled.get("_tracing", [])
        titles = [t.get("title", "") for t in tracing if isinstance(t, dict)]

        # 构建简短 prompt
        context_parts = []
        if questions:
            context_parts.append(f"分析问题: {'; '.join(questions[:3])}")
        if summary:
            context_parts.append(f"摘要: {summary}")
        if titles:
            context_parts.append(f"区块标题: {'; '.join(titles[:5])}")
        if not context_parts:
            return fallback

        prompt = (
            "请根据以下研究分析内容，概括一个简短的结论标题（不超过20个汉字，"
            "不要加引号、不要加句号，直接输出标题文本）：\n\n" + "\n".join(context_parts)
        )

        try:
            ai_config = await self._load_ai_config()
            if not ai_config:
                return fallback

            from packages.ai.openai_compatible import OpenAICompatibleProvider
            from packages.ai.providers import AIRequest

            model_name = ai_config.get("research_model_name") or ai_config.get("model_name", "")
            provider = OpenAICompatibleProvider(
                api_key=ai_config["api_key"],
                base_url=ai_config["base_url"],
                model=model_name,
                thinking_enabled=False,
            )
            request = AIRequest(
                messages=(
                    {
                        "role": "system",
                        "content": "你是一个标题概括助手，根据研究内容生成简短标题。",
                    },
                    {"role": "user", "content": prompt},
                ),
            )
            response = await provider.complete(request)
            title = response.content.strip()
            # 清理：去引号、去句号、限制长度
            title = title.rstrip("。.")
            title = title.strip("'")
            title = title.strip('"')
            title = title.strip("「")
            title = title.strip("」")
            if len(title) > 30:
                title = title[:30]
            return title or fallback
        except Exception:
            logger.warning("LLM summarize title failed, using fallback")
            return fallback

    async def _load_ai_config(self) -> dict[str, Any] | None:
        """Load AI config from database."""
        db_url = os.environ.get(
            "IRIP_DATABASE_URL",
            "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
        )
        factory = build_session_factory(db_url)
        async with factory() as session:
            result = await session.execute(
                sa.text(
                    "SELECT base_url, api_key, model_name, "
                    "research_model_name, research_thinking_enabled "
                    "FROM ai_config WHERE enabled = true "
                    "ORDER BY updated_at DESC LIMIT 1"
                )
            )
            row = result.first()
            if row is None:
                return None
            from packages.common.crypto import EnvelopeCrypto

            crypto = EnvelopeCrypto.from_env()
            decrypted_key = crypto.decrypt(row[1])
            return {
                "base_url": row[0],
                "api_key": decrypted_key,
                "model_name": row[2],
                "research_model_name": row[3],
                "research_thinking_enabled": row[4],
            }

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
            columns = snapshot.get("columns")
            rows = snapshot.get("rows")
            result["series"].append(
                {
                    "name": item.title,
                    "columns": list(columns) if isinstance(columns, list) else [],
                    "rows": list(rows) if isinstance(rows, list) else [],
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
