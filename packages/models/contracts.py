"""IRIP 模型契约与适配器协议（V2-T04）。

定义模型生命周期的核心值对象与接口契约：
- ModelContract: 模型契约（名称、版本、输入/输出 Schema、适用域、摘要）；
- LoadedModel: 已加载的模型实例引用；
- ModelOutput: 模型预测输出；
- ValidationResult: 输入校验结果；
- HealthStatus: 模型健康检查结果；
- ModelAdapter: 模型适配器协议（load / validate_input / predict / healthcheck）。

设计要点：
- 所有值对象为 frozen dataclass，确保不可变性与可测试性；
- ModelAdapter 为 Protocol，具体实现（CommandModelAdapter / OnnxModelAdapter）
  在 adapters.py 中提供，支持声明式 ONNX 进程内执行与命令行子进程执行；
- ModelContract.sha256 为契约内容（name+version+schemas+domain）的
  SHA-256 摘要，用于内容寻址与完整性校验。
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


def compute_contract_sha256(
    name: str,
    version: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    applicability_domain: dict[str, Any],
) -> str:
    """计算模型契约的 SHA-256 内容摘要。

    将 name、version、input_schema、output_schema、applicability_domain
    序列化为规范 JSON（排序键）后计算 SHA-256。相同内容 → 相同摘要，
    用于内容寻址与完整性校验。

    Args:
        name: 模型名称。
        version: 语义化版本。
        input_schema: 输入 JSON Schema。
        output_schema: 输出 JSON Schema。
        applicability_domain: 适用域（各维度 min/max 范围）。

    Returns:
        str: 64 位小写十六进制 SHA-256 摘要。
    """
    payload: dict[str, Any] = {
        "name": name,
        "version": version,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "applicability_domain": applicability_domain,
    }
    canonical: str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelContract:
    """模型契约（不可变值对象）。

    描述模型的输入/输出契约与适用域，是模型版本的核心元数据。
    契约一旦发布即不可变，sha256 用于校验完整性。

    Attributes:
        name: 模型名称（小写字母/数字/下划线）。
        version: 语义化版本（如 ``1.0.0``）。
        input_schema: 输入参数的 JSON Schema。
        output_schema: 输出参数的 JSON Schema。
        applicability_domain: 适用域，各输入维度的 min/max 范围，
            格式 ``{dimension: {"min": float, "max": float, "unit": str}}``。
        sha256: 契约内容的 SHA-256 摘要（hex 小写）。
        artifact_sha256: 模型工件的 SHA-256 摘要（hex 小写），
            适配器加载工件时校验完整性，为空时跳过校验。
        artifact_signature: 模型工件的 Ed25519 签名（hex 小写，128 字符），
            为空表示未签名（遗留契约）。与 signing_public_key 配合，
            在安全强制模式下（enforce_security=True）缺签名即拒绝加载。
        signing_public_key: 签名者的 Ed25519 公钥（hex 小写，64 字符），
            用于验证 artifact_signature，为空表示未绑定签名者。
        publisher: 发布者标识（如 ``org:team`` 或 UUID），用于发布者白名单
            校验；为空表示未声明发布者（遗留契约）。
    """

    name: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    applicability_domain: dict[str, Any]
    sha256: str = ""
    executor: dict[str, Any] = field(default_factory=dict)
    artifact_sha256: str = ""
    artifact_signature: str = ""
    signing_public_key: str = ""
    publisher: str = ""

    def __post_init__(self) -> None:
        """若未提供 sha256，则自动计算契约摘要。"""
        if not self.sha256:
            digest: str = compute_contract_sha256(
                self.name,
                self.version,
                self.input_schema,
                self.output_schema,
                self.applicability_domain,
            )
            object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        """将契约序列化为 JSON 兼容字典（用于持久化与传输）。"""
        result: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "applicability_domain": self.applicability_domain,
            "sha256": self.sha256,
            "artifact_sha256": self.artifact_sha256,
            "artifact_signature": self.artifact_signature,
            "signing_public_key": self.signing_public_key,
            "publisher": self.publisher,
        }
        if self.executor:
            result["executor"] = self.executor
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelContract":
        """从 JSON 兼容字典反序列化契约。

        Args:
            data: 契约字典。

        Returns:
            ModelContract: 契约值对象。
        """
        return cls(
            name=data["name"],
            version=data["version"],
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            applicability_domain=data.get("applicability_domain", {}),
            sha256=data.get("sha256", ""),
            executor=data.get("executor", {}) or {},
            artifact_sha256=data.get("artifact_sha256", ""),
            artifact_signature=data.get("artifact_signature", ""),
            signing_public_key=data.get("signing_public_key", ""),
            publisher=data.get("publisher", ""),
        )


@dataclass(frozen=True)
class LoadedModel:
    """已加载的模型实例引用（不可变）。

    由 ModelAdapter.load() 返回，封装已就绪的模型实例引用与元数据。

    Attributes:
        artifact_ref: 工件引用标识（如临时文件路径或工件 UUID 字符串）。
        metadata: 加载元数据（如加载耗时、模型类型等）。
    """

    artifact_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelOutput:
    """模型预测输出（不可变）。

    由 ModelAdapter.predict() 返回，封装预测结果与元数据。

    Attributes:
        predictions: 预测结果字典（输出维度名 → 预测值）。
        metadata: 预测元数据（如耗时、模型版本等）。
    """

    predictions: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    """输入校验结果（不可变）。

    由 ModelAdapter.validate_input() 与 ApplicabilityChecker.check() 返回。

    Attributes:
        valid: 校验是否通过。
        errors: 错误信息元组（valid=False 时非空）。
    """

    valid: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class HealthStatus:
    """模型健康检查结果（不可变）。

    由 ModelAdapter.healthcheck() 返回。

    Attributes:
        healthy: 模型是否健康（可正常预测）。
        message: 健康状态描述。
    """

    healthy: bool
    message: str = ""


class ModelAdapter(Protocol):
    """模型适配器协议。

    每个具体适配器（CommandModelAdapter / OnnxModelAdapter）实现此协议，
    提供统一的模型加载、输入校验、预测与健康检查接口。

    约定：
    - load() 加载工件字节为可执行模型实例，返回 LoadedModel；
    - validate_input() 基于 contract.input_schema 校验输入，返回 ValidationResult；
    - predict() 执行预测，返回 ModelOutput；
    - healthcheck() 检查模型是否可正常工作，返回 HealthStatus；
    - 所有方法应可独立调用，不依赖全局状态。
    """

    async def load(
        self,
        artifact_bytes: bytes,
        contract: ModelContract,
    ) -> LoadedModel:
        """加载模型工件。

        Args:
            artifact_bytes: 模型工件字节内容（如 pickle / 二进制）。
            contract: 模型契约（含输入/输出 Schema 与适用域）。

        Returns:
            LoadedModel: 已加载的模型实例引用。
        """
        ...

    def validate_input(
        self,
        inputs: dict[str, Any],
        contract: ModelContract,
    ) -> ValidationResult:
        """校验输入是否符合契约的 input_schema。

        Args:
            inputs: 输入参数字典。
            contract: 模型契约。

        Returns:
            ValidationResult: 校验结果。
        """
        ...

    async def predict(self, inputs: dict[str, Any]) -> ModelOutput:
        """执行预测。

        Args:
            inputs: 输入参数字典（应已通过 validate_input 校验）。

        Returns:
            ModelOutput: 预测输出。
        """
        ...

    def healthcheck(self) -> HealthStatus:
        """检查模型健康状态。

        Returns:
            HealthStatus: 健康检查结果。
        """
        ...
