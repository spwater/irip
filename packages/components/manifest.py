"""IRIP 组件清单：解析、校验、摘要计算。

提供：
- ComponentManifest: 不可变清单值对象（解析后的 YAML）；
- ManifestValidator: 从 YAML 文本解析 → JSON Schema 校验
  → SHA-256 摘要 → 构建 ComponentManifest。

设计要点（IRIP V2-T01）：
- 清单 YAML 文本的 SHA-256 摘要作为内容寻址键，相同内容 → 相同摘要；
- PortSpec 从 dict 列表转为 tuple，保证不可变；
- parameters 字段保留为 dict（JSON Schema 对象），不做深度转换；
- 校验失败统一抛出 AppError(code="invalid_manifest")，携带路径信息。
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from packages.common.errors import AppError
from packages.components.sdk import PortSpec


@dataclass(frozen=True)
class ComponentManifest:
    """组件清单（解析后的不可变值对象）。

    Attributes:
        name: 组件名称（小写字母/数字/下划线）。
        version: 语义化版本（如 ``1.0.0``）。
        kind: 组件类别
            （ingestion/transform/quality/statistics/output/model）。
        runtime: 运行时类型（python/cli）。
        inputs: 输入端口元组。
        outputs: 输出端口元组。
        parameters: 参数 JSON Schema（dict）。
        dependencies: 依赖组件列表
            （如 ``("field_mapper@1.0.0",)``）。
        raw_yaml: 原始 YAML 文本（用于摘要计算与持久化）。
        sha256: 原始 YAML 文本的 SHA-256 摘要（hex 小写）。
    """

    name: str
    display_name: str
    version: str
    kind: str
    runtime: str
    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]
    parameters: dict[str, Any]
    dependencies: tuple[str, ...]
    raw_yaml: str
    sha256: str


def _parse_port_specs(
    raw_list: list[dict[str, Any]] | None,
) -> tuple[PortSpec, ...]:
    """将原始端口字典列表转换为 PortSpec 元组。

    Args:
        raw_list: YAML 解析后的端口字典列表，None 时返回空元组。

    Returns:
        tuple[PortSpec, ...]: PortSpec 不可变元组。
    """
    if raw_list is None:
        return ()
    result: list[PortSpec] = []
    for item in raw_list:
        spec = PortSpec(
            name=item["name"],
            data_type=item["data_type"],
            required=item.get("required", True),
            schema=item.get("schema"),
        )
        result.append(spec)
    return tuple(result)


class ManifestValidator:
    """组件清单校验器。

    使用 JSON Schema 校验 YAML 格式的组件清单，计算内容摘要，
    并构建不可变的 ComponentManifest 值对象。

    Attributes:
        _schema_path: JSON Schema 文件路径。
        _schema: 已加载的 JSON Schema 字典。
    """

    def __init__(self, schema_path: Path) -> None:
        """初始化校验器。

        Args:
            schema_path: JSON Schema 文件路径
                （如 ``schemas/component-manifest/v1.schema.json``）。
        """
        self._schema_path = schema_path
        with open(schema_path, encoding="utf-8") as f:
            self._schema: dict[str, Any] = json.load(f)

    def validate(self, yaml_text: str) -> ComponentManifest:
        """校验 YAML 清单文本，返回 ComponentManifest。

        流程：
        1. yaml.safe_load 解析 YAML；
        2. jsonschema.validate 校验结构；
        3. 计算原始 YAML 文本的 SHA-256 摘要；
        4. 构建 frozen dataclass ComponentManifest。

        Args:
            yaml_text: YAML 格式的组件清单文本。

        Returns:
            ComponentManifest: 校验通过的清单值对象。

        Raises:
            AppError: code="invalid_manifest"，当 YAML 解析或
                Schema 校验失败。
        """
        # ---- 1. 解析 YAML ----
        try:
            raw: Any = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise AppError(
                code="invalid_manifest",
                message=f"清单 YAML 解析失败: {exc}",
                retryable=False,
                fields={},
            ) from exc

        if not isinstance(raw, dict):
            raise AppError(
                code="invalid_manifest",
                message="清单根节点必须为对象（mapping）",
                retryable=False,
                fields={},
            )

        # ---- 2. JSON Schema 校验 ----
        try:
            jsonschema.validate(instance=raw, schema=self._schema)
        except jsonschema.ValidationError as exc:
            raise AppError(
                code="invalid_manifest",
                message=f"清单校验失败: {exc.message}",
                retryable=False,
                fields={"path": list(exc.absolute_path)},
            ) from exc

        # ---- 3. SHA-256 摘要 ----
        sha256: str = hashlib.sha256(
            yaml_text.encode("utf-8")
        ).hexdigest()

        # ---- 4. 构建 ComponentManifest ----
        inputs: tuple[PortSpec, ...] = _parse_port_specs(
            raw.get("inputs")
        )
        outputs: tuple[PortSpec, ...] = _parse_port_specs(
            raw.get("outputs")
        )
        dependencies_raw: list[str] | None = raw.get("dependencies")
        dependencies: tuple[str, ...] = (
            tuple(dependencies_raw) if dependencies_raw else ()
        )
        parameters: dict[str, Any] = raw.get("parameters", {}) or {}

        return ComponentManifest(
            name=raw["name"],
            display_name=raw.get("display_name", ""),
            version=raw.get("version", "0.0.0"),
            kind=raw["kind"],
            runtime=raw.get("runtime", "python"),
            inputs=inputs,
            outputs=outputs,
            parameters=parameters,
            dependencies=dependencies,
            raw_yaml=yaml_text,
            sha256=sha256,
        )
