"""工业对象图服务单元测试（IRIP Task 11）。

验证：
- 创建对象 → status=active；
- 重复编码+类型 → conflict；
- 添加关系（contains）→ 创建成功；
- 自关联 → AppError(code="self_relation")；
- 环检测：A contains B, B contains A → AppError(code="object_cycle")；
- upstream_of / downstream_of 环检测；
- 非层次型关系（connected_to）允许双向；
- 幂等关系：重复添加返回同一行；
- descendants：lab → instrument → measurement_point 遍历；
- descendants 无子对象 → 空元组；
- 移除关系 → is_active=false；
- 重新添加移除的关系 → 重新激活。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL）。
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.standards.object_graph import ObjectGraphService


@pytest.fixture
async def graph_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    test_user: object,
    sync_engine: object,
) -> "ObjectGraphService":
    """ObjectGraphService（使用 test_user 的 org_id），测试后清理数据。"""
    org_id = test_user.organization_id  # type: ignore[attr-defined]
    service = ObjectGraphService(
        session_factory=async_session_factory,
        organization_id=org_id,
        actor_id=test_user.user_id,  # type: ignore[attr-defined]
    )
    yield service  # type: ignore[misc]

    # 清理：删除该组织下的全部对象关系和对象数据
    with sync_engine.connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            sa.text("DELETE FROM object_relation WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.execute(
            sa.text("DELETE FROM industrial_object WHERE organization_id = :oid"),
            {"oid": org_id},
        )
        conn.commit()


class TestAddObject:
    """创建工业对象测试。"""

    @pytest.mark.asyncio
    async def test_add_object_active(self, graph_service: ObjectGraphService) -> None:
        """创建对象 → status=active。"""
        obj = await graph_service.add_object(
            object_type="lab",
            code="LAB-001",
            display_name="主实验室",
            description="研发中心实验室",
        )
        assert obj.status == "active"
        assert obj.object_type == "lab"
        assert obj.code == "LAB-001"
        assert obj.display_name == "主实验室"
        assert obj.description == "研发中心实验室"
        assert obj.parent_id is None

    @pytest.mark.asyncio
    async def test_add_object_with_parent(self, graph_service: ObjectGraphService) -> None:
        """创建对象带父对象。"""
        parent = await graph_service.add_object(
            object_type="lab",
            code="LAB-PARENT",
            display_name="父实验室",
        )
        child = await graph_service.add_object(
            object_type="instrument",
            code="INST-001",
            display_name="仪器1",
            parent_id=parent.id,
        )
        assert child.parent_id == parent.id

    @pytest.mark.asyncio
    async def test_add_object_parent_not_found(self, graph_service: ObjectGraphService) -> None:
        """父对象不存在 → AppError(not_found)。"""
        from packages.common.ids import new_id

        with pytest.raises(AppError) as exc_info:
            await graph_service.add_object(
                object_type="instrument",
                code="INST-ORPHAN",
                display_name="孤儿仪器",
                parent_id=new_id(),
            )
        assert exc_info.value.code == "not_found"

    @pytest.mark.asyncio
    async def test_duplicate_code_same_type_conflict(
        self, graph_service: ObjectGraphService
    ) -> None:
        """同类型同编码 → conflict。"""
        await graph_service.add_object(
            object_type="lab",
            code="DUP-LAB",
            display_name="实验室A",
        )
        with pytest.raises(AppError) as exc_info:
            await graph_service.add_object(
                object_type="lab",
                code="DUP-LAB",
                display_name="实验室B",
            )
        assert exc_info.value.code == "conflict"

    @pytest.mark.asyncio
    async def test_same_code_different_type_ok(self, graph_service: ObjectGraphService) -> None:
        """同编码不同类型 → 允许。"""
        obj1 = await graph_service.add_object(
            object_type="lab",
            code="SHARED-001",
            display_name="实验室",
        )
        obj2 = await graph_service.add_object(
            object_type="instrument",
            code="SHARED-001",
            display_name="仪器",
        )
        assert obj1.object_type == "lab"
        assert obj2.object_type == "instrument"
        assert obj1.code == obj2.code

    @pytest.mark.asyncio
    async def test_get_object_by_code(self, graph_service: ObjectGraphService) -> None:
        """按编码+类型查询对象。"""
        await graph_service.add_object(
            object_type="lab",
            code="FIND-001",
            display_name="查找实验室",
        )
        found = await graph_service.get_object_by_code("FIND-001", "lab")
        assert found is not None
        assert found.display_name == "查找实验室"

        not_found = await graph_service.get_object_by_code("NOTEXIST", "lab")
        assert not_found is None

    @pytest.mark.asyncio
    async def test_get_object_not_found(self, graph_service: ObjectGraphService) -> None:
        """查询不存在的对象 → not_found。"""
        from packages.common.ids import new_id

        with pytest.raises(AppError) as exc_info:
            await graph_service.get_object(new_id())
        assert exc_info.value.code == "not_found"

    @pytest.mark.asyncio
    async def test_list_objects_pagination(self, graph_service: ObjectGraphService) -> None:
        """分页查询对象列表。"""
        for i in range(5):
            await graph_service.add_object(
                object_type="lab",
                code=f"LIST-{i:03d}",
                display_name=f"实验室{i}",
            )

        items, next_cursor = await graph_service.list_objects(page_size=3)
        assert len(items) == 3
        assert next_cursor is not None

        items2, next_cursor2 = await graph_service.list_objects(cursor=next_cursor, page_size=3)
        assert len(items2) == 2
        assert next_cursor2 is None

    @pytest.mark.asyncio
    async def test_list_objects_filter_by_type(self, graph_service: ObjectGraphService) -> None:
        """按类型过滤对象列表。"""
        await graph_service.add_object(object_type="lab", code="TYPE-LAB", display_name="实验室")
        await graph_service.add_object(
            object_type="instrument", code="TYPE-INST", display_name="仪器"
        )

        items, _ = await graph_service.list_objects(object_type="lab")
        assert all(obj.object_type == "lab" for obj in items)
        assert len(items) == 1
        assert items[0].code == "TYPE-LAB"


class TestAddRelation:
    """添加关系测试。"""

    @pytest.mark.asyncio
    async def test_add_relation_contains(self, graph_service: ObjectGraphService) -> None:
        """添加 contains 关系成功。"""
        lab = await graph_service.add_object(
            object_type="lab", code="REL-LAB", display_name="实验室"
        )
        inst = await graph_service.add_object(
            object_type="instrument", code="REL-INST", display_name="仪器"
        )
        relation = await graph_service.add_relation(
            source_id=lab.id,
            target_id=inst.id,
            relation_type="contains",
        )
        assert relation.source_id == lab.id
        assert relation.target_id == inst.id
        assert relation.relation_type == "contains"
        assert relation.is_active is True

    @pytest.mark.asyncio
    async def test_self_relation_rejected(self, graph_service: ObjectGraphService) -> None:
        """自关联 → AppError(code="self_relation")。"""
        obj = await graph_service.add_object(
            object_type="lab", code="SELF-LAB", display_name="自关联测试"
        )
        with pytest.raises(AppError) as exc_info:
            await graph_service.add_relation(
                source_id=obj.id,
                target_id=obj.id,
                relation_type="contains",
            )
        assert exc_info.value.code == "self_relation"

    @pytest.mark.asyncio
    async def test_relation_object_not_found(self, graph_service: ObjectGraphService) -> None:
        """关系对象不存在 → not_found。"""
        obj = await graph_service.add_object(
            object_type="lab", code="NF-LAB", display_name="存在对象"
        )
        from packages.common.ids import new_id

        with pytest.raises(AppError) as exc_info:
            await graph_service.add_relation(
                source_id=obj.id,
                target_id=new_id(),
                relation_type="contains",
            )
        assert exc_info.value.code == "not_found"

    @pytest.mark.asyncio
    async def test_cycle_detection_contains(self, graph_service: ObjectGraphService) -> None:
        """环检测：A contains B, B contains A → AppError(object_cycle)。"""
        a = await graph_service.add_object(object_type="lab", code="CYC-A", display_name="A")
        b = await graph_service.add_object(object_type="instrument", code="CYC-B", display_name="B")
        # A contains B — OK
        await graph_service.add_relation(source_id=a.id, target_id=b.id, relation_type="contains")
        # B contains A — 应检测到环
        with pytest.raises(AppError) as exc_info:
            await graph_service.add_relation(
                source_id=b.id, target_id=a.id, relation_type="contains"
            )
        assert exc_info.value.code == "object_cycle"

    @pytest.mark.asyncio
    async def test_cycle_detection_upstream_of(self, graph_service: ObjectGraphService) -> None:
        """环检测：upstream_of。"""
        a = await graph_service.add_object(
            object_type="production_line", code="UP-A", display_name="产线A"
        )
        b = await graph_service.add_object(
            object_type="production_line", code="UP-B", display_name="产线B"
        )
        await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="upstream_of"
        )
        with pytest.raises(AppError) as exc_info:
            await graph_service.add_relation(
                source_id=b.id, target_id=a.id, relation_type="upstream_of"
            )
        assert exc_info.value.code == "object_cycle"

    @pytest.mark.asyncio
    async def test_cycle_detection_downstream_of(self, graph_service: ObjectGraphService) -> None:
        """环检测：downstream_of。"""
        a = await graph_service.add_object(
            object_type="production_line", code="DOWN-A", display_name="产线A"
        )
        b = await graph_service.add_object(
            object_type="production_line", code="DOWN-B", display_name="产线B"
        )
        await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="downstream_of"
        )
        with pytest.raises(AppError) as exc_info:
            await graph_service.add_relation(
                source_id=b.id, target_id=a.id, relation_type="downstream_of"
            )
        assert exc_info.value.code == "object_cycle"

    @pytest.mark.asyncio
    async def test_non_hierarchical_bidirectional(self, graph_service: ObjectGraphService) -> None:
        """非层次型关系（connected_to）允许双向：A→B 和 B→A 都 OK。"""
        a = await graph_service.add_object(
            object_type="instrument", code="CONN-A", display_name="仪器A"
        )
        b = await graph_service.add_object(
            object_type="instrument", code="CONN-B", display_name="仪器B"
        )
        rel1 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="connected_to"
        )
        rel2 = await graph_service.add_relation(
            source_id=b.id, target_id=a.id, relation_type="connected_to"
        )
        assert rel1.source_id == a.id
        assert rel1.target_id == b.id
        assert rel2.source_id == b.id
        assert rel2.target_id == a.id
        assert rel1.id != rel2.id

    @pytest.mark.asyncio
    async def test_idempotent_relation(self, graph_service: ObjectGraphService) -> None:
        """幂等关系：重复添加返回同一行，无错误。"""
        a = await graph_service.add_object(object_type="lab", code="IDEM-A", display_name="A")
        b = await graph_service.add_object(
            object_type="instrument", code="IDEM-B", display_name="B"
        )
        rel1 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="contains"
        )
        rel2 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="contains"
        )
        assert rel1.id == rel2.id

    @pytest.mark.asyncio
    async def test_different_relation_types_same_pair(
        self, graph_service: ObjectGraphService
    ) -> None:
        """同一对对象可以有不同类型的关系。"""
        a = await graph_service.add_object(
            object_type="instrument", code="MULTI-A", display_name="A"
        )
        b = await graph_service.add_object(
            object_type="instrument", code="MULTI-B", display_name="B"
        )
        rel1 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="connected_to"
        )
        rel2 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="equivalent_to"
        )
        assert rel1.id != rel2.id
        assert rel1.relation_type == "connected_to"
        assert rel2.relation_type == "equivalent_to"

    @pytest.mark.asyncio
    async def test_deep_cycle_detection(self, graph_service: ObjectGraphService) -> None:
        """深层环检测：A→B→C→A 三级环。"""
        a = await graph_service.add_object(object_type="lab", code="DEEP-A", display_name="A")
        b = await graph_service.add_object(
            object_type="equipment_group", code="DEEP-B", display_name="B"
        )
        c = await graph_service.add_object(
            object_type="instrument", code="DEEP-C", display_name="C"
        )
        await graph_service.add_relation(source_id=a.id, target_id=b.id, relation_type="contains")
        await graph_service.add_relation(source_id=b.id, target_id=c.id, relation_type="contains")
        # C contains A — 应检测到环（C→...→A 已有路径 A→B→C）
        with pytest.raises(AppError) as exc_info:
            await graph_service.add_relation(
                source_id=c.id, target_id=a.id, relation_type="contains"
            )
        assert exc_info.value.code == "object_cycle"


class TestDescendants:
    """后代遍历测试。"""

    @pytest.mark.asyncio
    async def test_descendants_hierarchy(self, graph_service: ObjectGraphService) -> None:
        """descendants: lab → instrument → measurement_point。"""
        lab = await graph_service.add_object(
            object_type="lab", code="DESC-LAB", display_name="实验室"
        )
        inst = await graph_service.add_object(
            object_type="instrument", code="DESC-INST", display_name="仪器"
        )
        mp = await graph_service.add_object(
            object_type="measurement_point",
            code="DESC-MP",
            display_name="测量点",
        )
        await graph_service.add_relation(
            source_id=lab.id, target_id=inst.id, relation_type="contains"
        )
        await graph_service.add_relation(
            source_id=inst.id, target_id=mp.id, relation_type="contains"
        )

        descendants = await graph_service.descendants(lab.id)
        assert len(descendants) == 2
        assert inst.id in descendants
        assert mp.id in descendants

    @pytest.mark.asyncio
    async def test_descendants_no_children(self, graph_service: ObjectGraphService) -> None:
        """descendants 无子对象 → 空元组。"""
        obj = await graph_service.add_object(
            object_type="lab", code="LEAF-LAB", display_name="叶节点实验室"
        )
        descendants = await graph_service.descendants(obj.id)
        assert descendants == ()

    @pytest.mark.asyncio
    async def test_descendants_breadth_first_order(self, graph_service: ObjectGraphService) -> None:
        """descendants BFS 顺序：深度优先，同深度按 ID 排序。"""
        lab = await graph_service.add_object(
            object_type="lab", code="BFS-LAB", display_name="BFS实验室"
        )
        child1 = await graph_service.add_object(
            object_type="equipment_group", code="BFS-C1", display_name="C1"
        )
        child2 = await graph_service.add_object(
            object_type="equipment_group", code="BFS-C2", display_name="C2"
        )
        grandchild = await graph_service.add_object(
            object_type="instrument", code="BFS-GC", display_name="GC"
        )
        await graph_service.add_relation(
            source_id=lab.id, target_id=child1.id, relation_type="contains"
        )
        await graph_service.add_relation(
            source_id=lab.id, target_id=child2.id, relation_type="contains"
        )
        await graph_service.add_relation(
            source_id=child1.id, target_id=grandchild.id, relation_type="contains"
        )

        descendants = await graph_service.descendants(lab.id)
        assert len(descendants) == 3
        # 深度1的两个子节点应在深度2的孙节点之前
        assert child1.id in descendants[:2]
        assert child2.id in descendants[:2]
        assert grandchild.id == descendants[2]

    @pytest.mark.asyncio
    async def test_descendants_root_not_found(self, graph_service: ObjectGraphService) -> None:
        """根对象不存在 → not_found。"""
        from packages.common.ids import new_id

        with pytest.raises(AppError) as exc_info:
            await graph_service.descendants(new_id())
        assert exc_info.value.code == "not_found"


class TestRemoveAndReactivateRelation:
    """移除与重新激活关系测试。"""

    @pytest.mark.asyncio
    async def test_remove_relation_deactivates(self, graph_service: ObjectGraphService) -> None:
        """移除关系 → is_active=false。"""
        a = await graph_service.add_object(object_type="lab", code="RM-A", display_name="A")
        b = await graph_service.add_object(object_type="instrument", code="RM-B", display_name="B")
        await graph_service.add_relation(source_id=a.id, target_id=b.id, relation_type="contains")

        # 移除
        await graph_service.remove_relation(
            source_id=a.id, target_id=b.id, relation_type="contains"
        )

        # 验证关系已不活跃
        relations = await graph_service.get_relations(a.id)
        assert len(relations) == 0  # get_relations 只返回活跃关系

    @pytest.mark.asyncio
    async def test_remove_relation_not_found(self, graph_service: ObjectGraphService) -> None:
        """移除不存在的关系 → not_found。"""
        a = await graph_service.add_object(object_type="lab", code="RMNF-A", display_name="A")
        b = await graph_service.add_object(
            object_type="instrument", code="RMNF-B", display_name="B"
        )
        with pytest.raises(AppError) as exc_info:
            await graph_service.remove_relation(
                source_id=a.id, target_id=b.id, relation_type="contains"
            )
        assert exc_info.value.code == "not_found"

    @pytest.mark.asyncio
    async def test_readd_removed_relation_reactivates(
        self, graph_service: ObjectGraphService
    ) -> None:
        """重新添加移除的关系 → 重新激活。"""
        a = await graph_service.add_object(object_type="lab", code="REACT-A", display_name="A")
        b = await graph_service.add_object(
            object_type="instrument", code="REACT-B", display_name="B"
        )
        rel1 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="contains"
        )

        # 移除
        await graph_service.remove_relation(
            source_id=a.id, target_id=b.id, relation_type="contains"
        )

        # 重新添加（应重新激活同一行）
        rel2 = await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="contains"
        )
        assert rel1.id == rel2.id
        assert rel2.is_active is True

    @pytest.mark.asyncio
    async def test_get_relations_filtered_by_type(self, graph_service: ObjectGraphService) -> None:
        """按关系类型过滤查询关系。"""
        a = await graph_service.add_object(
            object_type="instrument", code="FILTER-A", display_name="A"
        )
        b = await graph_service.add_object(
            object_type="instrument", code="FILTER-B", display_name="B"
        )
        await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="connected_to"
        )
        await graph_service.add_relation(
            source_id=a.id, target_id=b.id, relation_type="equivalent_to"
        )

        connected = await graph_service.get_relations(a.id, relation_type="connected_to")
        assert len(connected) == 1
        assert connected[0].relation_type == "connected_to"

        all_relations = await graph_service.get_relations(a.id)
        assert len(all_relations) == 2
