"""IRIP 模型适配器实现（V2-T04）。

提供两种 ModelAdapter 实现：
- CommandModelAdapter: 命令行模型适配器，通过子进程执行外部命令，
  创建隔离工作目录，写入 input.json，读取 output.json，
  支持超时、SIGTERM 取消、受限环境变量与输出大小限制；
- OnnxModelAdapter: 声明式 ONNX 模型适配器，加载 ONNX 计算图工件，
  在进程内通过 onnxruntime 执行推理。ONNX 为声明式格式，
  反序列化不触发任意代码执行，是唯一允许在主进程内执行的模型格式。

设计要点：
- 两种适配器均实现 contracts.ModelAdapter 协议；
- CommandModelAdapter 通信协议：input.json -> 命令 -> output.json，
  超时发 SIGTERM，输出超过 max_output_bytes 时拒绝；
- OnnxModelAdapter 加载前校验工件 SHA-256 完整性，
  固定 CPUExecutionProvider 单线程执行，异常消息不泄露解析器内部信息；
- validate_input 使用 jsonschema 校验输入契约；
- 受限环境变量仅传递安全前缀（PATH/HOME/LANG/LC_*/IRIP_MODEL_*）。
- build_adapter 默认拒绝（fail closed），仅 executor.type == "onnx" 在进程内接受，
  其余类型（python/cli/缺失/未知）一律抛 unsafe_model_format，
  禁止主进程执行不可信模型代码。
"""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import jsonschema

from packages.common.errors import AppError
from packages.models.contracts import (
    HealthStatus,
    LoadedModel,
    ModelContract,
    ModelOutput,
    ValidationResult,
)

#: 默认超时秒数。
_DEFAULT_TIMEOUT_SECONDS: int = 300

#: 默认最大输出字节数（10 MB）。
_DEFAULT_MAX_OUTPUT_BYTES: int = 10 * 1024 * 1024

#: ONNX 工件最大字节数（256 MB），超过即拒绝加载（防止资源耗尽）。
_MAX_ONNX_ARTIFACT_BYTES: int = 256 * 1024 * 1024

#: 安全的环境变量前缀白名单（其余一律过滤）。
_SAFE_ENV_PREFIXES: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_",
    "IRIP_MODEL_",
    "PYTHONPATH",
)


def _verify_sha256(artifact_bytes: bytes, expected_sha256: str) -> None:
    """校验工件字节与期望 SHA-256 摘要一致。

    期望摘要为空时跳过校验（向后兼容）；非空时严格比对，
    不匹配则抛 invalid_model_artifact（消息不含内部细节）。

    Args:
        artifact_bytes: 模型工件字节内容。
        expected_sha256: 期望的 SHA-256 摘要（hex 小写），空字符串跳过。

    Raises:
        AppError: code="invalid_model_artifact"，当摘要不匹配时。
    """
    if not expected_sha256:
        return
    actual_sha: str = hashlib.sha256(artifact_bytes).hexdigest()
    if actual_sha != expected_sha256:
        raise AppError(
            code="invalid_model_artifact",
            message="工件 SHA-256 校验失败，内容可能被篡改",
            retryable=False,
            fields={},
        )


def _validate_input_schema(
    inputs: dict[str, Any],
    contract: ModelContract,
) -> ValidationResult:
    """基于契约的 input_schema 校验输入。

    使用 jsonschema 校验输入字典是否符合契约声明的 JSON Schema。
    空 schema 视为始终通过。

    Args:
        inputs: 输入参数字典。
        contract: 模型契约。

    Returns:
        ValidationResult: 校验结果。校验失败返回 valid=False，
        errors 含校验错误信息。
    """
    schema: dict[str, Any] = contract.input_schema
    if not schema:
        return ValidationResult(valid=True, errors=())
    try:
        jsonschema.validate(instance=inputs, schema=schema)
    except jsonschema.ValidationError as exc:
        return ValidationResult(
            valid=False,
            errors=(f"input_validation_failed: {exc.message}",),
        )
    return ValidationResult(valid=True, errors=())


class CommandModelAdapter:
    """命令行模型适配器。

    通过子进程执行外部命令行模型。创建隔离工作目录，
    将模型工件写入工作目录，写入 input.json，执行命令，
    读取 output.json。

    通信协议：
    - input.json: ``{"inputs": {...}}``；
    - output.json: ``{"predictions": {...}, "metadata": {...}}``；
    - 命令格式: ``<command...> <workdir> <input_path> <output_path>``，
      其中 workdir 为隔离工作目录（模型工件已写入其中）。

    安全措施：
    - 超时用 asyncio.wait_for，到期发 SIGTERM；
    - 输出超过 max_output_bytes 时拒绝（防止内存耗尽）；
    - 环境变量白名单过滤。

    注意：本适配器可通过直接构造使用，但 build_adapter 不会自动构建它
    （cli 类型在 build_adapter 中被拒绝）。如需使用，应由可信部署流程
    显式构造，而非由用户契约驱动。

    Attributes:
        _command: 命令元组（如 ``("python", "predict.py")``）。
        _timeout_seconds: 超时秒数。
        _max_output_bytes: 输出最大字节数。
        _loaded: 已加载的模型引用（工作目录路径）。
        _contract: 已加载的模型契约。
    """

    def __init__(
        self,
        command: tuple[str, ...],
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        """初始化命令行适配器。

        Args:
            command: 命令元组（如 ``("python", "predict.py")``）。
            timeout_seconds: 超时秒数（默认 300）。
            max_output_bytes: 输出最大字节数（默认 10 MB）。
        """
        self._command: tuple[str, ...] = command
        self._timeout_seconds: int = timeout_seconds
        self._max_output_bytes: int = max_output_bytes
        self._loaded: LoadedModel | None = None
        self._contract: ModelContract | None = None

    async def load(
        self,
        artifact_bytes: bytes,
        contract: ModelContract,
    ) -> LoadedModel:
        """加载模型工件到隔离工作目录。

        创建临时工作目录，将工件字节写入 ``model.artifact`` 文件，
        保存契约供后续校验与预测使用。

        Args:
            artifact_bytes: 模型工件字节内容。
            contract: 模型契约。

        Returns:
            LoadedModel: 已加载的模型引用（artifact_ref 为工作目录路径）。
        """
        workdir = Path(tempfile.mkdtemp(prefix="irip-model-"))
        artifact_path = workdir / "model.artifact"
        artifact_path.write_bytes(artifact_bytes)

        metadata: dict[str, Any] = {
            "workdir": str(workdir),
            "artifact_path": str(artifact_path),
            "artifact_size": len(artifact_bytes),
            "adapter_type": "cli",
        }
        self._loaded = LoadedModel(
            artifact_ref=str(artifact_path),
            metadata=metadata,
        )
        self._contract = contract
        return self._loaded

    def validate_input(
        self,
        inputs: dict[str, Any],
        contract: ModelContract,
    ) -> ValidationResult:
        """基于契约的 input_schema 校验输入。

        Args:
            inputs: 输入参数字典。
            contract: 模型契约。

        Returns:
            ValidationResult: 校验结果。
        """
        return _validate_input_schema(inputs, contract)

    async def predict(self, inputs: dict[str, Any]) -> ModelOutput:
        """执行命令行预测。

        流程：
        1. 校验已加载模型与契约；
        2. 创建临时输入/输出文件路径；
        3. 写入 input.json；
        4. 构建受限环境变量；
        5. 执行命令（command + workdir + input_path + output_path）；
        6. asyncio.wait_for 等待完成（超时发 SIGTERM）；
        7. 检查输出大小限制；
        8. 读取 output.json -> ModelOutput。

        Args:
            inputs: 输入参数字典。

        Returns:
            ModelOutput: 预测输出。

        Raises:
            AppError: code="model_not_loaded"，当未加载时。
            AppError: code="model_timeout"，当执行超时。
            AppError: code="model_failed"，当子进程非零退出。
            AppError: code="invalid_output"，当输出过大或解析失败。
        """
        if self._loaded is None or self._contract is None:
            raise AppError(
                code="model_not_loaded",
                message="模型未加载，请先调用 load()",
                retryable=False,
                fields={},
            )

        workdir = Path(self._loaded.metadata["workdir"])
        input_path = workdir / "input.json"
        output_path = workdir / "output.json"

        input_payload: dict[str, Any] = {"inputs": inputs}
        input_path.write_text(
            json.dumps(input_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        safe_env = self._build_safe_env()
        full_command: list[str] = list(self._command) + [
            str(workdir),
            str(input_path),
            str(output_path),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *full_command,
                cwd=str(workdir),
                env=safe_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AppError(
                code="model_failed",
                message=f"模型命令不存在: {self._command[0]}",
                retryable=False,
                fields={"command": self._command[0]},
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            self._terminate_process(process)
            raise AppError(
                code="model_timeout",
                message=(f"模型执行超时（{self._timeout_seconds}s）"),
                retryable=False,
                fields={"timeout_seconds": self._timeout_seconds},
            ) from None

        if process.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            raise AppError(
                code="model_failed",
                message=(f"模型执行失败 (exit={process.returncode}): {stderr_text}"),
                retryable=False,
                fields={"exit_code": process.returncode},
            )

        if not output_path.exists():
            raise AppError(
                code="invalid_output",
                message="模型未生成 output.json",
                retryable=False,
                fields={},
            )

        output_bytes = output_path.read_bytes()
        if len(output_bytes) > self._max_output_bytes:
            raise AppError(
                code="invalid_output",
                message=(f"模型输出过大（{len(output_bytes)} > {self._max_output_bytes} bytes）"),
                retryable=False,
                fields={
                    "output_size": len(output_bytes),
                    "max_output_bytes": self._max_output_bytes,
                },
            )

        try:
            output_data = json.loads(output_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(
                code="invalid_output",
                message=f"output.json 解析失败: {exc}",
                retryable=False,
                fields={},
            ) from exc

        predictions: dict[str, Any] = output_data.get("predictions", {})
        metadata: dict[str, Any] = output_data.get("metadata", {})
        return ModelOutput(
            predictions=predictions,
            metadata=metadata,
        )

    def healthcheck(self) -> HealthStatus:
        """检查模型命令是否可执行。

        验证命令元组非空且首个可执行文件存在（在 PATH 中可找到）。

        Returns:
            HealthStatus: 健康检查结果。
        """
        if not self._command:
            return HealthStatus(healthy=False, message="命令为空")
        executable = self._command[0]
        if os.path.isabs(executable):
            if not Path(executable).exists():
                return HealthStatus(
                    healthy=False,
                    message=f"可执行文件不存在: {executable}",
                )
        else:
            from shutil import which

            if which(executable) is None:
                return HealthStatus(
                    healthy=False,
                    message=f"命令不在 PATH 中: {executable}",
                )
        return HealthStatus(
            healthy=True,
            message=f"命令可执行: {executable}",
        )

    def _build_safe_env(self) -> dict[str, str]:
        """构建受限环境变量（过滤不安全变量）。

        仅保留以安全前缀开头的环境变量。

        Returns:
            dict[str, str]: 安全的环境变量字典。
        """
        safe_env: dict[str, str] = {}
        for key, value in os.environ.items():
            if any(key.startswith(prefix) for prefix in _SAFE_ENV_PREFIXES):
                safe_env[key] = value
        return safe_env

    def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        """向子进程发送 SIGTERM（优雅终止）。

        Args:
            process: 待终止的子进程。
        """
        try:
            process.terminate()
        except ProcessLookupError:
            pass


class OnnxModelAdapter:
    """声明式 ONNX 模型适配器（进程内安全执行）。

    加载 ONNX 序列化模型工件，在进程内通过 onnxruntime 执行推理。
    ONNX 是声明式计算图格式，不包含任意可执行代码，
    因此反序列化不会触发 RCE，是唯一允许在主进程内执行的模型格式。

    安全措施：
    - 加载前校验工件 SHA-256 完整性（篡改即拒绝）；
    - 工件大小超过 max_artifact_bytes 即拒绝（防止资源耗尽）；
    - 固定使用 CPUExecutionProvider，单线程执行（intra/inter op num threads=1）；
    - 异常消息不泄露解析器内部信息（不包含底层异常文本）。

    Attributes:
        _timeout_seconds: 推理超时秒数（用于 predict 超时保护）。
        _max_artifact_bytes: 工件最大字节数。
        _session: onnxruntime 推理会话（加载后非空）。
        _contract: 已加载的模型契约。
        _loaded: 已加载的模型引用。
    """

    def __init__(
        self,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_artifact_bytes: int = _MAX_ONNX_ARTIFACT_BYTES,
    ) -> None:
        """初始化 ONNX 适配器。

        Args:
            timeout_seconds: 推理超时秒数（默认 300）。
            max_artifact_bytes: 工件最大字节数（默认 256 MB）。
        """
        self._timeout_seconds: int = timeout_seconds
        self._max_artifact_bytes: int = max_artifact_bytes
        self._session: Any = None
        self._contract: ModelContract | None = None
        self._loaded: LoadedModel | None = None

    async def load(
        self,
        artifact_bytes: bytes,
        contract: ModelContract,
    ) -> LoadedModel:
        """加载 ONNX 模型工件为推理会话。

        流程：
        1. 校验工件 SHA-256 完整性（契约声明 artifact_sha256 时）；
        2. 校验工件大小不超限；
        3. 延迟导入 onnxruntime 并构建单线程 CPU 推理会话。

        Args:
            artifact_bytes: ONNX 模型工件字节内容。
            contract: 模型契约（含 artifact_sha256 用于完整性校验）。

        Returns:
            LoadedModel: 已加载的模型引用（artifact_ref 为 "<in-memory>"）。

        Raises:
            AppError: code="invalid_model_artifact"，当哈希校验失败、
                工件过大或不是有效 ONNX 格式时。
            AppError: code="model_failed"，当 onnxruntime 未安装时。
        """
        _verify_sha256(artifact_bytes, contract.artifact_sha256)

        if len(artifact_bytes) > self._max_artifact_bytes:
            raise AppError(
                code="invalid_model_artifact",
                message="模型工件过大，超出允许上限",
                retryable=False,
                fields={
                    "size": len(artifact_bytes),
                    "max": self._max_artifact_bytes,
                },
            )

        try:
            import onnxruntime
        except ImportError as exc:
            raise AppError(
                code="model_failed",
                message="onnxruntime 未安装，无法加载 ONNX 模型",
                retryable=False,
                fields={},
            ) from exc

        try:
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            self._session = onnxruntime.InferenceSession(
                artifact_bytes,
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise AppError(
                code="invalid_model_artifact",
                message="模型工件不是有效的 ONNX 格式",
                retryable=False,
                fields={},
            ) from exc

        self._contract = contract
        self._loaded = LoadedModel(
            artifact_ref="<in-memory>",
            metadata={
                "adapter_type": "onnx",
                "artifact_size": len(artifact_bytes),
            },
        )
        return self._loaded

    def validate_input(
        self,
        inputs: dict[str, Any],
        contract: ModelContract,
    ) -> ValidationResult:
        """基于契约的 input_schema 校验输入。

        Args:
            inputs: 输入参数字典。
            contract: 模型契约。

        Returns:
            ValidationResult: 校验结果。
        """
        return _validate_input_schema(inputs, contract)

    async def predict(self, inputs: dict[str, Any]) -> ModelOutput:
        """执行 ONNX 进程内推理。

        按契约声明的输入维度顺序构造喂入张量，调用 onnxruntime 推理，
        按契约声明的输出维度顺序映射结果。

        输入映射策略：
        - 若 ONNX 模型仅 1 个输入且契约有多个输入维度，
          将各维度标量堆叠为单行二维张量 ``[[v0, v1, ...]]``；
        - 否则按位置一一映射，每个输入构造为 ``[[v]]``。

        输出映射策略：
        - 若 ONNX 模型仅 1 个输出且契约有多个输出维度，
          将输出展平后按顺序映射到各维度名；
        - 否则按位置一一映射，取每个输出张量的首个元素。

        Args:
            inputs: 输入参数字典（应已通过 validate_input 校验）。

        Returns:
            ModelOutput: 预测输出。

        Raises:
            AppError: code="model_not_loaded"，当未加载时。
            AppError: code="model_timeout"，当推理超时时。
            AppError: code="model_failed"，当输入构造或推理失败时
                （消息不包含底层异常文本）。
        """
        if self._session is None or self._contract is None:
            raise AppError(
                code="model_not_loaded",
                message="模型未加载，请先调用 load()",
                retryable=False,
                fields={},
            )

        import numpy as np

        contract = self._contract
        input_metas = self._session.get_inputs()
        in_props: dict[str, Any] = contract.input_schema.get("properties", {})
        input_names: list[str] = list(in_props.keys()) if in_props else list(inputs.keys())

        feed: dict[str, Any] = {}
        try:
            if len(input_metas) == 1 and len(input_names) > 1:
                row: list[float] = [float(inputs[name]) for name in input_names]
                feed[input_metas[0].name] = np.asarray([row], dtype=np.float32)
            else:
                for idx, meta in enumerate(input_metas):
                    cname = input_names[idx] if idx < len(input_names) else meta.name
                    feed[meta.name] = np.asarray(
                        [[float(inputs[cname])]],
                        dtype=np.float32,
                    )
            output_names: list[str] = [o.name for o in self._session.get_outputs()]
            results = await asyncio.wait_for(
                asyncio.to_thread(self._session.run, output_names, feed),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise AppError(
                code="model_timeout",
                message=f"ONNX 推理超时（{self._timeout_seconds}s）",
                retryable=False,
                fields={"timeout_seconds": self._timeout_seconds},
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                code="model_failed",
                message="ONNX 推理输入构造失败",
                retryable=False,
                fields={},
            ) from exc
        except Exception as exc:
            raise AppError(
                code="model_failed",
                message="ONNX 推理执行失败",
                retryable=False,
                fields={},
            ) from exc

        out_props: dict[str, Any] = contract.output_schema.get("properties", {})
        output_names_contract: list[str] = (
            list(out_props.keys()) if out_props else []
        )

        predictions: dict[str, Any] = {}
        if len(results) == 1 and len(output_names_contract) > 1:
            flat = np.asarray(results[0]).reshape(-1).tolist()
            for i, name in enumerate(output_names_contract):
                predictions[name] = flat[i] if i < len(flat) else 0.0
        else:
            for i, res in enumerate(results):
                name = (
                    output_names_contract[i]
                    if i < len(output_names_contract)
                    else f"output_{i}"
                )
                flat = np.asarray(res).reshape(-1).tolist()
                predictions[name] = flat[0] if flat else 0.0

        return ModelOutput(
            predictions=predictions,
            metadata={"adapter_type": "onnx"},
        )

    def healthcheck(self) -> HealthStatus:
        """检查 ONNX 模型是否已加载。

        Returns:
            HealthStatus: 健康检查结果。
        """
        if self._session is None:
            return HealthStatus(healthy=False, message="模型未加载")
        return HealthStatus(healthy=True, message="ONNX 模型已加载")


def build_adapter(contract: ModelContract) -> OnnxModelAdapter:
    """根据契约的 executor 规格构建适配器（fail closed）。

    安全策略：仅 executor.type == "onnx" 在进程内执行（声明式计算图，
    无 RCE 风险）。其余类型（python / cli / 缺失 / 未知）一律抛
    unsafe_model_format，禁止主进程执行不可信模型代码。

    Args:
        contract: 模型契约。

    Returns:
        OnnxModelAdapter: ONNX 适配器实例。

    Raises:
        AppError: code="unsafe_model_format"，当 executor 类型非 onnx 时。
    """
    contract_dict = contract.to_dict()
    executor: dict[str, Any] = contract_dict.get("executor", {}) or {}
    adapter_type: str = executor.get("type", "")

    if adapter_type == "onnx":
        timeout_seconds: int = int(
            executor.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        )
        max_artifact_bytes: int = int(
            executor.get("max_artifact_bytes", _MAX_ONNX_ARTIFACT_BYTES)
        )
        return OnnxModelAdapter(
            timeout_seconds=timeout_seconds,
            max_artifact_bytes=max_artifact_bytes,
        )

    raise AppError(
        code="unsafe_model_format",
        message="不支持的模型执行格式，仅允许声明式 ONNX 工件在进程内执行",
        retryable=False,
        fields={"executor_type": adapter_type} if adapter_type else {},
    )
