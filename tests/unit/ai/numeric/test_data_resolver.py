"""test_data_resolver.py — Resolver 与权限测试。

设计文档 §19.6 Resolver 与权限测试。
"""

from __future__ import annotations

import asyncio
import math
from uuid import UUID

import numpy as np
import pytest

from packages.ai.numeric.contracts import (
    NumericError,
    NumericLimits,
    NumericPrincipal,
    NumericSource,
)
from packages.ai.numeric.data_resolver import NumericDataResolver


# =============================================================================
# 辅助函数
# =============================================================================


def make_principal(roles: tuple[str, ...] = ("lab_director",)) -> NumericPrincipal:
    return NumericPrincipal(
        user_id=UUID("018f0000-0000-7000-8000-000000000001"),
        department_id=UUID("018f0000-0000-7000-8000-000000000002"),
        roles=roles,
    )


def resolve_sync(source: NumericSource, principal: NumericPrincipal | None = None, **kwargs) -> any:
    """同步包装 async resolve。"""
    resolver = NumericDataResolver(**kwargs)
    p = principal or make_principal()
    return asyncio.run(resolver.resolve(source, p))


# =============================================================================
# inline 不读数据库
# =============================================================================


class TestInlineResolution:
    """inline 解析不需要数据库。"""

    def test_inline_basic(self) -> None:
        source = NumericSource(name="x", source_type="inline", values=[1.0, 2.0, 3.0])
        result = resolve_sync(source)
        assert result.name == "x"
        assert result.length == 3
        np.testing.assert_allclose(result.values, [1.0, 2.0, 3.0])
        assert not np.any(result.null_mask)
        assert result.source_provenance.source_type == "inline"

    def test_inline_with_null(self) -> None:
        source = NumericSource(name="x", source_type="inline", values=[1.0, None, 3.0])
        result = resolve_sync(source)
        assert result.null_mask[1] == True
        assert result.null_mask[0] == False

    def test_inline_with_unit(self) -> None:
        source = NumericSource(name="x", source_type="inline", values=[1.0, 2.0], unit="MPa")
        result = resolve_sync(source)
        assert result.unit == "MPa"

    def test_inline_reject_bool(self) -> None:
        source = NumericSource(name="x", source_type="inline", values=[True, 1.0])
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_numeric"

    def test_inline_reject_string(self) -> None:
        source = NumericSource(name="x", source_type="inline", values=["hello", 1.0])
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_numeric"

    def test_inline_reject_nested_array(self) -> None:
        source = NumericSource(name="x", source_type="inline", values=[[1.0], 2.0])
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_numeric"

    def test_inline_exceeds_limit(self) -> None:
        values = [1.0] * 10001
        source = NumericSource(name="x", source_type="inline", values=values)
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_size_limit"

    def test_inline_input_digest_stable(self) -> None:
        source1 = NumericSource(name="x", source_type="inline", values=[1.0, 2.0, 3.0])
        source2 = NumericSource(name="x", source_type="inline", values=[1.0, 2.0, 3.0])
        r1 = resolve_sync(source1)
        r2 = resolve_sync(source2)
        assert r1.input_digest == r2.input_digest

    def test_inline_input_digest_sensitive(self) -> None:
        source1 = NumericSource(name="x", source_type="inline", values=[1.0, 2.0, 3.0])
        source2 = NumericSource(name="x", source_type="inline", values=[1.0, 2.0, 3.1])
        r1 = resolve_sync(source1)
        r2 = resolve_sync(source2)
        assert r1.input_digest != r2.input_digest


# =============================================================================
# scalar 解析
# =============================================================================


class TestScalarResolution:
    """scalar 解析有限数字，拒绝 bool/NaN/Infinity。"""

    def test_scalar_basic(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value=900)
        result = resolve_sync(source)
        assert result.is_scalar
        assert float(result.values) == 900.0
        assert result.length == 1

    def test_scalar_with_unit(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value=900, unit="K")
        result = resolve_sync(source)
        assert result.unit == "K"

    def test_scalar_reject_bool(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value=True)
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_numeric"

    def test_scalar_reject_string(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value="hello")
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_numeric"

    def test_scalar_reject_nan(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value=float("nan"))
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_finite_result"

    def test_scalar_reject_infinity(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value=float("inf"))
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_non_finite_result"

    def test_scalar_missing_value(self) -> None:
        source = NumericSource(name="T", source_type="scalar")
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_invalid_source"

    def test_scalar_input_digest(self) -> None:
        source = NumericSource(name="T", source_type="scalar", value=42.0)
        result = resolve_sync(source)
        assert result.input_digest != ""
        assert len(result.input_digest) == 64


# =============================================================================
# artifact_series stub
# =============================================================================


class TestArtifactSeriesStub:
    """artifact_series stub 返回 numeric_invalid_source。"""

    def test_artifact_series_returns_invalid_source(self) -> None:
        source = NumericSource(
            name="x",
            source_type="artifact_series",
            artifact_id="018f0000-0000-7000-8000-000000000002",
            series_index=0,
            column_name="value",
        )
        # lab_director has artifact:read
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_invalid_source"
        assert "not yet supported" in exc_info.value.message

    def test_artifact_series_no_permission(self) -> None:
        source = NumericSource(
            name="x",
            source_type="artifact_series",
            artifact_id="018f0000-0000-7000-8000-000000000002",
            series_index=0,
            column_name="value",
        )
        # Use a role that definitely doesn't have artifact:read
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source, principal=make_principal(roles=("unknown_role",)))
        assert exc_info.value.code == "numeric_field_not_found"


# =============================================================================
# fact_series 权限 + RLS
# =============================================================================


class TestFactSeriesResolution:
    """fact_series 需要 fact:read 权限。"""

    def test_fact_series_no_permission(self) -> None:
        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=0,
            column_name="value",
        )
        # Use a role that definitely doesn't have fact:read
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source, principal=make_principal(roles=("unknown_role",)))
        assert exc_info.value.code == "numeric_field_not_found"

    def test_fact_series_no_factory(self) -> None:
        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=0,
            column_name="value",
        )
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        # Without factory, should get internal error or field_not_found
        assert exc_info.value.code in ("numeric_internal_error", "numeric_field_not_found")

    def test_fact_series_with_mock_factory(self) -> None:
        """Test with a mock FactQueryService factory."""

        class MockFactService:
            async def get_fact_data(self, fact_id):
                return {
                    "series": [
                        {
                            "columns": ["value"],
                            "units": {"value": "MPa"},
                        }
                    ],
                    "points": [
                        {"value": 1.0},
                        {"value": 2.0},
                        {"value": 3.0},
                    ],
                }

        def factory(principal):
            return MockFactService()

        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=0,
            column_name="value",
        )
        result = resolve_sync(source, fact_query_factory=factory)
        assert result.length == 3
        np.testing.assert_allclose(result.values, [1.0, 2.0, 3.0])
        assert result.unit == "MPa"
        assert result.source_provenance.source_type == "fact_series"
        assert result.source_provenance.series_index == 0
        assert result.source_provenance.column_name == "value"

    def test_fact_series_cross_tenant_not_found(self) -> None:
        """跨租户/不存在统一 not_found。"""

        class MockFactService:
            async def get_fact_data(self, fact_id):
                raise Exception("not found")

        def factory(principal):
            return MockFactService()

        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000099",
            series_index=0,
            column_name="value",
        )
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source, fact_query_factory=factory)
        assert exc_info.value.code == "numeric_field_not_found"

    def test_fact_series_invalid_uuid(self) -> None:
        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="not-a-uuid",
            series_index=0,
            column_name="value",
        )
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_invalid_source"

    def test_fact_series_missing_fields(self) -> None:
        # Missing fact_id
        source = NumericSource(
            name="x",
            source_type="fact_series",
            series_index=0,
            column_name="value",
        )
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_invalid_source"

    def test_fact_series_invalid_series_index(self) -> None:
        class MockFactService:
            async def get_fact_data(self, fact_id):
                return {"series": [{"columns": ["value"]}], "points": []}

        def factory(principal):
            return MockFactService()

        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=5,
            column_name="value",
        )
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source, fact_query_factory=factory)
        assert exc_info.value.code == "numeric_field_not_found"

    def test_fact_series_invalid_column(self) -> None:
        class MockFactService:
            async def get_fact_data(self, fact_id):
                return {"series": [{"columns": ["temperature"]}], "points": []}

        def factory(principal):
            return MockFactService()

        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=0,
            column_name="value",
        )
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source, fact_query_factory=factory)
        assert exc_info.value.code == "numeric_field_not_found"

    def test_fact_series_unit_from_artifact(self) -> None:
        """平台来源单位来自 artifact，不能由工具参数覆盖。"""

        class MockFactService:
            async def get_fact_data(self, fact_id):
                return {
                    "series": [
                        {
                            "columns": ["value"],
                            "units": {"value": "GPa"},
                        }
                    ],
                    "points": [{"value": 1.0}, {"value": 2.0}],
                }

        def factory(principal):
            return MockFactService()

        source = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=0,
            column_name="value",
        )
        result = resolve_sync(source, fact_query_factory=factory)
        assert result.unit == "GPa"  # from artifact, not from parameter


# =============================================================================
# 未知 source_type
# =============================================================================


class TestUnknownSourceType:
    """未知 source_type 被拒绝。"""

    def test_unknown_source_type(self) -> None:
        source = NumericSource(name="x", source_type="unknown_type")
        with pytest.raises(NumericError) as exc_info:
            resolve_sync(source)
        assert exc_info.value.code == "numeric_invalid_source"
