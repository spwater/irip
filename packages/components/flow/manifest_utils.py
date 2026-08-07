"""流程 manifest 构建辅助函数。

从 ComponentVersion ORM 构建 ComponentManifest（解析 manifest_yaml）。
"""

from __future__ import annotations

from typing import Any

import yaml

from packages.common.errors import AppError
from packages.components.manifest import ComponentManifest
from packages.components.registry import ComponentVersion  # type: ignore[attr-defined]


def build_manifest_from_version(
    version_row: ComponentVersion,
) -> ComponentManifest:
    """从 ComponentVersion ORM 构建 ComponentManifest（解析 manifest_yaml）。

    ComponentVersion 存储了 manifest_yaml，需要解析为 ComponentManifest
    值对象供 runner 使用。manifest 已在发布时校验过，此处不再校验。

    Args:
        version_row: 组件版本 ORM 记录。

    Returns:
        ComponentManifest: 组件清单值对象。

    Raises:
        AppError: code="invalid_manifest"，当 YAML 解析失败。
    """
    from packages.components.manifest import _parse_port_specs

    try:
        raw: Any = yaml.safe_load(version_row.manifest_yaml)
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

    dependencies_raw: list[str] | None = raw.get("dependencies")
    dependencies: tuple[str, ...] = tuple(dependencies_raw) if dependencies_raw else ()

    return ComponentManifest(
        name=raw["name"],
        display_name=raw.get("display_name", ""),
        version=raw.get("version", "auto"),
        kind=raw["kind"],
        runtime=raw.get("runtime", "python"),
        inputs=_parse_port_specs(raw.get("inputs")),
        outputs=_parse_port_specs(raw.get("outputs")),
        parameters=raw.get("parameters", {}) or {},
        dependencies=dependencies,
        raw_yaml=version_row.manifest_yaml,
        sha256=version_row.manifest_sha256,
    )
