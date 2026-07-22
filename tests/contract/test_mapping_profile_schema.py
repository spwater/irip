"""映射配置 v1 JSON Schema 契约测试。

校验 schemas/mapping-profile/v1.schema.json 的约束：
- 合法配置通过校验；
- 缺失必填字段失败；
- 敏感凭据（DSN/token）不可内联，仅允许 secret_id 引用；
- 非法 missing_policy / source kind 被拒绝。
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

_SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "mapping-profile"
    / "v1.schema.json"
)


def _load_schema() -> dict:
    """加载映射配置 JSON Schema。"""
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _validator() -> Draft202012Validator:
    """构建 JSON Schema 校验器（启用格式校验，强制 UUID 格式）。"""
    return Draft202012Validator(_load_schema(), format_checker=FormatChecker())


def _valid_document() -> dict:
    """构造一个合法的映射配置文档。"""
    return {
        "name": "颗粒粒度导入配置",
        "source": {
            "kind": "file",
            "file": {"path": "imports/particles.csv", "format": "csv"},
        },
        "rules": [
            {
                "source_path": "D50",
                "target_variable_version_id": "12345678-1234-1234-1234-123456789012",
                "source_unit": "mm",
                "missing_policy": "default",
                "default_value": "0",
            }
        ],
    }


# ---- 合法文档通过 ----


class TestValidDocuments:
    """合法文档应通过校验。"""

    def test_file_source_valid(self) -> None:
        """文件数据源合法配置通过校验。"""
        validator = _validator()
        validator.validate(_valid_document())

    def test_postgres_source_valid(self) -> None:
        """postgres 数据源仅含 secret_id 引用，通过校验。"""
        document = _valid_document()
        document["source"] = {
            "kind": "postgres",
            "postgres": {
                "secret_id": "12345678-1234-1234-1234-123456789012",
                "query": "SELECT d50, temp FROM particle",
            },
        }
        _validator().validate(document)

    def test_rest_source_valid(self) -> None:
        """rest 数据源仅含 secret_id 引用，通过校验。"""
        document = _valid_document()
        document["source"] = {
            "kind": "rest",
            "rest": {
                "secret_id": "12345678-1234-1234-1234-123456789012",
                "path": "/api/v1/particles",
                "method": "GET",
            },
        }
        _validator().validate(document)

    def test_minimal_rule_valid(self) -> None:
        """仅必填字段的规则通过校验。"""
        document = _valid_document()
        document["rules"] = [
            {
                "source_path": "temp",
                "target_variable_version_id": "12345678-1234-1234-1234-123456789012",
                "missing_policy": "reject",
            }
        ]
        _validator().validate(document)


# ---- 缺失必填字段失败 ----


class TestMissingRequired:
    """缺失必填字段应校验失败。"""

    @pytest.mark.parametrize(
        "missing",
        ["name", "source", "rules"],
    )
    def test_missing_top_level_required(self, missing: str) -> None:
        """缺失顶层必填字段失败。"""
        document = _valid_document()
        document.pop(missing)
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_missing_source_kind(self) -> None:
        """source 缺失 kind 失败。"""
        document = _valid_document()
        document["source"] = {"file": {"path": "x.csv", "format": "csv"}}
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_missing_rule_required(self) -> None:
        """规则缺失必填字段失败。"""
        document = _valid_document()
        document["rules"] = [
            {
                "source_path": "D50",
                "missing_policy": "reject",
            }
        ]
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_empty_rules_rejected(self) -> None:
        """空规则数组失败（minItems=1）。"""
        document = _valid_document()
        document["rules"] = []
        with pytest.raises(ValidationError):
            _validator().validate(document)


# ---- 敏感凭据不可内联 ----


class TestSecretIsolation:
    """敏感凭据（DSN/token）不可内联，仅允许 secret_id 引用。"""

    def test_postgres_inline_dsn_rejected(self) -> None:
        """postgres 配置内联 dsn 明文应被拒绝（additionalProperties=false）。"""
        document = _valid_document()
        document["source"] = {
            "kind": "postgres",
            "postgres": {
                "secret_id": "12345678-1234-1234-1234-123456789012",
                "query": "SELECT 1",
                "dsn": "postgresql://user:pass@host/db",
            },
        }
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_rest_inline_token_rejected(self) -> None:
        """rest 配置内联 token 明文应被拒绝（additionalProperties=false）。"""
        document = _valid_document()
        document["source"] = {
            "kind": "rest",
            "rest": {
                "secret_id": "12345678-1234-1234-1234-123456789012",
                "path": "/api",
                "method": "GET",
                "token": "secret-bearer-token",
            },
        }
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_schema_has_no_dsn_or_token_properties(self) -> None:
        """schema 定义中不应出现 dsn / token 属性。"""
        schema_text = _SCHEMA_PATH.read_text(encoding="utf-8")
        assert '"dsn"' not in schema_text
        assert '"token"' not in schema_text

    def test_only_secret_id_referenced(self) -> None:
        """postgres/rest 子对象的属性仅 secret_id 及业务字段。"""
        schema = _load_schema()
        pg_props = schema["properties"]["source"]["properties"]["postgres"][
            "properties"
        ]
        rest_props = schema["properties"]["source"]["properties"]["rest"][
            "properties"
        ]
        assert "secret_id" in pg_props
        assert "dsn" not in pg_props
        assert "secret_id" in rest_props
        assert "token" not in rest_props


# ---- 非法枚举值被拒绝 ----


class TestInvalidEnums:
    """非法枚举值应被拒绝。"""

    @pytest.mark.parametrize("policy", ["drop", "skip", "", "REJECT"])
    def test_invalid_missing_policy(self, policy: str) -> None:
        """非法 missing_policy 值失败。"""
        document = _valid_document()
        document["rules"][0]["missing_policy"] = policy
        with pytest.raises(ValidationError):
            _validator().validate(document)

    @pytest.mark.parametrize("kind", ["mongodb", "grpc", "FILE", ""])
    def test_invalid_source_kind(self, kind: str) -> None:
        """非法 source kind 失败。"""
        document = _valid_document()
        document["source"]["kind"] = kind
        with pytest.raises(ValidationError):
            _validator().validate(document)

    @pytest.mark.parametrize("fmt", ["tsv", "xls", "yaml", ""])
    def test_invalid_file_format(self, fmt: str) -> None:
        """非法文件格式失败。"""
        document = _valid_document()
        document["source"]["file"]["format"] = fmt
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_invalid_rest_method(self) -> None:
        """非法 REST method 失败。"""
        document = _valid_document()
        document["source"] = {
            "kind": "rest",
            "rest": {
                "secret_id": "12345678-1234-1234-1234-123456789012",
                "path": "/api",
                "method": "DELETE",
            },
        }
        with pytest.raises(ValidationError):
            _validator().validate(document)


# ---- 类型与格式约束 ----


class TestTypeConstraints:
    """类型与格式约束。"""

    def test_name_too_long(self) -> None:
        """name 超过 200 字符失败。"""
        document = _valid_document()
        document["name"] = "x" * 201
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_invalid_target_uuid_format(self) -> None:
        """target_variable_version_id 非合法 UUID 格式失败。"""
        document = _valid_document()
        document["rules"][0]["target_variable_version_id"] = "not-a-uuid"
        with pytest.raises(ValidationError):
            _validator().validate(document)

    def test_invalid_secret_id_format(self) -> None:
        """postgres secret_id 非合法 UUID 格式失败。"""
        document = _valid_document()
        document["source"] = {
            "kind": "postgres",
            "postgres": {"secret_id": "not-a-uuid", "query": "SELECT 1"},
        }
        with pytest.raises(ValidationError):
            _validator().validate(document)


# ---- API 序列化路径回归测试 ----


class TestApiSerializationPath:
    """验证 API 层 Pydantic 模型序列化后的文档能通过 JSON Schema 校验。

    这组测试模拟 FastAPI 解析请求体的流程：将请求 dict 解析为
    SourceSpec Pydantic 模型，再用 ``model_dump(exclude_none=True)``
    序列化（与 ``create_profile`` 端点一致），最后校验生成的完整文档。
    防止 None 兄弟字段（file/postgres/rest）导致 422 校验失败。
    """

    @staticmethod
    def _build_document_via_api(source_dict: dict) -> dict:
        """模拟 create_profile 端点的序列化路径。

        1. 请求体 → CreateProfileRequest（FastAPI 自动解析）；
        2. body.source.model_dump(exclude_none=True) → source dict；
        3. 组装完整文档供 JSON Schema 校验。
        """
        from apps.api.routers.ingestions import CreateProfileRequest

        rule_dict = {
            "source_path": "d50",
            "target_variable_version_id": "12345678-1234-1234-1234-123456789012",
            "missing_policy": "reject",
        }
        body = CreateProfileRequest.model_validate(
            {
                "name": "API 序列化测试",
                "source": source_dict,
                "rules": [rule_dict],
            }
        )
        return {
            "name": body.name,
            "source": body.source.model_dump(exclude_none=True),
            "rules": [r.model_dump(exclude_none=True) for r in body.rules],
        }

    def test_file_source_api_path_valid(self) -> None:
        """file 源经 API 序列化后通过 JSON Schema（无 None 兄弟字段）。"""
        document = self._build_document_via_api(
            {"kind": "file", "file": {"path": "/tmp/test.csv", "format": "csv"}}
        )
        _validator().validate(document)

    def test_postgres_source_api_path_valid(self) -> None:
        """postgres 源经 API 序列化后通过 JSON Schema。"""
        document = self._build_document_via_api(
            {
                "kind": "postgres",
                "postgres": {
                    "secret_id": "12345678-1234-1234-1234-123456789012",
                    "query": "SELECT 1",
                },
            }
        )
        _validator().validate(document)

    def test_rest_source_api_path_valid(self) -> None:
        """rest 源经 API 序列化后通过 JSON Schema。"""
        document = self._build_document_via_api(
            {
                "kind": "rest",
                "rest": {
                    "secret_id": "12345678-1234-1234-1234-123456789012",
                    "path": "/api",
                    "method": "GET",
                },
            }
        )
        _validator().validate(document)

    def test_file_source_no_none_siblings(self) -> None:
        """file 源序列化后不含 postgres/rest 的 None 键。"""
        document = self._build_document_via_api(
            {"kind": "file", "file": {"path": "/tmp/test.csv", "format": "csv"}}
        )
        source = document["source"]
        assert "postgres" not in source
        assert "rest" not in source
        assert source == {
            "kind": "file",
            "file": {"path": "/tmp/test.csv", "format": "csv"},
        }

    def test_old_serialization_would_fail(self) -> None:
        """回归断言：不带 exclude_none 的 model_dump 会引入 None 兄弟字段。

        确保旧 bug（422 None is not of type 'object'）不会复现。
        """
        from apps.api.routers.ingestions import SourceSpec

        spec = SourceSpec.model_validate(
            {"kind": "file", "file": {"path": "/tmp/test.csv", "format": "csv"}}
        )
        buggy = spec.model_dump()
        assert buggy.get("postgres") is None
        assert buggy.get("rest") is None
        # 修复后
        fixed = spec.model_dump(exclude_none=True)
        assert "postgres" not in fixed
        assert "rest" not in fixed
