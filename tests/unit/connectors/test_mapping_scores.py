"""映射评分算法与映射配置生命周期单元测试。

覆盖：
- exact_code + unit_dimension 提升分数至 ≥ 0.90；
- 别名匹配命中 alias_match；
- 仅已发布变量版本参与候选；
- 未知单位不产生 unit_dimension 加分且无惩罚；
- 数据类型不匹配降低分数且无 data_type 理由；
- 已发布配置拒绝规则变更（published_version_immutable）；
- 候选按分数降序排列；
- display_name（双语名）匹配 + 单位维度 ≥ 0.90。
"""

from uuid import UUID

import pytest

from packages.common.errors import AppError
from packages.connectors.contracts import MappingRule
from packages.connectors.mapping import MappingProfileService, MappingService
from packages.standards.service import StandardService
from tests.unit.connectors.conftest import create_published_variable

# ---- 评分测试 ----


class TestRankScoring:
    """映射评分算法测试。"""

    async def test_exact_code_and_unit_raise_score(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """精确匹配 code + 单位同维度 → 分数 ≥ 0.90，含 exact_code 与 unit_dimension。"""
        await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
            alias="D50",
        )

        candidates = await mapping_service.rank(
            source_name="particle.d50", source_unit="mm", data_type="number"
        )

        assert len(candidates) >= 1
        top = candidates[0]
        assert top.variable_code == "particle.d50"
        assert top.score >= 0.90
        assert "exact_code" in top.reasons
        assert "unit_dimension" in top.reasons
        assert "data_type" in top.reasons

    async def test_display_name_match_scores_high(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """源字段名匹配 display_name（双语名）+ 单位维度 → 分数 ≥ 0.90。"""
        await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
            alias="D50",
        )

        candidates = await mapping_service.rank(
            source_name="中位径", source_unit="mm", data_type="number"
        )

        assert len(candidates) >= 1
        top = candidates[0]
        assert top.variable_code == "particle.d50"
        assert top.score >= 0.90
        assert "unit_dimension" in top.reasons
        assert "bilingual_name" in top.reasons

    async def test_alias_match(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """源字段名匹配别名 → 候选命中且理由含 alias_match。"""
        await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
            alias="D50",
        )

        candidates = await mapping_service.rank(
            source_name="D50", source_unit=None, data_type="number"
        )

        assert len(candidates) >= 1
        top = candidates[0]
        assert top.variable_code == "particle.d50"
        assert "alias_match" in top.reasons
        assert "exact_code" not in top.reasons

    async def test_published_only(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """仅已发布变量版本参与候选，草稿变量不出现。"""
        await create_published_variable(
            standard_service,
            code="published.one",
            display_name="已发布",
            data_type="number",
            canonical_unit="mm",
        )
        # 创建草稿变量（未提交、未发布）
        await standard_service.create_variable(
            code="draft.only",
            display_name="草稿",
            data_type="number",
            canonical_unit="mm",
        )

        candidates = await mapping_service.rank(
            source_name="draft.only", source_unit="mm", data_type="number"
        )

        # 草稿变量无已发布版本，不应出现在候选中
        codes = {c.variable_code for c in candidates}
        assert "draft.only" not in codes
        assert "published.one" in codes

    async def test_unknown_unit_no_dimension_bonus(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """源单位不在注册表 → 无 unit_dimension 加分，且无惩罚。"""
        await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        candidates = await mapping_service.rank(
            source_name="particle.d50",
            source_unit="frobnicate",
            data_type="number",
        )

        assert len(candidates) >= 1
        top = candidates[0]
        assert top.variable_code == "particle.d50"
        assert "unit_dimension" not in top.reasons
        # exact_code(0.55) + data_type(0.15) = 0.70，无单位加分也无惩罚
        assert top.score == pytest.approx(0.70, abs=1e-6)

    async def test_data_type_mismatch_lower_score(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """数据类型不匹配 → 分数更低，理由不含 data_type。"""
        await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        matched = await mapping_service.rank(
            source_name="particle.d50", source_unit="mm", data_type="number"
        )
        mismatched = await mapping_service.rank(
            source_name="particle.d50", source_unit="mm", data_type="text"
        )

        assert len(matched) >= 1 and len(mismatched) >= 1
        assert mismatched[0].score < matched[0].score
        assert "data_type" not in mismatched[0].reasons
        assert "data_type" in matched[0].reasons

    async def test_rank_returns_sorted_desc(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """多候选按分数降序排列。"""
        await create_published_variable(
            standard_service,
            code="alpha.one",
            display_name="Alpha",
            data_type="number",
            canonical_unit="mm",
        )
        await create_published_variable(
            standard_service,
            code="beta.two",
            display_name="Beta",
            data_type="number",
            canonical_unit="mm",
        )

        candidates = await mapping_service.rank(
            source_name="alpha.one", source_unit="mm", data_type="number"
        )

        assert len(candidates) >= 2
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)
        # alpha.one 精确匹配 code，应排在首位
        assert candidates[0].variable_code == "alpha.one"
        assert candidates[0].score > candidates[1].score

    async def test_no_candidates_when_nothing_matches(
        self,
        mapping_service: MappingService,
        standard_service: StandardService,
    ) -> None:
        """无任何组件命中时返回空候选列表。"""
        await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
            alias="D50",
        )

        candidates = await mapping_service.rank(
            source_name="completely.unrelated",
            source_unit=None,
            data_type="text",
        )

        # 单位为 None（无 unit_dimension）、data_type 不匹配、名称不匹配 → 0 分候选被过滤
        assert candidates == ()

    async def test_no_published_variables_returns_empty(
        self,
        mapping_service: MappingService,
    ) -> None:
        """组织内无已发布变量时返回空候选列表。"""
        candidates = await mapping_service.rank(
            source_name="anything", source_unit="mm", data_type="number"
        )
        assert candidates == ()


# ---- 映射配置生命周期测试 ----


def _sample_rule(target_version_id: UUID) -> MappingRule:
    """构造一条示例映射规则。"""
    return MappingRule(
        source_path="D50",
        target_variable_version_id=target_version_id,
        source_unit="mm",
        missing_policy="default",
        default_value="0",
    )


def _file_source() -> dict:
    """构造文件数据源描述。"""
    return {"kind": "file", "file": {"path": "imports/x.csv", "format": "csv"}}


class TestMappingProfileLifecycle:
    """映射配置生命周期测试。"""

    async def test_create_and_get_profile(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """创建草稿配置后可查询，状态为 draft。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        detail = await mapping_profile_service.create_profile(
            name="粒度导入",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )

        assert detail["name"] == "粒度导入"
        assert detail["status"] == "draft"
        assert detail["version"]["version"] == 1
        assert len(detail["version"]["rules"]) == 1

        fetched = await mapping_profile_service.get_profile(UUID(detail["id"]))
        assert fetched["id"] == detail["id"]

    async def test_publish_flow(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """创建 → 提交 → 发布，发布后状态为 published。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        detail = await mapping_profile_service.create_profile(
            name="发布流程",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )
        profile_id = UUID(detail["id"])

        submitted = await mapping_profile_service.submit_profile(profile_id)
        assert submitted["status"] == "in_review"

        published = await mapping_profile_service.publish_profile(profile_id)
        assert published["status"] == "published"
        assert published["version"]["status"] == "published"
        assert published["version"]["published_at"] is not None

    async def test_reject_flow(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """创建 → 提交 → 拒绝，拒绝后状态为 rejected。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        detail = await mapping_profile_service.create_profile(
            name="拒绝流程",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )
        profile_id = UUID(detail["id"])

        await mapping_profile_service.submit_profile(profile_id)
        rejected = await mapping_profile_service.reject_profile(profile_id)
        assert rejected["status"] == "rejected"
        assert rejected["version"]["status"] == "rejected"

    async def test_published_profile_rejects_mutation(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """已发布配置拒绝规则变更（published_version_immutable）。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        detail = await mapping_profile_service.create_profile(
            name="不可变测试",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )
        profile_id = UUID(detail["id"])
        await mapping_profile_service.submit_profile(profile_id)
        await mapping_profile_service.publish_profile(profile_id)

        new_rule = MappingRule(
            source_path="temp",
            target_variable_version_id=version_id,
            source_unit="°C",
            missing_policy="null",
            default_value=None,
        )
        with pytest.raises(AppError) as exc_info:
            await mapping_profile_service.update_rules(profile_id, [new_rule])
        assert exc_info.value.code == "published_version_immutable"

    async def test_submit_invalid_transition(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """非草稿状态提交审核抛 invalid_transition。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        detail = await mapping_profile_service.create_profile(
            name="状态转换",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )
        profile_id = UUID(detail["id"])
        await mapping_profile_service.submit_profile(profile_id)

        with pytest.raises(AppError) as exc_info:
            await mapping_profile_service.submit_profile(profile_id)
        assert exc_info.value.code == "invalid_transition"

    async def test_duplicate_name_conflict(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """同名配置创建抛 conflict。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        await mapping_profile_service.create_profile(
            name="重名",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )
        with pytest.raises(AppError) as exc_info:
            await mapping_profile_service.create_profile(
                name="重名",
                source=_file_source(),
                rules=[_sample_rule(version_id)],
            )
        assert exc_info.value.code == "conflict"

    async def test_list_profiles(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """列表返回已创建的配置。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        await mapping_profile_service.create_profile(
            name="列表A",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )
        await mapping_profile_service.create_profile(
            name="列表B",
            source=_file_source(),
            rules=[_sample_rule(version_id)],
        )

        items, next_cursor = await mapping_profile_service.list_profiles(page_size=10)
        names = {item["name"] for item in items}
        assert "列表A" in names
        assert "列表B" in names

    async def test_list_profiles_pagination_cursor(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """多页分页游标正确翻页，无跳过/重复。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )
        created_names: list[str] = []
        for i in range(3):
            name = f"分页{i}"
            await mapping_profile_service.create_profile(
                name=name,
                source=_file_source(),
                rules=[_sample_rule(version_id)],
            )
            created_names.append(name)

        page1, cursor1 = await mapping_profile_service.list_profiles(page_size=2)
        assert len(page1) == 2
        assert cursor1 is not None

        page2, cursor2 = await mapping_profile_service.list_profiles(cursor=cursor1, page_size=2)
        assert len(page2) == 1
        assert cursor2 is None

        all_names = [item["name"] for item in page1] + [item["name"] for item in page2]
        assert sorted(all_names) == sorted(created_names)
        # 无重复
        assert len(all_names) == len(set(all_names))

    async def test_invalid_document_validation(
        self,
        mapping_profile_service: MappingProfileService,
        standard_service: StandardService,
    ) -> None:
        """不符合 JSON Schema 的配置创建抛 validation_failed。"""
        version_id = await create_published_variable(
            standard_service,
            code="particle.d50",
            display_name="中位径",
            data_type="number",
            canonical_unit="mm",
        )

        # 非法 missing_policy
        bad_rule = MappingRule(
            source_path="D50",
            target_variable_version_id=version_id,
            source_unit="mm",
            missing_policy="drop",  # type: ignore[arg-type]
            default_value=None,
        )
        with pytest.raises(AppError) as exc_info:
            await mapping_profile_service.create_profile(
                name="非法文档",
                source=_file_source(),
                rules=[bad_rule],
            )
        assert exc_info.value.code == "validation_failed"
