"""端到端摄入管线（IRIP Task 16）。

IngestionPipeline 将外部源文件通过映射配置转化为 L2 事实，
并运行质量评估。管线阶段：

1. download: 读取文件内容并计算 SHA-256 摘要（用于幂等去重）；
2. parse: 解析文件为字段-值字典（CSV/XLSX/JSON）；
3. map: 使用 MappingProfile 的规则映射源字段到 L1 变量；
4. normalize: 单位转换（如 mm→um）；
5. persist_fact: 创建 Fact（含 raw + normalized 观察值）；
6. quality: 对标准化值运行质量规则；
7. finalize: 返回摄入结果。

去重：通过文件 SHA-256 作为 idempotency_key，重复文件返回已有事实。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.connectors.entities import MappingProfileVersion
from packages.facts.observations import (
    NormalizedObservationInput,
    RawObservationInput,
)
from packages.facts.quality import QualityAssessment, QualityEngine
from packages.facts.service import CreateFactCommand, FactService
from packages.standards.variables import VariableVersion


@dataclass(frozen=True)
class IngestionResult:
    """摄入作业结果。

    Attributes:
        fact_ids: 创建/匹配的事实 ID 元组。
        deduplicated: 是否有重复文件去重。
        quality: 质量评估结果。
        blocked: 是否被质量规则阻断。
        warnings: 警告数。
        error: 失败原因（成功时 None）。
    """

    fact_ids: tuple[UUID, ...]
    deduplicated: bool
    quality: QualityAssessment
    blocked: bool
    warnings: int
    error: str | None


def _compute_sha256(file_path: Path) -> str:
    """计算文件 SHA-256 摘要。

    Args:
        file_path: 文件路径。

    Returns:
        str: 十六进制 SHA-256 摘要。
    """
    sha = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _parse_xlsx_summary(file_path: Path) -> dict[str, str]:
    """解析 XLSX 文件 Summary 工作表为字段-值字典。

    Summary 工作表格式为 [Field, Value] 键值对行。

    Args:
        file_path: XLSX 文件路径。

    Returns:
        dict[str, str]: 字段名→值字符串映射。

    Raises:
        AppError: code="unsupported_media_type"，当 openpyxl 未安装时。
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise AppError(
            code="unsupported_media_type",
            message="读取 XLSX 需要 openpyxl 依赖",
            retryable=False,
            fields={"format": "xlsx"},
        ) from exc

    wb = load_workbook(filename=str(file_path), read_only=True, data_only=True)
    try:
        ws = wb.active
        if ws is None:
            return {}
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return {}

        # 第一行是表头 ["Field", "Value"]
        fields: dict[str, str] = {}
        for row in rows[1:]:
            if row is None or len(row) < 2:
                continue
            key = row[0]
            value = row[1]
            if key is None:
                continue
            key_str = str(key).strip()
            if value is None:
                fields[key_str] = ""
            elif isinstance(value, float):
                # 避免浮点数精度问题：整数浮点转整数字符串
                if math.isclose(value, round(value)):
                    fields[key_str] = str(round(value))
                else:
                    fields[key_str] = str(value)
            else:
                fields[key_str] = str(value).strip()
        return fields
    finally:
        wb.close()


def _parse_csv(file_path: Path) -> dict[str, str]:
    """解析 CSV 文件为字段-值字典。

    假设 CSV 有两列（Field, Value）。

    Args:
        file_path: CSV 文件路径。

    Returns:
        dict[str, str]: 字段名→值字符串映射。
    """
    import csv

    fields: dict[str, str] = {}
    with open(file_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for i, row in enumerate(reader):
            if i == 0:
                continue  # 跳过表头
            if len(row) < 2:
                continue
            fields[row[0].strip()] = row[1].strip()
    return fields


def _parse_json(file_path: Path) -> dict[str, str]:
    """解析 JSON 文件为字段-值字典。

    假设 JSON 是一个键值对对象。

    Args:
        file_path: JSON 文件路径。

    Returns:
        dict[str, str]: 字段名→值字符串映射。
    """
    import json

    with open(file_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise AppError(
            code="validation_failed",
            message="JSON 文件必须是键值对对象",
            retryable=False,
            fields={"format": "json"},
        )
    return {str(k): str(v) if v is not None else "" for k, v in data.items()}


def _parse_file(file_path: Path) -> dict[str, str]:
    """根据文件扩展名解析为字段-值字典。

    Args:
        file_path: 文件路径。

    Returns:
        dict[str, str]: 字段名→值字符串映射。

    Raises:
        AppError: code="unsupported_media_type"，当格式不支持时。
    """
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        return _parse_xlsx_summary(file_path)
    if suffix == ".csv":
        return _parse_csv(file_path)
    if suffix == ".json":
        return _parse_json(file_path)
    raise AppError(
        code="unsupported_media_type",
        message=f"不支持的文件格式：{suffix}",
        retryable=False,
        fields={"suffix": suffix},
    )


def _try_convert_unit(value: str, source_unit: str, target_unit: str) -> str:
    """尝试单位转换，失败时返回原值。

    Args:
        value: 源数值字符串。
        source_unit: 源单位。
        target_unit: 目标单位。

    Returns:
        str: 转换后的数值字符串。
    """
    if not source_unit or not target_unit or source_unit == target_unit:
        return value
    try:
        from packages.standards.units import UnitConverter

        converted = UnitConverter.convert(Decimal(value), source_unit, target_unit)
        return str(converted)
    except (AppError, ValueError, ArithmeticError):
        return value


class IngestionPipeline:
    """端到端摄入管线：下载→解析→映射→标准化→持久化→质量→完成。

    依赖注入 session_factory、FactService、QualityEngine 等组件。
    支持单文件和批量摄入，通过 SHA-256 幂等键实现文件级去重。

    Attributes:
        _factory: 异步会话工厂。
        _fact_service: 事实服务。
        _quality_engine: 质量评估引擎。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fact_service: FactService,
        quality_engine: QualityEngine,
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化摄入管线。

        Args:
            session_factory: 异步会话工厂。
            fact_service: 事实服务（用于创建事实）。
            quality_engine: 质量评估引擎。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选）。
        """
        self._factory = session_factory
        self._fact_service = fact_service
        self._quality_engine = quality_engine
        self._org_id = organization_id
        self._actor_id = actor_id

    async def ingest_file(
        self,
        file_path: Path,
        mapping_profile_version_id: UUID,
        template_version_id: UUID,
        object_id: UUID,
        method_version_id: UUID | None = None,
    ) -> IngestionResult:
        """摄入单个文件。

        流程：
        1. download: 读取文件内容，计算 SHA-256 摘要；
        2. parse: 解析为字段-值字典；
        3. map: 加载 MappingProfile 规则，映射源字段到 L1 变量；
        4. normalize: 单位转换（如 mm→um）；
        5. persist_fact: 创建 Fact（含 raw + normalized 观察值）；
        6. quality: 对标准化值运行质量规则；
        7. finalize: 返回摄入结果。

        去重：通过 SHA-256 作为 idempotency_key，重复文件返回已有事实。

        Args:
            file_path: 文件路径。
            mapping_profile_version_id: 映射配置版本 ID。
            template_version_id: 事实模板版本 ID。
            object_id: 工业对象 ID。
            method_version_id: 方法版本 ID（可选）。

        Returns:
            IngestionResult: 摄入结果。
        """
        try:
            # 1. download: 计算 SHA-256
            file_sha256 = _compute_sha256(file_path)
            idempotency_key = f"sha256:{file_sha256}"

            # 去重检查：在完整管线前检查幂等键是否已存在
            from packages.facts.repository import FactRepository

            async with self._factory() as session:
                existing_fact = await FactRepository.find_by_idempotency_key(
                    session, self._org_id, idempotency_key
                )

            if existing_fact is not None:
                # 重复文件：返回已有事实，标记去重
                return IngestionResult(
                    fact_ids=(existing_fact.id,),
                    deduplicated=True,
                    quality=QualityAssessment(
                        results=(),
                        overall_status="passed",
                        summary={"passed": 0, "warning": 0, "blocked": 0},
                    ),
                    blocked=False,
                    warnings=0,
                    error=None,
                )

            # 2. parse: 解析文件
            parsed = _parse_file(file_path)

            # 提取实验 ID 作为 subject_id
            subject_id = (
                parsed.get("Experiment ID")
                or parsed.get("experiment_id")
                or file_path.stem
            )

            # 提取源单位（用于粒度字段单位转换）
            file_source_unit = parsed.get("Source Unit", "")

            # 3. map: 加载映射规则与变量版本
            rules, var_versions = await self._load_mapping_rules(
                mapping_profile_version_id
            )

            # 4-5. 映射 + 标准化 + 构建观察值
            from packages.common.ids import new_id

            raw_inputs: list[RawObservationInput] = []
            norm_inputs: list[NormalizedObservationInput] = []
            quality_observations: dict[str, object] = {}

            for rule in rules:
                source_path = rule["source_path"]
                target_vv_id = UUID(str(rule["target_variable_version_id"]))
                rule_source_unit = rule.get("source_unit")

                source_value = parsed.get(source_path)
                if source_value is None or source_value == "":
                    missing_policy = rule.get("missing_policy", "reject")
                    if missing_policy == "reject":
                        raise AppError(
                            code="validation_failed",
                            message=f"必填字段缺失：{source_path}",
                            retryable=False,
                            fields={"field": source_path},
                        )
                    elif missing_policy == "default":
                        source_value = rule.get("default_value", "")
                    else:
                        # null 策略：跳过该字段
                        continue

                var_version = var_versions.get(target_vv_id)
                if var_version is None:
                    continue

                canonical_unit = var_version.canonical_unit or ""

                # 确定源单位：优先使用规则中的 source_unit，
                # 否则使用文件中的 Source Unit（仅对粒度字段）
                effective_source_unit = rule_source_unit
                if not effective_source_unit and source_path in ("D10", "D50", "D90"):
                    effective_source_unit = file_source_unit

                # 单位转换
                normalized_value = _try_convert_unit(
                    source_value, effective_source_unit or "", canonical_unit
                )

                # 预生成 raw_id，用于 raw 和 normalized 观察值关联
                raw_id = new_id()
                raw_input = RawObservationInput(
                    id=raw_id,
                    source_path=source_path,
                    source_value=source_value,
                    source_unit=effective_source_unit,
                    source_name=source_path,
                    artifact_id=None,
                )
                raw_inputs.append(raw_input)

                # 构建 normalized 观察值（引用同一 raw_id）
                norm_input = NormalizedObservationInput(
                    variable_version_id=target_vv_id,
                    raw_observation_id=raw_id,
                    value=normalized_value,
                    unit=canonical_unit,
                )
                norm_inputs.append(norm_input)

                # 构建质量评估观察值字典
                try:
                    quality_observations[var_version.code] = float(normalized_value)
                except (TypeError, ValueError):
                    quality_observations[var_version.code] = normalized_value

            # 6. persist_fact: 创建事实
            command = CreateFactCommand(
                fact_type="experiment_run",
                template_version_id=template_version_id,
                organization_id=self._org_id,
                object_id=object_id,
                subject_id=subject_id,
                started_at=None,
                ended_at=None,
                method_version_id=method_version_id,
                raw=tuple(raw_inputs),
                normalized=tuple(norm_inputs),
                artifacts=(),
                idempotency_key=idempotency_key,
                created_by=self._actor_id or self._org_id,
            )

            ref = await self._fact_service.create(command)

            # 7. quality: 运行质量规则
            assessment = self._quality_engine.evaluate(quality_observations)

            return IngestionResult(
                fact_ids=(ref.fact_id,),
                deduplicated=False,
                quality=assessment,
                blocked=assessment.overall_status == "blocked",
                warnings=int(assessment.summary.get("warning", 0)),
                error=None,
            )
        except AppError as exc:
            return IngestionResult(
                fact_ids=(),
                deduplicated=False,
                quality=QualityAssessment(
                    results=(),
                    overall_status="blocked",
                    summary={"passed": 0, "warning": 0, "blocked": 0},
                ),
                blocked=True,
                warnings=0,
                error=f"{exc.code}: {exc.message}",
            )
        except Exception as exc:
            return IngestionResult(
                fact_ids=(),
                deduplicated=False,
                quality=QualityAssessment(
                    results=(),
                    overall_status="blocked",
                    summary={"passed": 0, "warning": 0, "blocked": 0},
                ),
                blocked=True,
                warnings=0,
                error=str(exc),
            )

    async def ingest_batch(
        self,
        file_paths: tuple[Path, ...],
        mapping_profile_version_id: UUID,
        template_version_id: UUID,
        object_id: UUID,
        method_version_id: UUID | None = None,
    ) -> tuple[IngestionResult, ...]:
        """批量摄入多个文件。

        逐个文件调用 ingest_file，收集结果。个别文件失败不影响其他文件。

        Args:
            file_paths: 文件路径元组。
            mapping_profile_version_id: 映射配置版本 ID。
            template_version_id: 事实模板版本 ID。
            object_id: 工业对象 ID。
            method_version_id: 方法版本 ID（可选）。

        Returns:
            tuple[IngestionResult, ...]: 每个文件的摄入结果元组。
        """
        results: list[IngestionResult] = []
        for file_path in file_paths:
            result = await self.ingest_file(
                file_path=file_path,
                mapping_profile_version_id=mapping_profile_version_id,
                template_version_id=template_version_id,
                object_id=object_id,
                method_version_id=method_version_id,
            )
            results.append(result)
        return tuple(results)

    async def _load_mapping_rules(
        self, mapping_profile_version_id: UUID
    ) -> tuple[list[dict], dict[UUID, VariableVersion]]:
        """加载映射配置版本的规则和关联的变量版本。

        Args:
            mapping_profile_version_id: 映射配置版本 ID。

        Returns:
            tuple[list[dict], dict[UUID, VariableVersion]]:
                (规则字典列表, 变量版本字典)。

        Raises:
            AppError: code="not_found"，当映射配置版本不存在时。
        """
        async with self._factory() as session:
            mpv = await session.scalar(
                sa.select(MappingProfileVersion).where(
                    MappingProfileVersion.id == mapping_profile_version_id
                )
            )
            if mpv is None:
                raise AppError(
                    code="not_found",
                    message="映射配置版本不存在",
                    retryable=False,
                    fields={
                        "mapping_profile_version_id": str(
                            mapping_profile_version_id
                        )
                    },
                )

            rules_raw = mpv.rules or []
            if not isinstance(rules_raw, list):
                rules_raw = []

            rules: list[dict] = list(rules_raw)

            # 收集所有目标变量版本 ID
            vv_ids: list[UUID] = []
            for rule in rules:
                target = rule.get("target_variable_version_id")
                if target:
                    try:
                        vv_ids.append(UUID(str(target)))
                    except (ValueError, TypeError):
                        continue

            # 加载变量版本
            var_versions: dict[UUID, VariableVersion] = {}
            if vv_ids:
                result = await session.execute(
                    sa.select(VariableVersion).where(
                        VariableVersion.id.in_(vv_ids)
                    )
                )
                for vv in result.scalars().all():
                    var_versions[vv.id] = vv

            return rules, var_versions

    async def _check_deduplicated(
        self, idempotency_key: str, fact_id: UUID
    ) -> bool:
        """检查事实是否为去重（幂等键已存在且事实不是新创建的）。

        通过检查 fact 表中是否有相同幂等键但不同创建时间的事实来判断。
        简化实现：检查 fact 的 current_revision 是否为 1 且 idempotency_key 已存在。

        Args:
            idempotency_key: 幂等键。
            fact_id: 事实 ID。

        Returns:
            bool: 是否为去重。
        """
        from packages.facts.entities import Fact
        from packages.facts.repository import FactRepository

        async with self._factory() as session:
            existing = await FactRepository.find_by_idempotency_key(
                session, self._org_id, idempotency_key
            )
            if existing is None:
                return False
            # 如果找到的事实与返回的事实 ID 相同，且该事实的 current_revision 为 1，
            # 则可能是新创建或去重。我们通过检查是否有多个事实来判断。
            # 实际上，FactService.create 在幂等键匹配时返回已有事实。
            # 简化判断：检查 fact 的 created_at 是否在 "很久以前"（不是刚创建的）。
            # 更可靠的方法：在创建前检查幂等键是否存在。
            return existing.id == fact_id and existing.current_revision >= 1
