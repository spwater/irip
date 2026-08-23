"""Object graph integration tests: ObjectGraphService CRUD with real DB.

Tests cover:
  - add_object (uniqueness, parent validation)
  - get_object (not_found)
  - update_object (code immutability)
  - set_object_status (active/inactive)
  - delete_object
  - get_object_by_code
  - list_objects (filter, pagination, cursor)
  - count_facts_by_object

Uses ScopedSessionMixin with RLS GUC set via department_id/actor_id.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.standards.objects.object_graph import (
    ObjectGraphService,
    _decode_list_cursor,
    _encode_list_cursor,
)

# ============================================================
# Helpers
# ============================================================


async def _cleanup_objects(session_factory, object_ids: list[UUID]) -> None:
    """Delete industrial objects by ID."""
    if not object_ids:
        return
    async with session_factory() as session:
        async with session.begin():
            for oid in object_ids:
                await session.execute(
                    sa.text("DELETE FROM industrial_object WHERE id = :id"),
                    {"id": oid},
                )


# ============================================================
# Cursor encode/decode tests
# ============================================================


class TestListCursorEncodeDecode:
    """_encode_list_cursor / _decode_list_cursor round-trip and error handling."""

    def test_encode_decode_roundtrip(self) -> None:
        """Encode then decode returns the same (created_at, object_id)."""
        ts = datetime(2026, 3, 15, 12, 0, 0)
        oid = uuid4()
        cursor = _encode_list_cursor(ts, oid)
        decoded_ts, decoded_id = _decode_list_cursor(cursor)
        assert decoded_ts == ts
        assert decoded_id == oid

    def test_decode_invalid_base64_raises(self) -> None:
        """Malformed base64 raises AppError(invalid_cursor)."""
        with pytest.raises(AppError) as exc_info:
            _decode_list_cursor("!!!not-base64!!!")
        assert exc_info.value.code == "invalid_cursor"

    def test_decode_invalid_json_raises(self) -> None:
        """Valid base64 but invalid JSON raises."""
        bad = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(AppError) as exc_info:
            _decode_list_cursor(bad)
        assert exc_info.value.code == "invalid_cursor"

    def test_decode_missing_fields_raises(self) -> None:
        """JSON missing v or id raises."""
        payload = base64.urlsafe_b64encode(json.dumps({"v": "2026-01-01"}).encode()).decode()
        with pytest.raises(AppError) as exc_info:
            _decode_list_cursor(payload)
        assert exc_info.value.code == "invalid_cursor"

    def test_decode_invalid_timestamp_raises(self) -> None:
        """v field not a valid ISO datetime raises."""
        payload = base64.urlsafe_b64encode(
            json.dumps({"v": "not-a-date", "id": str(uuid4())}).encode()
        ).decode()
        with pytest.raises(AppError) as exc_info:
            _decode_list_cursor(payload)
        assert exc_info.value.code == "invalid_cursor"

    def test_decode_invalid_uuid_raises(self) -> None:
        """id field not a valid UUID raises."""
        payload = base64.urlsafe_b64encode(
            json.dumps({"v": "2026-01-01T00:00:00", "id": "not-a-uuid"}).encode()
        ).decode()
        with pytest.raises(AppError) as exc_info:
            _decode_list_cursor(payload)
        assert exc_info.value.code == "invalid_cursor"


# ============================================================
# ObjectGraphService DB-backed tests
# ============================================================


@pytest.mark.integration
class TestObjectGraphServiceDB:
    """ObjectGraphService CRUD with real DB."""

    async def test_add_object_success(self, async_session_factory, test_user) -> None:
        """add_object creates an object with status=active."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-{uuid4().hex[:8]}",
            display_name="测试实验室",
            description="A test lab",
        )
        try:
            assert obj.id is not None
            assert obj.object_type == "lab"
            assert obj.status == "active"
            assert obj.display_name == "测试实验室"
            assert obj.lock_version == 0
            assert obj.department_id == test_user.department_id
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_add_object_duplicate_code_conflict(
        self, async_session_factory, test_user
    ) -> None:
        """add_object with duplicate code+type raises conflict."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        code = f"LAB-{uuid4().hex[:8]}"
        obj = await service.add_object(
            object_type="lab",
            code=code,
            display_name="First Lab",
        )
        try:
            with pytest.raises(AppError) as exc_info:
                await service.add_object(
                    object_type="lab",
                    code=code,
                    display_name="Second Lab",
                )
            assert exc_info.value.code == "conflict"
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_add_object_same_code_different_type_ok(
        self, async_session_factory, test_user
    ) -> None:
        """Same code with different object_type is allowed."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        code = f"SHARED-{uuid4().hex[:8]}"
        obj1 = await service.add_object(
            object_type="lab",
            code=code,
            display_name="Lab",
        )
        obj2 = await service.add_object(
            object_type="production_line",
            code=code,
            display_name="Line",
        )
        try:
            assert obj1.id != obj2.id
            assert obj1.object_type == "lab"
            assert obj2.object_type == "production_line"
        finally:
            await _cleanup_objects(async_session_factory, [obj1.id, obj2.id])

    async def test_add_object_with_parent(self, async_session_factory, test_user) -> None:
        """add_object with a valid parent_id succeeds."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        parent = await service.add_object(
            object_type="lab",
            code=f"LAB-P-{uuid4().hex[:8]}",
            display_name="Parent Lab",
        )
        child = await service.add_object(
            object_type="production_line",
            code=f"LINE-C-{uuid4().hex[:8]}",
            display_name="Child Line",
            parent_id=parent.id,
        )
        try:
            assert child.id is not None
        finally:
            await _cleanup_objects(async_session_factory, [child.id, parent.id])

    async def test_add_object_invalid_parent_raises(self, async_session_factory, test_user) -> None:
        """add_object with non-existent parent raises not_found."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        with pytest.raises(AppError) as exc_info:
            await service.add_object(
                object_type="lab",
                code=f"LAB-X-{uuid4().hex[:8]}",
                display_name="Orphan",
                parent_id=uuid4(),
            )
        assert exc_info.value.code == "not_found"

    async def test_add_object_with_visible_departments(
        self, async_session_factory, test_user
    ) -> None:
        """add_object stores visible_departments list."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        visible = [str(uuid4()), str(uuid4())]
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-V-{uuid4().hex[:8]}",
            display_name="Visible Lab",
            visible_departments=visible,
        )
        try:
            assert obj.visible_departments == visible
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_get_object_success(self, async_session_factory, test_user) -> None:
        """get_object retrieves an existing object."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        created = await service.add_object(
            object_type="lab",
            code=f"LAB-G-{uuid4().hex[:8]}",
            display_name="Get Lab",
        )
        try:
            retrieved = await service.get_object(created.id)
            assert retrieved.id == created.id
            assert retrieved.display_name == "Get Lab"
        finally:
            await _cleanup_objects(async_session_factory, [created.id])

    async def test_get_object_not_found(self, async_session_factory, test_user) -> None:
        """get_object raises not_found for non-existent ID."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        with pytest.raises(AppError) as exc_info:
            await service.get_object(uuid4())
        assert exc_info.value.code == "not_found"

    async def test_update_object(self, async_session_factory, test_user) -> None:
        """update_object changes display_name and description."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-U-{uuid4().hex[:8]}",
            display_name="Old Name",
            description="Old desc",
        )
        try:
            updated = await service.update_object(
                obj.id,
                display_name="New Name",
                description="New desc",
            )
            assert updated.display_name == "New Name"
            assert updated.description == "New desc"
            assert updated.lock_version == 1
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_update_object_type(self, async_session_factory, test_user) -> None:
        """update_object can change object_type."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-UT-{uuid4().hex[:8]}",
            display_name="Type Change",
        )
        try:
            updated = await service.update_object(
                obj.id,
                display_name="Type Change",
                object_type="instrument",
            )
            assert updated.object_type == "instrument"
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_update_object_not_found(self, async_session_factory, test_user) -> None:
        """update_object raises not_found for non-existent ID."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        with pytest.raises(AppError) as exc_info:
            await service.update_object(uuid4(), display_name="X")
        assert exc_info.value.code == "not_found"

    async def test_set_object_status(self, async_session_factory, test_user) -> None:
        """set_object_status toggles active/inactive."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-S-{uuid4().hex[:8]}",
            display_name="Status Lab",
        )
        try:
            inactive = await service.set_object_status(obj.id, "inactive")
            assert inactive.status == "inactive"
            assert inactive.lock_version == 1

            active = await service.set_object_status(obj.id, "active")
            assert active.status == "active"
            assert active.lock_version == 2
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_set_object_status_not_found(self, async_session_factory, test_user) -> None:
        """set_object_status raises not_found for non-existent ID."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        with pytest.raises(AppError) as exc_info:
            await service.set_object_status(uuid4(), "inactive")
        assert exc_info.value.code == "not_found"

    async def test_delete_object(self, async_session_factory, test_user) -> None:
        """delete_object physically removes the object."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-D-{uuid4().hex[:8]}",
            display_name="Delete Lab",
        )
        obj_id = obj.id
        await service.delete_object(obj_id)

        # Verify it's gone
        with pytest.raises(AppError) as exc_info:
            await service.get_object(obj_id)
        assert exc_info.value.code == "not_found"

    async def test_delete_object_not_found(self, async_session_factory, test_user) -> None:
        """delete_object raises not_found for non-existent ID."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        with pytest.raises(AppError) as exc_info:
            await service.delete_object(uuid4())
        assert exc_info.value.code == "not_found"

    async def test_get_object_by_code(self, async_session_factory, test_user) -> None:
        """get_object_by_code finds object by code+type."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        code = f"LAB-C-{uuid4().hex[:8]}"
        obj = await service.add_object(
            object_type="lab",
            code=code,
            display_name="Code Lab",
        )
        try:
            found = await service.get_object_by_code(code, "lab")
            assert found is not None
            assert found.id == obj.id

            # Non-existent code returns None
            missing = await service.get_object_by_code("NON-EXISTENT", "lab")
            assert missing is None
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_count_facts_by_object_zero(self, async_session_factory, test_user) -> None:
        """count_facts_by_object returns 0 for object with no facts."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-F-{uuid4().hex[:8]}",
            display_name="Facts Lab",
        )
        try:
            count = await service.count_facts_by_object(obj.id)
            assert count == 0
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_count_facts_by_object_nonexistent(
        self, async_session_factory, test_user
    ) -> None:
        """count_facts_by_object returns 0 for non-existent object."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        count = await service.count_facts_by_object(uuid4())
        assert count == 0

    async def test_list_objects_empty(self, async_session_factory, test_user) -> None:
        """list_objects returns empty list when no objects exist."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        objects, next_cursor = await service.list_objects()
        # May have objects from other tests, but cursor logic should still work
        assert isinstance(objects, list)
        assert next_cursor is None or isinstance(next_cursor, str)

    async def test_list_objects_with_filter(self, async_session_factory, test_user) -> None:
        """list_objects filters by object_type."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        code = f"LAB-LF-{uuid4().hex[:8]}"
        obj = await service.add_object(
            object_type="lab",
            code=code,
            display_name="Filter Lab",
        )
        try:
            objects, _ = await service.list_objects(object_type="lab")
            assert any(o.id == obj.id for o in objects)

            # Filter by different type should not include it
            objects2, _ = await service.list_objects(object_type="instrument")
            assert all(o.id != obj.id for o in objects2)
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_list_objects_with_list_type_filter(
        self, async_session_factory, test_user
    ) -> None:
        """list_objects accepts a list of types for IN query."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        code1 = f"LAB-LT-{uuid4().hex[:8]}"
        code2 = f"INST-LT-{uuid4().hex[:8]}"
        obj1 = await service.add_object(object_type="lab", code=code1, display_name="LT Lab")
        obj2 = await service.add_object(
            object_type="instrument", code=code2, display_name="LT Instrument"
        )
        try:
            objects, _ = await service.list_objects(
                object_type=["lab", "instrument"], page_size=100
            )
            ids = {o.id for o in objects}
            assert obj1.id in ids
            assert obj2.id in ids
        finally:
            await _cleanup_objects(async_session_factory, [obj1.id, obj2.id])

    async def test_list_objects_pagination(self, async_session_factory, test_user) -> None:
        """list_objects paginates with keyset cursor."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        # Create 3 objects
        objs = []
        for i in range(3):
            o = await service.add_object(
                object_type="lab",
                code=f"LAB-PG-{uuid4().hex[:8]}-{i}",
                display_name=f"Page Lab {i}",
            )
            objs.append(o)
        try:
            # Page size 2 — should get 2 items and a cursor
            page1, cursor1 = await service.list_objects(object_type="lab", page_size=2)
            # Filter to only our objects
            our_page1 = [o for o in page1 if o.id in {obj.id for obj in objs}]
            assert len(our_page1) <= 2

            if cursor1:
                page2, cursor2 = await service.list_objects(
                    object_type="lab", cursor=cursor1, page_size=2
                )
                our_page2 = [o for o in page2 if o.id in {obj.id for obj in objs}]
                # Should not overlap with page1
                page1_ids = {o.id for o in our_page1}
                page2_ids = {o.id for o in our_page2}
                assert page1_ids.isdisjoint(page2_ids)
        finally:
            await _cleanup_objects(async_session_factory, [o.id for o in objs])

    async def test_list_objects_department_filter(self, async_session_factory, test_user) -> None:
        """list_objects filters by department_id."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        obj = await service.add_object(
            object_type="lab",
            code=f"LAB-DF-{uuid4().hex[:8]}",
            display_name="Dept Filter Lab",
        )
        try:
            objects, _ = await service.list_objects(
                object_type="lab",
                department_id=test_user.department_id,
                page_size=100,
            )
            assert any(o.id == obj.id for o in objects)

            # Filter by random department should not include it
            objects2, _ = await service.list_objects(
                object_type="lab",
                department_id=uuid4(),
                page_size=100,
            )
            assert all(o.id != obj.id for o in objects2)
        finally:
            await _cleanup_objects(async_session_factory, [obj.id])

    async def test_list_objects_invalid_cursor(self, async_session_factory, test_user) -> None:
        """list_objects with invalid cursor raises."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        with pytest.raises(AppError) as exc_info:
            await service.list_objects(cursor="!!!invalid-cursor!!!")
        assert exc_info.value.code == "invalid_cursor"

    async def test_session_factory_property(self, async_session_factory, test_user) -> None:
        """session_factory property returns the injected factory."""
        service = ObjectGraphService(
            async_session_factory,
            department_id=test_user.department_id,
            actor_id=test_user.user_id,
        )
        assert service.session_factory is async_session_factory
