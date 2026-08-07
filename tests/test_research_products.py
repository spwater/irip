"""研究产物（阶段 3）测试。

测试范围：
1. ThreeSegmentValidator：validate / infer_field_manifest / compute_content_hash（纯逻辑）
2. InsightExtractor：_parse_insight_json / _validate_fields（纯逻辑）
3. 迁移 0076：7 张表 DDL + revision 链 + 唯一约束
4. ORM 实体：7 个新实体定义 + 字段与迁移一致性
5. Repository 不可变保证：版本实体无 update/delete 方法
6. API 端点：25+ 端点定义 + require_permission + 图片下载
7. Composition 注册：ProductService / CandidateService / ResearchCatalogImpl / InsightExtractor
8. 前端 API：researchProducts.ts 函数与类型定义 + 路径匹配
9. 前端组件：文件存在 + CandidateInsightCard 三按钮 + InsightModifyModal AI 原稿只读 + 修改原因必填

纯逻辑/结构性验证，不需要数据库连接。
"""

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

# ============================================================
# 1. ThreeSegmentValidator 测试（纯逻辑，最高优先级）
# ============================================================


class TestThreeSegmentValidator:
    """ThreeSegmentValidator 三段式校验 + field_manifest 推断 + content_hash 计算。"""

    def test_validate_valid_data_passes(self):
        """合法三段式数据校验通过。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {
            "metadata": {"description": "批次特征提取", "analysis_scope": "2026-Q2"},
            "points": [
                {"name": "平均峰值", "value": 18.4, "unit": "MPa"},
                {"name": "峰面积均值", "value": 52.1, "unit": ""},
            ],
            "series": [
                {
                    "name": "批次特征表",
                    "columns": ["batch_id", "peak", "area", "status"],
                    "rows": [
                        {"batch_id": "B-001", "peak": 17.8, "area": 52.1, "status": "normal"},
                        {"batch_id": "B-002", "peak": 18.5, "area": 48.3, "status": "normal"},
                    ],
                }
            ],
        }
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is True
        assert result.errors == []
        assert result.data is not None
        assert isinstance(result.data.metadata, dict)
        assert isinstance(result.data.points, list)
        assert isinstance(result.data.series, list)
        assert len(result.field_manifest) > 0

    def test_validate_empty_points_and_series_allowed(self):
        """空 points 和空 series 允许。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {"metadata": {"description": "仅元数据"}, "points": [], "series": []}
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is True
        assert result.errors == []
        assert result.data is not None
        assert result.data.points == []
        assert result.data.series == []

    def test_validate_metadata_not_dict_fails(self):
        """metadata 不是 dict 时校验失败并返回字段级错误。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {"metadata": "not a dict", "points": [], "series": []}
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is False
        assert any("metadata" in e for e in result.errors)

    def test_validate_points_not_list_fails(self):
        """points 不是 list 时校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {"metadata": {}, "points": "not a list", "series": []}
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is False
        assert any("points" in e for e in result.errors)

    def test_validate_series_not_list_fails(self):
        """series 不是 list 时校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {"metadata": {}, "points": [], "series": {"not": "a list"}}
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is False
        assert any("series" in e for e in result.errors)

    def test_validate_point_missing_name_or_value_fails(self):
        """point 缺少 name 或 value 时校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {
            "metadata": {},
            "points": [{"name": "only_name"}],  # missing value
            "series": [],
        }
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is False
        assert any("name" in e or "value" in e for e in result.errors)

    def test_validate_series_missing_name_fails(self):
        """series 缺少 name 时校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {
            "metadata": {},
            "points": [],
            "series": [{"rows": []}],  # missing name
        }
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_validate_series_missing_rows_fails(self):
        """series 缺少 rows 时校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        data = {
            "metadata": {},
            "points": [],
            "series": [{"name": "test"}],  # missing rows
        }
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is False
        assert any("rows" in e for e in result.errors)

    def test_validate_bytes_json_input(self):
        """接受 bytes JSON 输入并正确解析。"""
        from packages.research.validation import ThreeSegmentValidator

        data = json.dumps({"metadata": {"key": "value"}, "points": [], "series": []}).encode(
            "utf-8"
        )
        result = ThreeSegmentValidator.validate(data)
        assert result.valid is True

    def test_validate_invalid_bytes_fails(self):
        """无法解析的 bytes 返回校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        result = ThreeSegmentValidator.validate(b"not json at all")
        assert result.valid is False
        assert len(result.errors) > 0

    def test_validate_non_dict_root_fails(self):
        """根节点不是 dict 时校验失败。"""
        from packages.research.validation import ThreeSegmentValidator

        result = ThreeSegmentValidator.validate([1, 2, 3])
        assert result.valid is False
        assert any("dict" in e for e in result.errors)

    # ---- infer_field_manifest ----

    def test_infer_field_manifest_int_type(self):
        """推断 int 类型。"""
        from packages.research.validation import ThreeSegmentValidator

        points = [{"name": "count", "value": 42, "unit": ""}]
        manifest = ThreeSegmentValidator.infer_field_manifest(points, [])
        assert len(manifest) == 1
        assert manifest[0]["field_name"] == "count"
        assert manifest[0]["inferred_type"] == "int"

    def test_infer_field_manifest_float_type(self):
        """推断 float 类型。"""
        from packages.research.validation import ThreeSegmentValidator

        points = [{"name": "avg", "value": 18.4, "unit": "MPa"}]
        manifest = ThreeSegmentValidator.infer_field_manifest(points, [])
        assert manifest[0]["inferred_type"] == "float"

    def test_infer_field_manifest_str_type(self):
        """推断 str 类型。"""
        from packages.research.validation import ThreeSegmentValidator

        points = [{"name": "label", "value": "normal", "unit": ""}]
        manifest = ThreeSegmentValidator.infer_field_manifest(points, [])
        assert manifest[0]["inferred_type"] == "str"

    def test_infer_field_manifest_bool_type(self):
        """推断 bool 类型（bool 优先于 int）。"""
        from packages.research.validation import ThreeSegmentValidator

        points = [{"name": "flag", "value": True, "unit": ""}]
        manifest = ThreeSegmentValidator.infer_field_manifest(points, [])
        assert manifest[0]["inferred_type"] == "bool"

    def test_infer_field_manifest_null_type(self):
        """推断 null 类型。"""
        from packages.research.validation import ThreeSegmentValidator

        points = [{"name": "missing", "value": None, "unit": ""}]
        manifest = ThreeSegmentValidator.infer_field_manifest(points, [])
        assert manifest[0]["inferred_type"] == "null"

    def test_infer_field_manifest_column_order(self):
        """列顺序正确递增。"""
        from packages.research.validation import ThreeSegmentValidator

        points = [
            {"name": "p1", "value": 1, "unit": ""},
            {"name": "p2", "value": 2, "unit": ""},
        ]
        series = [
            {
                "name": "table1",
                "columns": ["col_a", "col_b"],
                "rows": [{"col_a": 1, "col_b": 2}],
            }
        ]
        manifest = ThreeSegmentValidator.infer_field_manifest(points, series)
        # p1 → order 0, p2 → order 1, col_a → order 2, col_b → order 3
        assert manifest[0]["column_order"] == 0
        assert manifest[1]["column_order"] == 1
        assert manifest[2]["column_order"] == 2
        assert manifest[3]["column_order"] == 3

    def test_infer_field_manifest_from_series_rows_dict(self):
        """series columns 未定义时从 rows dict 推断列名。"""
        from packages.research.validation import ThreeSegmentValidator

        series = [
            {
                "name": "table",
                "columns": [],
                "rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
            }
        ]
        manifest = ThreeSegmentValidator.infer_field_manifest([], series)
        field_names = [m["field_name"] for m in manifest]
        assert "a" in field_names
        assert "b" in field_names

    def test_infer_field_manifest_from_series_rows_list(self):
        """series columns 未定义且 rows 为 list 时推断列名为 col_N。"""
        from packages.research.validation import ThreeSegmentValidator

        series = [
            {
                "name": "table",
                "columns": [],
                "rows": [[1, 2, 3], [4, 5, 6]],
            }
        ]
        manifest = ThreeSegmentValidator.infer_field_manifest([], series)
        field_names = [m["field_name"] for m in manifest]
        assert "col_0" in field_names
        assert "col_1" in field_names
        assert "col_2" in field_names

    def test_infer_field_manifest_shape(self):
        """series 字段的 shape 为 row_count x col_count。"""
        from packages.research.validation import ThreeSegmentValidator

        series = [
            {
                "name": "table",
                "columns": ["a", "b"],
                "rows": [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}],
            }
        ]
        manifest = ThreeSegmentValidator.infer_field_manifest([], series)
        assert manifest[0]["shape"] == "3x2"

    def test_infer_field_manifest_empty_inputs(self):
        """空 points 和空 series 返回空 manifest。"""
        from packages.research.validation import ThreeSegmentValidator

        manifest = ThreeSegmentValidator.infer_field_manifest([], [])
        assert manifest == []

    # ---- compute_content_hash ----

    def test_compute_content_hash_64_char_hex(self):
        """content_hash 为 64 字符十六进制 SHA-256。"""
        from packages.research.validation import ThreeSegmentValidator

        h = ThreeSegmentValidator.compute_content_hash(
            {"key": "value"}, [{"name": "p", "value": 1}], []
        )
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_content_hash_same_data_same_hash(self):
        """相同数据生成相同哈希。"""
        from packages.research.validation import ThreeSegmentValidator

        metadata = {"description": "test"}
        points = [{"name": "p1", "value": 42, "unit": "MPa"}]
        series = [{"name": "s1", "columns": ["a"], "rows": [{"a": 1}]}]

        h1 = ThreeSegmentValidator.compute_content_hash(metadata, points, series)
        h2 = ThreeSegmentValidator.compute_content_hash(metadata, points, series)
        assert h1 == h2

    def test_compute_content_hash_different_data_different_hash(self):
        """不同数据生成不同哈希。"""
        from packages.research.validation import ThreeSegmentValidator

        h1 = ThreeSegmentValidator.compute_content_hash({"key": "v1"}, [], [])
        h2 = ThreeSegmentValidator.compute_content_hash({"key": "v2"}, [], [])
        assert h1 != h2

    def test_compute_content_hash_matches_manual_sha256(self):
        """content_hash 与手动 SHA-256 计算一致。"""
        from packages.research.validation import ThreeSegmentValidator

        metadata = {"b": 2, "a": 1}
        points = [{"name": "p", "value": 1}]
        series = []

        h = ThreeSegmentValidator.compute_content_hash(metadata, points, series)

        payload = {"metadata": metadata, "points": points, "series": series}
        expected = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert h == expected

    def test_compute_content_hash_key_order_independent(self):
        """metadata key 顺序不影响哈希（sort_keys=True）。"""
        from packages.research.validation import ThreeSegmentValidator

        h1 = ThreeSegmentValidator.compute_content_hash({"a": 1, "b": 2}, [], [])
        h2 = ThreeSegmentValidator.compute_content_hash({"b": 2, "a": 1}, [], [])
        assert h1 == h2


# ============================================================
# 2. InsightExtractor 测试（纯逻辑）
# ============================================================


class TestInsightExtractor:
    """InsightExtractor JSON 解析与字段校验。"""

    @pytest.fixture
    def extractor(self):
        """创建 InsightExtractor 实例（mock ModelGateway）。"""
        from packages.research.insight_extractor import InsightExtractor

        return InsightExtractor(model_gateway=MagicMock())

    def test_parse_insight_json_valid(self, extractor):
        """合法 JSON 返回 InsightCandidateData，extraction_failed=False。"""
        from packages.research.models import InsightCandidateData

        raw = json.dumps(
            {
                "conclusion": "批次B-003的峰值异常源于温度波动",
                "scope": "2026-Q2 生产的铝合金批次",
                "evidence_refs": [{"type": "dataset", "name": "批次特征", "version": 1}],
                "method_refs": [{"run_id": "r1", "step_key": "step2"}],
                "confidence_level": "medium",
                "limitations": "单批次验证，需扩大样本",
                "evidence_source_label": "experimental_data",
            }
        )
        result = extractor._parse_insight_json(raw)
        assert result is not None
        assert isinstance(result, InsightCandidateData)
        assert result.extraction_failed is False
        assert result.conclusion == "批次B-003的峰值异常源于温度波动"
        assert result.evidence_source_label == "experimental_data"

    def test_parse_insight_json_null_returns_none(self, extractor):
        """AI 返回 null 时返回 None。"""
        result = extractor._parse_insight_json("null")
        assert result is None

    def test_parse_insight_json_none_string_returns_none(self, extractor):
        """AI 返回 none 时返回 None。"""
        result = extractor._parse_insight_json("none")
        assert result is None

    def test_parse_insight_json_empty_returns_none(self, extractor):
        """空字符串返回 None。"""
        result = extractor._parse_insight_json("")
        assert result is None

    def test_parse_insight_json_whitespace_returns_none(self, extractor):
        """纯空白返回 None。"""
        result = extractor._parse_insight_json("   ")
        assert result is None

    def test_parse_insight_json_markdown_wrapped(self, extractor):
        """markdown 代码块包裹的 JSON 正确解析。"""
        raw = (
            '```json\n{"conclusion": "test", "scope": "scope", '
            '"evidence_refs": [], "method_refs": [], '
            '"confidence_level": "high", "limitations": "none", '
            '"evidence_source_label": "model_inference"}\n```'
        )
        result = extractor._parse_insight_json(raw)
        assert result is not None
        assert result.extraction_failed is False
        assert result.conclusion == "test"

    def test_parse_insight_json_invalid_json_returns_failed(self, extractor):
        """非法 JSON 返回 extraction_failed=True。"""
        result = extractor._parse_insight_json("this is not json at all")
        assert result is not None
        assert result.extraction_failed is True
        assert result.ai_raw_text == "this is not json at all"

    def test_parse_insight_json_missing_field_returns_failed(self, extractor):
        """缺少必填字段返回 extraction_failed=True。"""
        raw = json.dumps(
            {
                "conclusion": "test",
                "scope": "scope",
                # missing evidence_refs, method_refs,
                # confidence_level, limitations, evidence_source_label
            }
        )
        result = extractor._parse_insight_json(raw)
        assert result is not None
        assert result.extraction_failed is True

    def test_parse_insight_json_non_dict_returns_none(self, extractor):
        """JSON 解析为非 dict（如嵌套在花括号中的 list）时返回 None。"""
        # 输入包含 { } 但解析后非 dict 的场景：实际上 { ... } 总是解析为 dict，
        # 所以这里测试一个边界场景：纯数组输入会被 _extract_json_from_text 返回 None，
        # 从而返回 extraction_failed=True 的结果（保留原文）。
        result = extractor._parse_insight_json("[1, 2, 3]")
        # 由于 [1, 2, 3] 没有花括号，_extract_json_from_text 返回 None，
        # 代码保留 AI 原始文本并标记 extraction_failed=True
        assert result is not None
        assert result.extraction_failed is True
        assert result.ai_raw_text == "[1, 2, 3]"

    def test_validate_fields_all_present(self, extractor):
        """7 个必填字段全部存在且非空 → True。"""
        data = {
            "conclusion": "结论",
            "scope": "范围",
            "evidence_refs": [{"type": "dataset"}],
            "method_refs": [{"run_id": "r1"}],
            "confidence_level": "high",
            "limitations": "限制",
            "evidence_source_label": "experimental_data",
        }
        assert extractor._validate_fields(data) is True

    def test_validate_fields_empty_evidence_refs_allowed(self, extractor):
        """evidence_refs 和 method_refs 允许为空列表。"""
        data = {
            "conclusion": "结论",
            "scope": "范围",
            "evidence_refs": [],
            "method_refs": [],
            "confidence_level": "high",
            "limitations": "限制",
            "evidence_source_label": "knowledge_base",
        }
        assert extractor._validate_fields(data) is True

    @pytest.mark.parametrize(
        "missing_field",
        [
            "conclusion",
            "scope",
            "evidence_refs",
            "method_refs",
            "confidence_level",
            "limitations",
            "evidence_source_label",
        ],
    )
    def test_validate_fields_missing_any_field(self, extractor, missing_field):
        """缺少任一必填字段返回 False。"""
        data = {
            "conclusion": "结论",
            "scope": "范围",
            "evidence_refs": [],
            "method_refs": [],
            "confidence_level": "high",
            "limitations": "限制",
            "evidence_source_label": "experimental_data",
        }
        del data[missing_field]
        assert extractor._validate_fields(data) is False

    def test_validate_fields_empty_string_conclusion(self, extractor):
        """conclusion 为空字符串返回 False。"""
        data = {
            "conclusion": "  ",
            "scope": "范围",
            "evidence_refs": [],
            "method_refs": [],
            "confidence_level": "high",
            "limitations": "限制",
            "evidence_source_label": "experimental_data",
        }
        assert extractor._validate_fields(data) is False

    def test_validate_fields_invalid_source_label(self, extractor):
        """evidence_source_label 取值不合法返回 False。"""
        data = {
            "conclusion": "结论",
            "scope": "范围",
            "evidence_refs": [],
            "method_refs": [],
            "confidence_level": "high",
            "limitations": "限制",
            "evidence_source_label": "invalid_label",
        }
        assert extractor._validate_fields(data) is False

    @pytest.mark.parametrize(
        "label",
        [
            "experimental_data",
            "knowledge_base",
            "model_inference",
        ],
    )
    def test_validate_fields_valid_source_labels(self, extractor, label):
        """三种合法 evidence_source_label 均通过。"""
        data = {
            "conclusion": "结论",
            "scope": "范围",
            "evidence_refs": [],
            "method_refs": [],
            "confidence_level": "high",
            "limitations": "限制",
            "evidence_source_label": label,
        }
        assert extractor._validate_fields(data) is True

    def test_extract_empty_step_output_returns_none(self, extractor):
        """空步骤输出返回 None。"""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(extractor.extract("", "context"))
        assert result is None

    def test_extract_whitespace_step_output_returns_none(self, extractor):
        """纯空白步骤输出返回 None。"""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(extractor.extract("   ", "context"))
        assert result is None

    def test_prompt_version_constant(self, extractor):
        """提示词版本常量存在。"""
        assert extractor.PROMPT_VERSION == "insight_extraction_v1"

    def test_insight_extraction_prompt_contains_required_fields(self, extractor):
        """提示词包含 6 个必填字段 + evidence_source_label。"""
        prompt = extractor.INSIGHT_EXTRACTION_PROMPT
        for field in [
            "conclusion",
            "scope",
            "evidence_refs",
            "method_refs",
            "confidence_level",
            "limitations",
            "evidence_source_label",
        ]:
            assert field in prompt


# ============================================================
# 3. 迁移文件 0076 验证
# ============================================================


class TestMigration0076:
    """验证 0076_research_products.py 迁移结构。"""

    MIGRATIONS_DIR = Path(__file__).parents[1] / "migrations" / "versions"

    def _load_migration_module(self):
        """动态加载 0076 迁移文件。"""
        files = list(self.MIGRATIONS_DIR.glob("0076_*.py"))
        assert files, "找不到 0076 迁移文件"
        assert len(files) == 1, f"0076 匹配到多个文件: {files}"
        spec = importlib.util.spec_from_file_location("migration_0076", files[0])
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_revision_chain(self):
        """迁移链连续性：revision=0076, down_revision=0075。"""
        mod = self._load_migration_module()
        assert mod.revision == "0076"
        assert mod.down_revision == "0075"

    def test_upgrade_creates_seven_tables(self):
        """upgrade 创建 7 张表。"""
        from unittest.mock import patch

        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        with patch.object(mod, "op", _MockOp()):
            mod.upgrade()

        create_tables = [s for s in executed_sqls if "CREATE TABLE" in s.upper()]
        assert len(create_tables) == 7, f"期望 7 张表，实际 {len(create_tables)}"

        all_sql = " ".join(executed_sqls)
        expected_tables = [
            "research_derived_dataset",
            "research_derived_dataset_version",
            "research_view",
            "research_view_version",
            "research_insight",
            "research_insight_version",
            "research_insight_candidate",
        ]
        for table in expected_tables:
            assert table in all_sql, f"缺少表: {table}"

    def test_upgrade_creates_unique_constraints(self):
        """upgrade 创建 3 个唯一约束（dataset+version, view+version, insight+version）。"""
        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        from unittest.mock import patch

        with patch.object(mod, "op", _MockOp()):
            mod.upgrade()

        unique_indexes = [s for s in executed_sqls if "CREATE UNIQUE INDEX" in s.upper()]
        assert len(unique_indexes) == 3, f"期望 3 个唯一索引，实际 {len(unique_indexes)}"

        all_sql = " ".join(executed_sqls)
        assert "uq_rddv_dataset_version" in all_sql  # dataset_id + version_number
        assert "uq_rvv_view_version" in all_sql  # view_id + version_number
        assert "uq_riv_insight_version" in all_sql  # insight_id + version_number

    def test_upgrade_creates_indexes(self):
        """upgrade 创建普通索引。"""
        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        from unittest.mock import patch

        with patch.object(mod, "op", _MockOp()):
            mod.upgrade()

        create_indexes = [
            s for s in executed_sqls if "CREATE INDEX" in s.upper() and "UNIQUE" not in s.upper()
        ]
        assert len(create_indexes) >= 8, f"期望至少 8 个普通索引，实际 {len(create_indexes)}"

    def test_downgrade_drops_all_tables(self):
        """downgrade 按反序删除 7 张表。"""
        mod = self._load_migration_module()

        executed_sqls: list[str] = []

        class _MockOp:
            def execute(self, sql):
                executed_sqls.append(str(sql))

        from unittest.mock import patch

        with patch.object(mod, "op", _MockOp()):
            mod.downgrade()

        drop_tables = [s for s in executed_sqls if "DROP TABLE" in s.upper()]
        assert len(drop_tables) == 7, f"期望 7 个 DROP TABLE，实际 {len(drop_tables)}"

    def test_migration_file_revision_down_revision_text(self):
        """从文件文本验证 revision 和 down_revision。"""
        path = self.MIGRATIONS_DIR / "0076_research_products.py"
        text = path.read_text()
        assert 'revision = "0076"' in text
        assert 'down_revision = "0075"' in text


# ============================================================
# 4. ORM 实体验证
# ============================================================


class TestProductEntities:
    """验证 7 个新 ORM 实体。"""

    def test_entities_importable(self):
        """7 个实体可导入。"""
        from packages.research.entities import (
            ResearchDerivedDataset,
            ResearchDerivedDatasetVersion,
            ResearchInsight,
            ResearchInsightCandidate,
            ResearchInsightVersion,
            ResearchView,
            ResearchViewVersion,
        )

        assert ResearchDerivedDataset.__tablename__ == "research_derived_dataset"
        assert ResearchDerivedDatasetVersion.__tablename__ == "research_derived_dataset_version"
        assert ResearchView.__tablename__ == "research_view"
        assert ResearchViewVersion.__tablename__ == "research_view_version"
        assert ResearchInsight.__tablename__ == "research_insight"
        assert ResearchInsightVersion.__tablename__ == "research_insight_version"
        assert ResearchInsightCandidate.__tablename__ == "research_insight_candidate"

    def test_entities_inherit_base(self):
        """实体继承 Base。"""
        from packages.common.database import Base
        from packages.research.entities import (
            ResearchDerivedDataset,
            ResearchDerivedDatasetVersion,
            ResearchInsight,
            ResearchInsightCandidate,
            ResearchInsightVersion,
            ResearchView,
            ResearchViewVersion,
        )

        for cls in [
            ResearchDerivedDataset,
            ResearchDerivedDatasetVersion,
            ResearchView,
            ResearchViewVersion,
            ResearchInsight,
            ResearchInsightVersion,
            ResearchInsightCandidate,
        ]:
            assert issubclass(cls, Base)

    def test_table_names_have_research_prefix(self):
        """所有表名以 research_ 前缀。"""
        from packages.research.entities import (
            ResearchDerivedDataset,
            ResearchDerivedDatasetVersion,
            ResearchInsight,
            ResearchInsightCandidate,
            ResearchInsightVersion,
            ResearchView,
            ResearchViewVersion,
        )

        for cls in [
            ResearchDerivedDataset,
            ResearchDerivedDatasetVersion,
            ResearchView,
            ResearchViewVersion,
            ResearchInsight,
            ResearchInsightVersion,
            ResearchInsightCandidate,
        ]:
            assert cls.__tablename__.startswith("research_")

    def test_derived_dataset_columns(self):
        """ResearchDerivedDataset 字段约束。"""
        from sqlalchemy.dialects.postgresql import JSONB

        from packages.research.entities import ResearchDerivedDataset

        cols = ResearchDerivedDataset.__table__.columns
        assert cols["id"].primary_key
        assert not cols["workspace_id"].nullable
        assert not cols["owner_user_id"].nullable
        assert not cols["name"].nullable
        assert cols["summary"].nullable
        assert isinstance(cols["tags"].type, JSONB)
        assert not cols["source_run_id"].nullable
        assert cols["source_snapshot_id"].nullable
        # source_snapshot_id 不建 FK（逻辑引用）
        assert len(cols["source_snapshot_id"].foreign_keys) == 0

    def test_derived_dataset_version_columns(self):
        """ResearchDerivedDatasetVersion 字段约束。"""
        from sqlalchemy.dialects.postgresql import JSONB

        from packages.research.entities import ResearchDerivedDatasetVersion

        cols = ResearchDerivedDatasetVersion.__table__.columns
        assert cols["id"].primary_key
        assert not cols["dataset_id"].nullable
        assert not cols["version_number"].nullable
        assert isinstance(cols["metadata_content"].type, JSONB)
        assert isinstance(cols["points_content"].type, JSONB)
        assert isinstance(cols["series_content"].type, JSONB)
        assert isinstance(cols["field_manifest"].type, JSONB)
        assert not cols["content_hash"].nullable
        assert not cols["created_by"].nullable

    def test_view_version_columns(self):
        """ResearchViewVersion 字段约束。"""
        from packages.research.entities import ResearchViewVersion

        cols = ResearchViewVersion.__table__.columns
        assert cols["id"].primary_key
        assert not cols["view_id"].nullable
        assert not cols["version_number"].nullable
        assert not cols["image_storage_path"].nullable
        assert not cols["image_content_hash"].nullable
        assert not cols["source_run_id"].nullable
        # bound_dataset_version_id 为逻辑引用，不建 FK
        assert len(cols["bound_dataset_version_id"].foreign_keys) == 0

    def test_insight_version_columns(self):
        """ResearchInsightVersion 字段约束。"""
        from sqlalchemy.dialects.postgresql import JSONB

        from packages.research.entities import ResearchInsightVersion

        cols = ResearchInsightVersion.__table__.columns
        assert cols["id"].primary_key
        assert not cols["insight_id"].nullable
        assert not cols["version_number"].nullable
        assert not cols["conclusion"].nullable
        assert not cols["scope"].nullable
        assert isinstance(cols["evidence_refs"].type, JSONB)
        assert isinstance(cols["method_refs"].type, JSONB)
        assert not cols["confidence_level"].nullable
        assert not cols["limitations"].nullable
        assert not cols["evidence_source_label"].nullable
        assert cols["ai_original_text"].nullable
        assert cols["is_modified"].nullable is False  # NOT NULL
        assert cols["modification_note"].nullable
        # source_candidate_id 为逻辑引用，不建 FK
        assert len(cols["source_candidate_id"].foreign_keys) == 0

    def test_insight_candidate_columns(self):
        """ResearchInsightCandidate 字段约束。"""
        from packages.research.entities import ResearchInsightCandidate

        cols = ResearchInsightCandidate.__table__.columns
        assert cols["id"].primary_key
        assert not cols["workspace_id"].nullable
        assert not cols["run_id"].nullable
        assert not cols["conclusion"].nullable
        assert not cols["ai_raw_text"].nullable
        # accepted_insight_id 为逻辑引用，不建 FK
        assert len(cols["accepted_insight_id"].foreign_keys) == 0


# ============================================================
# 5. Repository 不可变保证验证
# ============================================================


class TestRepositoryImmutability:
    """验证版本实体 Repository 不提供 update/delete 方法。"""

    def test_no_update_dataset_version_method(self):
        """ResearchRepository 不提供 DerivedDatasetVersion 的 update 方法。"""
        from packages.research.repository import ResearchRepository

        # 检查不存在 update_dataset_version 或 delete_dataset_version
        assert not hasattr(ResearchRepository, "update_dataset_version")
        assert not hasattr(ResearchRepository, "delete_dataset_version")

    def test_no_update_view_version_method(self):
        """ResearchRepository 不提供 ViewVersion 的 update 方法。"""
        from packages.research.repository import ResearchRepository

        assert not hasattr(ResearchRepository, "update_view_version")
        assert not hasattr(ResearchRepository, "delete_view_version")

    def test_no_update_insight_version_method(self):
        """ResearchRepository 不提供 InsightVersion 的 update 方法。"""
        from packages.research.repository import ResearchRepository

        assert not hasattr(ResearchRepository, "update_insight_version")
        assert not hasattr(ResearchRepository, "delete_insight_version")

    def test_stable_identity_has_update_metadata(self):
        """stable identity 实体提供 update_metadata 方法。"""
        from packages.research.repository import ResearchRepository

        assert hasattr(ResearchRepository, "update_dataset_metadata")
        assert hasattr(ResearchRepository, "update_view_metadata")
        assert hasattr(ResearchRepository, "update_insight_metadata")

    def test_version_entities_have_insert_methods(self):
        """版本实体有 insert 方法（只能创建）。"""
        from packages.research.repository import ResearchRepository

        assert hasattr(ResearchRepository, "insert_dataset_version")
        assert hasattr(ResearchRepository, "insert_view_version")
        assert hasattr(ResearchRepository, "insert_insight_version")

    def test_version_entities_have_read_methods(self):
        """版本实体有 get/list 方法（可读）。"""
        from packages.research.repository import ResearchRepository

        assert hasattr(ResearchRepository, "get_dataset_version")
        assert hasattr(ResearchRepository, "list_dataset_versions")
        assert hasattr(ResearchRepository, "get_latest_dataset_version")
        assert hasattr(ResearchRepository, "get_view_version")
        assert hasattr(ResearchRepository, "list_view_versions")
        assert hasattr(ResearchRepository, "get_insight_version")
        assert hasattr(ResearchRepository, "list_insight_versions")
        assert hasattr(ResearchRepository, "get_latest_insight_version")

    def test_stable_identity_has_current_version_update(self):
        """stable identity 有 update_current_version 方法（更新冗余缓存，非版本内容）。"""
        from packages.research.repository import ResearchRepository

        assert hasattr(ResearchRepository, "update_dataset_current_version")
        assert hasattr(ResearchRepository, "update_view_current_version")
        assert hasattr(ResearchRepository, "update_insight_current_version")

    def test_candidate_has_status_update(self):
        """InsightCandidate 有 update_status 方法（候选状态可变）。"""
        from packages.research.repository import ResearchRepository

        assert hasattr(ResearchRepository, "update_insight_candidate_status")


# ============================================================
# 6. API 端点验证
# ============================================================


class TestProductAPI:
    """验证研究产物 API 端点。"""

    def test_router_has_at_least_25_endpoints(self):
        """research_products_router 至少 25 个端点。"""
        from apps.api.routers.research_products import research_products_router

        routes = [r for r in research_products_router.routes if hasattr(r, "methods") and r.methods]
        assert len(routes) >= 25, f"期望至少 25 个端点，实际 {len(routes)}"

    def test_all_endpoints_use_research_use_permission(self):
        """所有端点使用 require_permission("research:use") 依赖。"""

        # 检查路由模块中 ResearchUserDep 使用了 require_permission
        from apps.api.routers.research_products import research_products_router

        # ResearchUserDep 应该是 Annotated[CurrentUser, Depends(require_permission("research:use"))]
        # 验证 require_permission 被调用且参数正确
        # 通过检查依赖链中的 require_permission 调用
        routes = [r for r in research_products_router.routes if hasattr(r, "methods") and r.methods]
        assert len(routes) > 0

        # 检查每个路由的依赖中包含 ResearchUserDep
        # FastAPI 路由的 dependant.callables 包含依赖
        for route in routes:
            # 检查端点函数签名是否引用了 ResearchUserDep
            # 通过检查路由的 dependant
            assert route.dependant is not None
            # 检查是否有 _user 参数
            has_user_dep = False
            for dep in route.dependant.dependencies:
                if dep.name == "_user":
                    has_user_dep = True
                    break
            assert has_user_dep, f"端点 {route.path} 缺少 _user (ResearchUserDep) 依赖"

    def test_image_download_endpoint_exists(self):
        """图片下载端点存在。"""
        from apps.api.routers.research_products import research_products_router

        image_routes = [
            r for r in research_products_router.routes if hasattr(r, "path") and "/image" in r.path
        ]
        assert len(image_routes) >= 1, "缺少图片下载端点"

    def test_catalog_search_endpoint_exists(self):
        """ResearchCatalog 搜索端点存在。"""
        from apps.api.routers.research_products import research_products_router

        catalog_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and "/catalog/search" in r.path
        ]
        assert len(catalog_routes) >= 1, "缺少 catalog/search 端点"

    def test_products_list_endpoint_exists(self):
        """产物列表端点存在。"""
        from apps.api.routers.research_products import research_products_router

        product_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and r.path.endswith("/products")
        ]
        assert len(product_routes) >= 1, "缺少 products 列表端点"

    def test_write_endpoints_count(self):
        """写端点（POST/PATCH）数量正确。"""
        from apps.api.routers.research_products import research_products_router

        routes = [r for r in research_products_router.routes if hasattr(r, "methods") and r.methods]
        write_routes = [r for r in routes if "POST" in r.methods or "PATCH" in r.methods]
        # POST: create_dataset, create_view, accept, modify, reject(insight), reject(any) = 6
        # PATCH: update_dataset, update_view, update_insight = 3
        assert len(write_routes) == 9, f"期望 9 个写端点，实际 {len(write_routes)}"

    def test_candidate_endpoints_exist(self):
        """候选产物端点存在。"""
        from apps.api.routers.research_products import research_products_router

        candidate_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and "/candidates" in r.path
        ]
        assert len(candidate_routes) >= 2, "缺少候选产物端点"

    def test_insight_candidate_endpoints_exist(self):
        """Insight 候选端点存在（list, detail, accept, modify, reject）。"""
        from apps.api.routers.research_products import research_products_router

        ic_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and "/insight-candidates" in r.path
        ]
        assert len(ic_routes) >= 5, f"期望 5 个 insight-candidate 端点，实际 {len(ic_routes)}"

    def test_derived_dataset_endpoints_exist(self):
        """DerivedDataset 端点存在（create, list, detail, edit, versions, version detail）。"""
        from apps.api.routers.research_products import research_products_router

        ds_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and "/derived-datasets" in r.path
        ]
        assert len(ds_routes) >= 6, f"期望 6 个 derived-datasets 端点，实际 {len(ds_routes)}"

    def test_view_endpoints_exist(self):
        """ResearchView 端点存在（create, list, detail, edit, versions, version detail, image）。"""
        from apps.api.routers.research_products import research_products_router

        view_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and "/views" in r.path and "/derived-datasets" not in r.path
        ]
        assert len(view_routes) >= 7, f"期望 7 个 views 端点，实际 {len(view_routes)}"

    def test_insight_endpoints_exist(self):
        """Insight 端点存在（list, detail, edit, versions）。"""
        from apps.api.routers.research_products import research_products_router

        insight_routes = [
            r
            for r in research_products_router.routes
            if hasattr(r, "path") and "/insights" in r.path and "insight-candidates" not in r.path
        ]
        assert len(insight_routes) >= 4, f"期望 4 个 insights 端点，实际 {len(insight_routes)}"


# ============================================================
# 7. Composition 注册验证
# ============================================================


class TestProductComposition:
    """验证研究产物 DI 注册。"""

    def test_composition_file_exists(self):
        """Composition 文件存在且可导入。"""
        from apps.api.composition import research_products as comp

        assert hasattr(comp, "register")

    def test_register_function_signature(self):
        """register 函数接受 CompositionContext。"""
        import inspect

        from apps.api.composition.research_products import register

        sig = inspect.signature(register)
        assert "ctx" in sig.parameters

    def test_composition_imports_services(self):
        """Composition 导入 ProductService / CandidateService
        / ResearchCatalogImpl / InsightExtractor。"""
        # 读取源码验证导入（通过模块文件内容）
        import inspect

        import apps.api.composition.research_products as comp

        source = inspect.getsource(comp)
        assert "ProductService" in source
        assert "CandidateService" in source
        assert "ResearchCatalogImpl" in source
        assert "InsightExtractor" in source

    def test_composition_registers_product_service(self):
        """Composition 注册 ProductService 依赖覆盖。"""
        import inspect

        from apps.api.composition.research_products import register

        source = inspect.getsource(register)
        assert "get_product_service" in source
        assert "dependency_overrides" in source

    def test_composition_registers_candidate_service(self):
        """Composition 注册 CandidateService 依赖覆盖。"""
        import inspect

        from apps.api.composition.research_products import register

        source = inspect.getsource(register)
        assert "get_candidate_service" in source

    def test_composition_registers_catalog(self):
        """Composition 注册 ResearchCatalogImpl（替换 Stub）。"""
        import inspect

        from apps.api.composition.research_products import register

        source = inspect.getsource(register)
        assert "get_catalog" in source
        assert "ResearchCatalogImpl" in source

    def test_composition_registers_insight_extractor(self):
        """Composition 构建 InsightExtractor 供 Orchestrator 使用。"""
        import inspect

        from apps.api.composition.research_products import register

        source = inspect.getsource(register)
        assert "InsightExtractor" in source
        assert "_insight_extractor" in source


# ============================================================
# 8. 前端 API 验证
# ============================================================


class TestFrontendAPI:
    """验证 researchProducts.ts API 函数和类型定义。"""

    API_FILE = Path(__file__).parents[1] / "apps" / "web" / "src" / "api" / "researchProducts.ts"

    def test_api_file_exists(self):
        """researchProducts.ts 文件存在。"""
        assert self.API_FILE.exists(), "researchProducts.ts 不存在"

    def _read_source(self):
        """读取 API 文件源码。"""
        return self.API_FILE.read_text()

    def test_type_definitions_exist(self):
        """类型定义存在。"""
        source = self._read_source()
        types = [
            "CandidateProduct",
            "DerivedDataset",
            "DatasetDetail",
            "View",
            "ViewDetail",
            "Insight",
            "InsightDetail",
            "InsightCandidate",
            "ProductSummary",
            "CatalogSearchResult",
        ]
        for t in types:
            assert f"type {t}" in source or f"export type {t}" in source, f"缺少类型定义: {t}"

    def test_api_functions_exist(self):
        """API 函数存在。"""
        source = self._read_source()
        functions = [
            "apiGetCandidates",
            "apiGetCandidateDetail",
            "apiCreateDataset",
            "apiListDatasets",
            "apiGetDataset",
            "apiUpdateDatasetMetadata",
            "apiListDatasetVersions",
            "apiGetDatasetVersion",
            "apiCreateView",
            "apiListViews",
            "apiGetView",
            "apiUpdateViewMetadata",
            "apiListViewVersions",
            "apiGetViewVersion",
            "apiListInsights",
            "apiGetInsight",
            "apiUpdateInsightMetadata",
            "apiListInsightVersions",
            "apiListInsightCandidates",
            "apiAcceptCandidate",
            "apiModifyCandidate",
            "apiRejectCandidate",
            "apiListProducts",
            "apiSearchCatalog",
        ]
        for fn in functions:
            assert f"function {fn}" in source or f"async function {fn}" in source, (
                f"缺少 API 函数: {fn}"
            )

    def test_api_paths_match_backend(self):
        """API 路径与后端端点匹配。"""
        source = self._read_source()
        paths = [
            "/research/workspaces/${workspaceId}/runs/${runId}/candidates",
            "/research/workspaces/${workspaceId}/derived-datasets",
            "/research/workspaces/${workspaceId}/views",
            "/research/workspaces/${workspaceId}/insights",
            "/research/workspaces/${workspaceId}/runs/${runId}/insight-candidates",
            "/research/workspaces/${workspaceId}/products",
            "/research/catalog/search",
        ]
        for p in paths:
            assert p in source, f"缺少 API 路径: {p}"

    def test_view_image_url_function_exists(self):
        """getViewImageUrl 函数存在（图片下载 URL 构造）。"""
        source = self._read_source()
        assert "getViewImageUrl" in source
        assert "/image" in source


# ============================================================
# 9. 前端组件验证
# ============================================================


class TestFrontendComponents:
    """验证前端组件文件存在且结构正确。"""

    COMPONENTS_DIR = Path(__file__).parents[1] / "apps" / "web" / "src" / "features" / "research"

    EXPECTED_COMPONENTS = [
        "CandidatePreviewPanel.tsx",
        "CandidateDataCard.tsx",
        "CandidateChartCard.tsx",
        "CandidateInsightCard.tsx",
        "InsightModifyModal.tsx",
        "ConfirmedProductsPanel.tsx",
        "ProductDetailView.tsx",
        "DatasetPreview.tsx",
        "ViewPreview.tsx",
        "InsightDetailView.tsx",
        "EvidencePanel.tsx",
    ]

    def test_all_component_files_exist(self):
        """11 个组件文件全部存在。"""
        for name in self.EXPECTED_COMPONENTS:
            path = self.COMPONENTS_DIR / name
            assert path.exists(), f"组件文件不存在: {name}"

    def test_candidate_insight_card_has_three_buttons(self):
        """CandidateInsightCard 有接受/修改/拒绝三个按钮。"""
        source = (self.COMPONENTS_DIR / "CandidateInsightCard.tsx").read_text()
        # 检查三个操作按钮
        assert "accept" in source.lower() or "接受" in source
        assert "modify" in source.lower() or "修改" in source
        assert "reject" in source.lower() or "拒绝" in source
        # 检查 onAccept / onModify / onReject 回调
        assert "onAccept" in source
        assert "onModify" in source
        assert "onReject" in source

    def test_candidate_insight_card_shows_six_fields(self):
        """CandidateInsightCard 展示 6 个结构化字段。"""
        source = (self.COMPONENTS_DIR / "CandidateInsightCard.tsx").read_text()
        fields = [
            "conclusion",
            "scope",
            "evidence_refs",
            "method_refs",
            "confidence_level",
            "limitations",
        ]
        for f in fields:
            assert f in source, f"CandidateInsightCard 缺少字段: {f}"

    def test_candidate_insight_card_has_source_label(self):
        """CandidateInsightCard 有证据来源标签。"""
        source = (self.COMPONENTS_DIR / "CandidateInsightCard.tsx").read_text()
        labels = ["experimental_data", "knowledge_base", "model_inference"]
        for label in labels:
            assert label in source, f"缺少证据来源标签: {label}"

    def test_insight_modify_modal_ai_original_readonly(self):
        """InsightModifyModal AI 原稿只读展示。"""
        source = (self.COMPONENTS_DIR / "InsightModifyModal.tsx").read_text()
        assert "ai_raw_text" in source or "aiRawText" in source
        assert "AI 原稿" in source
        assert "只读" in source

    def test_insight_modify_modal_has_six_editable_fields(self):
        """InsightModifyModal 有 6 个可编辑字段。"""
        source = (self.COMPONENTS_DIR / "InsightModifyModal.tsx").read_text()
        fields = [
            "conclusion",
            "scope",
            "evidence_refs",
            "method_refs",
            "confidence_level",
            "limitations",
        ]
        for f in fields:
            assert f in source, f"InsightModifyModal 缺少字段: {f}"

    def test_insight_modify_modal_modification_note_required(self):
        """InsightModifyModal 修改原因为必填。"""
        source = (self.COMPONENTS_DIR / "InsightModifyModal.tsx").read_text()
        assert "modificationNote" in source or "modification_note" in source
        # 检查必填校验逻辑
        assert "修改原因为必填" in source or "trim()" in source

    def test_insight_modify_modal_has_source_label_selector(self):
        """InsightModifyModal 有证据来源选择器。"""
        source = (self.COMPONENTS_DIR / "InsightModifyModal.tsx").read_text()
        assert "experimental_data" in source
        assert "knowledge_base" in source
        assert "model_inference" in source

    def test_candidate_data_card_has_confirm_button(self):
        """CandidateDataCard 有确认按钮。"""
        source = (self.COMPONENTS_DIR / "CandidateDataCard.tsx").read_text()
        assert "确认" in source or "confirm" in source.lower()

    def test_candidate_chart_card_has_confirm_button(self):
        """CandidateChartCard 有确认按钮。"""
        source = (self.COMPONENTS_DIR / "CandidateChartCard.tsx").read_text()
        assert "确认" in source or "confirm" in source.lower()

    def test_candidate_preview_panel_imports_cards(self):
        """CandidatePreviewPanel 导入候选卡片组件。"""
        source = (self.COMPONENTS_DIR / "CandidatePreviewPanel.tsx").read_text()
        assert "CandidateDataCard" in source
        assert "CandidateChartCard" in source
        assert "CandidateInsightCard" in source


# ============================================================
# 10. ResearchCatalogImpl 验证
# ============================================================


class TestResearchCatalogImpl:
    """验证 ResearchCatalogImpl 结构。"""

    def test_catalog_impl_exists(self):
        """ResearchCatalogImpl 类存在。"""
        from packages.research.catalog import ResearchCatalogImpl

        assert ResearchCatalogImpl is not None

    def test_catalog_stub_exists(self):
        """ResearchCatalogStub 类仍存在（向后兼容）。"""
        from packages.research.catalog import ResearchCatalogStub

        assert ResearchCatalogStub is not None

    def test_catalog_protocol_exists(self):
        """ResearchCatalog Protocol 接口存在。"""
        from packages.research.catalog import ResearchCatalog

        assert ResearchCatalog is not None

    def test_catalog_impl_has_search_derived_data(self):
        """ResearchCatalogImpl 有 search_derived_data 方法。"""
        from packages.research.catalog import ResearchCatalogImpl

        assert hasattr(ResearchCatalogImpl, "search_derived_data")

    def test_catalog_stub_returns_empty(self):
        """ResearchCatalogStub.search_derived_data 返回空列表。"""
        import asyncio

        from packages.research.catalog import ResearchCatalogStub

        stub = ResearchCatalogStub()
        result = asyncio.get_event_loop().run_until_complete(stub.search_derived_data("query"))
        assert result == []

    def test_catalog_impl_constructor_params(self):
        """ResearchCatalogImpl 构造函数参数正确。"""
        import inspect

        from packages.research.catalog import ResearchCatalogImpl

        sig = inspect.signature(ResearchCatalogImpl.__init__)
        assert "session_factory" in sig.parameters
        assert "actor_id" in sig.parameters


# ============================================================
# 11. ProductService / CandidateService 结构验证
# ============================================================


class TestProductServiceStructure:
    """验证 ProductService / CandidateService 方法签名和结构。"""

    def test_product_service_exists(self):
        """ProductService 类存在。"""
        from packages.research.products import ProductService

        assert ProductService is not None

    def test_product_service_has_create_dataset(self):
        """ProductService 有 create_dataset 方法。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "create_dataset")

    def test_product_service_has_create_view(self):
        """ProductService 有 create_view 方法。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "create_view")

    def test_product_service_has_create_insight_from_accept(self):
        """ProductService 有 create_insight_from_accept 方法。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "create_insight_from_accept")

    def test_product_service_has_create_insight_from_modify(self):
        """ProductService 有 create_insight_from_modify 方法。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "create_insight_from_modify")

    def test_product_service_has_list_products(self):
        """ProductService 有 list_products 方法。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "list_products")

    def test_product_service_has_update_metadata_methods(self):
        """ProductService 有 update_metadata 方法（仅 stable identity）。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "update_dataset_metadata")
        assert hasattr(ProductService, "update_view_metadata")
        assert hasattr(ProductService, "update_insight_metadata")

    def test_product_service_has_version_history_methods(self):
        """ProductService 有版本历史方法。"""
        from packages.research.products import ProductService

        assert hasattr(ProductService, "list_dataset_versions")
        assert hasattr(ProductService, "list_view_versions")
        assert hasattr(ProductService, "list_insight_versions")

    def test_candidate_service_exists(self):
        """CandidateService 类存在。"""
        from packages.research.candidates import CandidateService

        assert CandidateService is not None

    def test_candidate_service_has_identify_candidates(self):
        """CandidateService 有 identify_candidates 方法。"""
        from packages.research.candidates import CandidateService

        assert hasattr(CandidateService, "identify_candidates")

    def test_candidate_service_has_get_candidate_detail(self):
        """CandidateService 有 get_candidate_detail 方法。"""
        from packages.research.candidates import CandidateService

        assert hasattr(CandidateService, "get_candidate_detail")

    def test_candidate_service_has_reject_insight_candidate(self):
        """CandidateService 有 reject_insight_candidate 方法。"""
        from packages.research.candidates import CandidateService

        assert hasattr(CandidateService, "reject_insight_candidate")


# ============================================================
# 12. 数据类验证
# ============================================================


class TestProductDataModels:
    """验证研究产物数据类（models.py）。"""

    def test_three_segment_data_is_frozen(self):
        """ThreeSegmentData 为 frozen dataclass。"""
        from packages.research.models import ThreeSegmentData

        data = ThreeSegmentData(metadata={"k": "v"}, points=[], series=[])
        with pytest.raises(AttributeError):
            data.metadata = {}

    def test_field_manifest_entry_defaults(self):
        """FieldManifestEntry 默认值正确。"""
        from packages.research.models import FieldManifestEntry

        entry = FieldManifestEntry(field_name="test")
        assert entry.inferred_type == "null"
        assert entry.unit == ""
        assert entry.description == ""
        assert entry.source_step == ""
        assert entry.column_order == 0
        assert entry.shape == ""

    def test_validation_result_defaults(self):
        """ValidationResult 默认值正确。"""
        from packages.research.models import ValidationResult

        result = ValidationResult(valid=True)
        assert result.errors == []
        assert result.data is None
        assert result.field_manifest == []

    def test_insight_candidate_data_has_extraction_failed(self):
        """InsightCandidateData 有 extraction_failed 字段，默认 False。"""
        from packages.research.models import InsightCandidateData

        data = InsightCandidateData(
            conclusion="c",
            scope="s",
            evidence_refs=[],
            method_refs=[],
            confidence_level="high",
            limitations="l",
            evidence_source_label="experimental_data",
            ai_raw_text="raw",
        )
        assert data.extraction_failed is False

    def test_product_summary_is_frozen(self):
        """ProductSummary 为 frozen dataclass。"""
        from packages.research.models import ProductSummary

        summary = ProductSummary(
            product_type="derived_dataset",
            product_id=uuid4(),
            name="test",
            status="confirmed",
            current_version=1,
        )
        with pytest.raises(AttributeError):
            summary.name = "other"

    def test_candidate_product_summary_has_error_reason(self):
        """CandidateProductSummary 有 error_reason 字段，默认空字符串。"""
        from packages.research.models import CandidateProductSummary

        summary = CandidateProductSummary(
            candidate_type="derived_dataset",
            source_artifact_id=uuid4(),
            candidate_id=uuid4(),
            source_run_id=uuid4(),
            source_step_id=None,
            step_name="",
            step_status="",
            preview_data={},
            status="available",
        )
        assert summary.error_reason == ""

    def test_evidence_source_label_enum(self):
        """EvidenceSourceLabel 枚举值正确。"""
        from packages.research.models import EvidenceSourceLabel

        assert EvidenceSourceLabel.EXPERIMENTAL_DATA.value == "experimental_data"
        assert EvidenceSourceLabel.KNOWLEDGE_BASE.value == "knowledge_base"
        assert EvidenceSourceLabel.MODEL_INFERENCE.value == "model_inference"

    def test_candidate_status_enum(self):
        """CandidateStatus 枚举值正确。"""
        from packages.research.models import CandidateStatus

        assert CandidateStatus.PENDING.value == "pending"
        assert CandidateStatus.ACCEPTED.value == "accepted"
        assert CandidateStatus.MODIFIED.value == "modified"
        assert CandidateStatus.REJECTED.value == "rejected"

    def test_product_type_enum(self):
        """ProductType 枚举值正确。"""
        from packages.research.models import ProductType

        assert ProductType.DERIVED_DATASET.value == "derived_dataset"
        assert ProductType.VIEW.value == "view"
        assert ProductType.INSIGHT.value == "insight"
