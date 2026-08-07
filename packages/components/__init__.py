"""IRIP 组件系统包。

提供组件清单校验、注册表服务、运行器（Python / CLI）等能力，
支撑数据管线可插拔组件架构。
"""

# re-export 核心模块供 from packages.components import X 使用
from packages.components.flow import (  # type: ignore[attr-defined]  # noqa: F401
    PROTECTED_PARAMS,
    FlowDefinition,
    FlowDefinitionVersion,
    FlowDefinitionVersionORM,
    FlowEdge,
    FlowNode,
    FlowNodeExecution,
    FlowRun,
    FlowRuntimeService,
    FlowValidationService,
    ValidationResult,
    compute_flow_digest,
    edge_from_dict,
    edge_to_dict,
    edges_from_json,
    edges_to_json,
    node_from_dict,
    node_to_dict,
    nodes_from_json,
    nodes_to_json,
)
from packages.components.manifest import (  # noqa: F401
    ComponentManifest,
    ManifestValidator,
)
from packages.components.registry import (  # type: ignore[attr-defined]  # noqa: F401
    ComponentRegistryService,
    ComponentVersion,
)
from packages.components.runner import (  # type: ignore[attr-defined]  # noqa: F401
    CLIComponentRunner,
    PythonComponentRunner,
)
from packages.components.sdk import (  # noqa: F401
    Component,
    ComponentContext,
    ComponentResult,
    ComponentRunner,
    PortSpec,
)
