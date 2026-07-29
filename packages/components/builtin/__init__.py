"""IRIP 内置组件包（V2-T02 / V2-T04）。

提供 30 个开箱即用的组件实现，涵盖数据摄入、映射转换、
质量检查、统计分析、输出与模型六大类别。

注册机制：
- ``register_builtin_components(runner)`` 将所有内置组件
  注册到 PythonComponentRunner 的内存注册表；
- 每个组件由 manifest（name + version）与实现实例配对注册；
- 组件清单 YAML 位于 ``schemas/component-manifest/`` 目录。

组件清单（30 个）：
- ingestion (7): excel_reader, csv_reader, json_reader,
  pdf_table_reader, postgres_query, rest_fetch, minio_object,
  ez_scan_extractor
- transform (7): field_mapper, unit_converter, missing_values,
  time_alignment, resampler, mad_outliers, steady_window
- quality (4): schema_check, range_check, particle_order,
  relation_completeness
- statistics (4): descriptive, robust_estimator, bootstrap_interval,
  curve_fit
- output (3): parameter_card, experiment_comparison, report_draft
- model (4): model_train, model_evaluate, model_applicability,
  model_predict
"""

from pathlib import Path
from typing import Any

from packages.components.builtin.ingestion.csv_reader import CSVReader
from packages.components.builtin.ingestion.excel_reader import ExcelReader
from packages.components.builtin.ingestion.ez_scan_extractor import EZScanExtractor
from packages.components.builtin.ingestion.json_reader import JSONReader
from packages.components.builtin.ingestion.minio_object import MinioObject
from packages.components.builtin.ingestion.pdf_table_reader import (
    PDFTableReader,
)
from packages.components.builtin.ingestion.postgres_query import PostgresQuery
from packages.components.builtin.ingestion.rest_fetch import RESTFetch
from packages.components.builtin.ingestion.xrd_tool_component import XrdToolComponent
from packages.components.builtin.model.applicability import (
    ModelApplicabilityComponent,
)
from packages.components.builtin.model.evaluate import ModelEvaluateComponent
from packages.components.builtin.model.predict import ModelPredictComponent
from packages.components.builtin.model.train import ModelTrainComponent
from packages.components.builtin.output.experiment_comparison import (
    ExperimentComparison,
)
from packages.components.builtin.output.parameter_card import ParameterCard
from packages.components.builtin.output.report_draft import ReportDraft
from packages.components.builtin.quality.particle_order import ParticleOrder
from packages.components.builtin.quality.range_check import RangeCheck
from packages.components.builtin.quality.relation_completeness import (
    RelationCompleteness,
)
from packages.components.builtin.quality.schema_check import SchemaCheck
from packages.components.builtin.statistics.bootstrap_interval import (
    BootstrapInterval,
)
from packages.components.builtin.statistics.curve_fit import CurveFit
from packages.components.builtin.statistics.descriptive import Descriptive
from packages.components.builtin.statistics.robust_estimator import (
    RobustEstimator,
)
from packages.components.builtin.transform.field_mapper import FieldMapper
from packages.components.builtin.transform.mad_outliers import MADOutliers
from packages.components.builtin.transform.missing_values import MissingValues
from packages.components.builtin.transform.resampler import Resampler
from packages.components.builtin.transform.steady_window import SteadyWindow
from packages.components.builtin.transform.time_alignment import TimeAlignment
from packages.components.builtin.transform.unit_converter import UnitConverter
from packages.components.manifest import ComponentManifest, ManifestValidator
from packages.components.sdk import Component  # noqa: F401

#: 内置组件清单目录。
_MANIFEST_DIR: Path = Path(__file__).parent.parent.parent.parent / "schemas" / "component-manifest"

#: 内置组件注册表（name → (version, impl_class)）。
_BUILTIN_COMPONENTS: dict[str, tuple[str, type]] = {
    # ingestion
    "excel_reader": ("1.0.0", ExcelReader),
    "csv_reader": ("1.0.0", CSVReader),
    "json_reader": ("1.0.0", JSONReader),
    "pdf_table_reader": ("1.0.0", PDFTableReader),
    "postgres_query": ("1.0.0", PostgresQuery),
    "rest_fetch": ("1.0.0", RESTFetch),
    "minio_object": ("1.0.0", MinioObject),
    "ez_scan_extractor": ("1.3.1", EZScanExtractor),
    "aez_scan_extractor": ("1.4.0", EZScanExtractor),
    "xrf_ez_extractor": ("1.4.4", EZScanExtractor),
    "xrd_tool": ("1.0.0", XrdToolComponent),
    # transform
    "field_mapper": ("1.0.0", FieldMapper),
    "unit_converter": ("1.0.0", UnitConverter),
    "missing_values": ("1.0.0", MissingValues),
    "time_alignment": ("1.0.0", TimeAlignment),
    "resampler": ("1.0.0", Resampler),
    "mad_outliers": ("1.0.0", MADOutliers),
    "steady_window": ("1.0.0", SteadyWindow),
    # quality
    "schema_check": ("1.0.0", SchemaCheck),
    "range_check": ("1.0.0", RangeCheck),
    "particle_order": ("1.0.0", ParticleOrder),
    "relation_completeness": ("1.0.0", RelationCompleteness),
    # statistics
    "descriptive": ("1.0.0", Descriptive),
    "robust_estimator": ("1.0.0", RobustEstimator),
    "bootstrap_interval": ("1.0.0", BootstrapInterval),
    "curve_fit": ("1.0.0", CurveFit),
    # output
    "parameter_card": ("1.0.0", ParameterCard),
    "experiment_comparison": ("1.0.0", ExperimentComparison),
    "report_draft": ("1.0.0", ReportDraft),
    # model (V2-T04)
    "model_train": ("1.0.0", ModelTrainComponent),
    "model_evaluate": ("1.0.0", ModelEvaluateComponent),
    "model_applicability": ("1.0.0", ModelApplicabilityComponent),
    "model_predict": ("1.0.0", ModelPredictComponent),
}

#: YAML 文件名映射（name → yaml_filename）。
_YAML_FILES: dict[str, str] = {
    "excel_reader": "excel-reader.yaml",
    "csv_reader": "csv-reader.yaml",
    "json_reader": "json-reader.yaml",
    "pdf_table_reader": "pdf-table-reader.yaml",
    "postgres_query": "postgres-query.yaml",
    "rest_fetch": "rest-fetch.yaml",
    "minio_object": "minio-object.yaml",
    "ez_scan_extractor": "ez-scan-extractor.yaml",
    "aez_scan_extractor": "ez-scan-extractor.yaml",
    "xrf_ez_extractor": "ez-scan-extractor.yaml",
    "xrd_tool": "xrd-tool.yaml",
    "field_mapper": "field-mapper.yaml",
    "unit_converter": "unit-converter.yaml",
    "missing_values": "missing-values.yaml",
    "time_alignment": "time-alignment.yaml",
    "resampler": "resampler.yaml",
    "mad_outliers": "mad-outliers.yaml",
    "steady_window": "steady-window.yaml",
    "schema_check": "schema-check.yaml",
    "range_check": "range-check.yaml",
    "particle_order": "particle-order.yaml",
    "relation_completeness": "relation-completeness.yaml",
    "descriptive": "descriptive.yaml",
    "robust_estimator": "robust-estimator.yaml",
    "bootstrap_interval": "bootstrap-interval.yaml",
    "curve_fit": "curve-fit.yaml",
    "parameter_card": "parameter-card.yaml",
    "experiment_comparison": "experiment-comparison.yaml",
    "report_draft": "report-draft.yaml",
    "model_train": "model-train.yaml",
    "model_evaluate": "model-evaluate.yaml",
    "model_applicability": "model-applicability.yaml",
    "model_predict": "model-predict.yaml",
}


def _load_manifest(name: str) -> ComponentManifest:
    """加载并校验内置组件清单 YAML。

    Args:
        name: 组件名称。

    Returns:
        ComponentManifest: 校验通过的清单。

    Raises:
        FileNotFoundError: 当 YAML 文件不存在。
        AppError: 当清单校验失败。
    """
    yaml_path = _MANIFEST_DIR / _YAML_FILES[name]
    yaml_text = yaml_path.read_text(encoding="utf-8")
    validator = ManifestValidator(_MANIFEST_DIR.parent / "component-manifest" / "v1.schema.json")
    return validator.validate(yaml_text)


def register_builtin_components(runner: Any) -> dict[str, ComponentManifest]:
    """将所有内置组件注册到 PythonComponentRunner。

    Args:
        runner: PythonComponentRunner 实例。

    Returns:
        dict[str, ComponentManifest]: 组件名 → 清单映射。
    """
    manifests: dict[str, ComponentManifest] = {}
    for name, (version, impl_cls) in _BUILTIN_COMPONENTS.items():
        manifest = _load_manifest(name)
        impl = impl_cls()
        # 如果 YAML 里的 name 和期望的 name 不同（别名组件），
        # 用期望的 name 和 version 直接注册到 runner
        if manifest.name != name or manifest.version != version:
            runner._registry[(name, version)] = impl
        else:
            runner.register(manifest, impl)
        manifests[name] = manifest
    return manifests


def list_builtin_components() -> list[str]:
    """列出所有内置组件名称。

    Returns:
        list[str]: 组件名称列表。
    """
    return list(_BUILTIN_COMPONENTS.keys())
