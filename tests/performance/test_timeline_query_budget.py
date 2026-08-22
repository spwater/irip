"""时间线列表查询数据库往返预算（Program Gate 5 Step 1）。

契约：20 个 Turn 的列表查询（``TimelineQueryService.list_timeline``）
数据访问往返不超过 8 次。两阶段加载（Phase 1 keyset 取 Turn ID +
Phase 2 批量加载 card 元数据）避免 N+1，因此往返次数应与 Turn 数量
无关且处于常量预算内。

计数方式：SQLAlchemy ``before_cursor_execute`` 事件统计真实发送到数据库
的数据语句。租户 RLS 的 GUC 样板（``SET LOCAL app.current_*`` 与
``SELECT quote_literal(...)``）是每次调用的常量开销、与列表数据无关，
不计入"列表查询往返"预算（否则会引入与 N 无关的固定 +4，掩盖真实的
N+1 退化）。测试额外断言数据往返与 Turn 数量无关（1 vs 20 相同）。

DB 依赖：``IRIP_TEST_DATABASE_URL``（tests/conftest.py 的 ``sync_engine`` /
``async_session_factory`` fixture）；未设置时 skip（环境原因，非空壳）。
"""

from __future__ import annotations

from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.common.ids import new_id
from packages.research.entities import ResearchEvidenceSnapshot, ResearchWorkspace
from packages.research.timeline.entities import ResearchTurn
from packages.research.timeline.timeline_query_service import TimelineQueryService

#: 列表查询数据往返预算（Program Gate 5 Step 1）。
QUERY_BUDGET = 8


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


def _is_data_query(statement: str) -> bool:
    """判断一条 statement 是否属于"列表查询数据往返"。

    排除租户 RLS 的 GUC 样板：``SET LOCAL ...`` 与 ``SELECT quote_literal(...)``。
    """
    s = statement.strip().lower()
    if s.startswith("set "):
        return False
    if "quote_literal" in s:
        return False
    return True


async def _seed_workspace_with_turns(
    factory: async_sessionmaker,
    user,
    num_turns: int,
) -> UUID:
    """插入一个 workspace + 一个 snapshot + num_turns 个 Turn，返回 workspace_id。"""
    owner_id: UUID = user.user_id
    dept_id: UUID = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="query-budget-test",
            )
            session.add(ws)
            await session.flush()
            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash="0" * 64,
                permission_envelope={},
                field_manifest={},
                source_refs=[],
                created_by=owner_id,
            )
            session.add(snap)
            await session.flush()
            for i in range(1, num_turns + 1):
                session.add(
                    ResearchTurn(
                        id=new_id(),
                        workspace_id=ws.id,
                        turn_number=i,
                        kind="analysis",
                        status="succeeded",
                        question_text_snapshot=f"question-{i}",
                        question_origin="manual",
                        evidence_snapshot_id=snap.id,
                        idempotency_key=f"qb-{ws.id}-{i}",
                    )
                )
            await session.flush()
            return ws.id


async def _cleanup_workspace(factory: async_sessionmaker, workspace_id: UUID) -> None:
    """删除 workspace（CASCADE 清理 snapshot/turn）。"""
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :id"),
                {"id": workspace_id},
            )


async def _data_round_trips_for_list(
    factory: async_sessionmaker,
    user,
    workspace_id: UUID,
) -> tuple[int, list[str], int]:
    """在事件钩子下执行一次 list_timeline，返回 (数据往返次数, 语句列表, 返回条目数)。"""
    statements: list[str] = []

    def _on_execute(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ARG001
        statements.append(statement)

    engine = factory.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        service = TimelineQueryService(
            factory,
            department_id=user.department_id,
            actor_id=user.user_id,
        )
        page = await service.list_timeline(workspace_id, page_size=20)
        item_count = len(page.items)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)

    data_queries = [s for s in statements if _is_data_query(s)]
    return len(data_queries), data_queries, item_count


@pytest.mark.integration
async def test_20_turns_list_within_query_budget(sync_engine, test_user) -> None:
    """20 个 Turn 的列表查询数据往返 ≤ 8。"""
    async_url = _to_async_url(sync_engine.url.render_as_string(hide_password=False))
    engine = create_async_engine(async_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    workspace_id = await _seed_workspace_with_turns(factory, test_user, num_turns=20)
    try:
        round_trips, statements, item_count = await _data_round_trips_for_list(
            factory, test_user, workspace_id
        )
        assert item_count == 20
        assert round_trips <= QUERY_BUDGET, (
            f"list_timeline 数据往返 {round_trips} > 预算 {QUERY_BUDGET}:\n" + "\n".join(statements)
        )
    finally:
        await _cleanup_workspace(factory, workspace_id)


@pytest.mark.integration
async def test_query_round_trips_independent_of_turn_count(sync_engine, test_user) -> None:
    """列表查询数据往返与 Turn 数量无关（1 vs 20 相同，证明无 N+1）。"""
    async_url = _to_async_url(sync_engine.url.render_as_string(hide_password=False))
    engine = create_async_engine(async_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    one_id = await _seed_workspace_with_turns(factory, test_user, num_turns=1)
    twenty_id = await _seed_workspace_with_turns(factory, test_user, num_turns=20)
    try:
        one_rt, _, _ = await _data_round_trips_for_list(factory, test_user, one_id)
        twenty_rt, _, twenty_items = await _data_round_trips_for_list(factory, test_user, twenty_id)
        assert twenty_items == 20
        assert one_rt == twenty_rt, (
            f"往返次数应不随 Turn 数增长（1 turn={one_rt}, 20 turns={twenty_rt}）"
        )
        assert twenty_rt <= QUERY_BUDGET
    finally:
        await _cleanup_workspace(factory, one_id)
        await _cleanup_workspace(factory, twenty_id)
