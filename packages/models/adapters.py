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
  在 enforce_security=True（生产入口 build_adapter 的默认）下还会校验
  Ed25519 签名、发布者白名单与算子（opset）白名单，三层缺一即拒绝（fail closed）；
- validate_input 使用 jsonschema 校验输入契约；
- 受限环境变量仅传递安全前缀（PATH/HOME/LANG/LC_*/IRIP_MODEL_*）。
- build_adapter 默认拒绝（fail closed），仅 executor.type == "onnx" 在进程内接受，
  其余类型（python/cli/缺失/未知）一律抛 unsafe_model_format，
  禁止主进程执行不可信模型代码；对 onnx 类型默认开启 enforce_security=True，
  即签名/发布者/算子三层校验 fail-closed。

安全分层说明（B4）：
- 第一层「摘要」SHA-256 已存在（_verify_sha256，空值跳过，向后兼容）；
- 第二层「签名」Ed25519（_verify_artifact_signature）；
- 第三层「发布者白名单」（_verify_publisher + _allowed_publishers）；
- 第四层「算子/opset 白名单」（_extract_op_types + _allowed_op_types）。
后三层统一由 enforce_security 开关控制：enforce_security=True 时缺签名/
发布者/白名单声明一律 fail-closed；False 时跳过（直接构造适配器的遗留路径）。
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

#: 默认允许的 ONNX 算子（保守最小集合）。仅含常见的数值/线性代数/激活/
#: 张量整形算子，覆盖线性回归、浅层神经网络等基础推理场景；不含任何
#: 具备文件/网络/子进程副作用或动态代码加载能力的算子。
_DEFAULT_ALLOWED_OP_TYPES: frozenset[str] = frozenset(
    {
        "Identity",
        "Add",
        "Sub",
        "Mul",
        "Div",
        "Abs",
        "Neg",
        "Pow",
        "Sqrt",
        "Sum",
        "MatMul",
        "Gemm",
        "Relu",
        "Sigmoid",
        "Tanh",
        "Softmax",
        "LeakyRelu",
        "Conv",
        "MaxPool",
        "AveragePool",
        "GlobalAveragePool",
        "GlobalMaxPool",
        "Reshape",
        "Flatten",
        "Transpose",
        "Concat",
        "Gather",
        "Cast",
        "Constant",
        "ConstantOfShape",
        "Shape",
        "Slice",
        "Squeeze",
        "Unsqueeze",
        "Dropout",
        "BatchNormalization",
        "InstanceNormalization",
        "LayerNormalization",
        "Exp",
        "Log",
        "Clip",
        "Min",
        "Max",
        "ReduceMean",
        "ReduceSum",
    }
)

#: 环境变量名：允许的模型发布者白名单（逗号分隔）。
_ENV_ALLOWED_PUBLISHERS: str = "IRIP_MODEL_ALLOWED_PUBLISHERS"


def _resolve_publishers() -> frozenset[str]:
    """从环境变量解析允许的发布者白名单。

    读取 ``IRIP_MODEL_ALLOWED_PUBLISHERS``（逗号分隔），构建允许发布者集合；
    为空时返回空集合（fail-closed：任何发布者都被拒绝）。

    Returns:
        frozenset[str]: 允许的发布者集合。
    """
    raw = os.getenv(_ENV_ALLOWED_PUBLISHERS, "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


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


def _verify_artifact_signature(
    artifact_bytes: bytes,
    contract: ModelContract,
    *,
    enforce: bool,
) -> None:
    """用 Ed25519 验证工件字节的签名（第二层安全校验）。

    语义（fail-closed 边界）：
    - ``enforce=True``（生产）时，缺签名或缺公钥即拒绝加载；
      签名与公钥同时存在时用 Ed25519 严格验签，不匹配即拒绝。
    - ``enforce=False``（直接构造适配器的遗留/可信部署路径）时，
      跳过验签以保持向后兼容。

    Args:
        artifact_bytes: 模型工件字节内容。
        contract: 模型契约（含 artifact_signature / signing_public_key）。
        enforce: 是否强制（缺即拒绝，fail-closed）。

    Raises:
        AppError: code="invalid_model_artifact"，当 enforce=True 且缺签名/
            公钥、或验签失败时。
    """
    signature_hex: str = (contract.artifact_signature or "").strip()
    public_key_hex: str = (contract.signing_public_key or "").strip()

    if not enforce:
        # 遗留路径：即便声明了签名也不强制（保持与旧调用方一致）。
        return

    if not signature_hex or not public_key_hex:
        raise AppError(
            code="invalid_model_artifact",
            message="模型工件缺少签名或签名公钥，拒绝加载",
            retryable=False,
            fields={},
        )

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        signature = bytes.fromhex(signature_hex)
        public_key.verify(signature, artifact_bytes)
    except InvalidSignature:
        raise AppError(
            code="invalid_model_artifact",
            message="模型工件签名校验失败，内容可能被篡改",
            retryable=False,
            fields={},
        ) from None
    except (ValueError, TypeError):
        # 非法 hex / 非法公钥长度等情况，一律 fail-closed。
        raise AppError(
            code="invalid_model_artifact",
            message="模型工件签名或公钥格式非法，拒绝加载",
            retryable=False,
            fields={},
        ) from None


def _extract_op_types(artifact_bytes: bytes) -> set[str]:
    """解析 ONNX 计算图，收集主图与所有子图（函数体）的算子类型。

    Args:
        artifact_bytes: ONNX 模型字节内容。

    Returns:
        set[str]: 图中出现的全部算子类型集合。

    Raises:
        AppError: code="invalid_model_artifact"，当无法解析 ONNX 图时
            （消息不含底层异常文本）。
    """
    try:
        import onnx

        model = onnx.load_model_from_string(artifact_bytes)
    except Exception as exc:
        raise AppError(
            code="invalid_model_artifact",
            message="无法解析 ONNX 计算图，拒绝加载",
            retryable=False,
            fields={},
        ) from exc

    op_types: set[str] = set()
    graph_queue: list[Any] = [model.graph]
    while graph_queue:
        graph = graph_queue.pop()
        for node in graph.node:
            op_types.add(node.op_type)
            # 递归进入控制流算子（If/Loop/Scan）的子图。
            for attr in node.attribute:
                if attr.HasField("g"):
                    graph_queue.append(attr.g)
                for subgraph in attr.graphs:
                    graph_queue.append(subgraph)
    return op_types


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
    - enforce_security=True 时额外校验 Ed25519 签名、发布者白名单与
      算子白名单（三层缺一即拒绝，fail-closed）；
    - 工件大小超过 max_artifact_bytes 即拒绝（防止资源耗尽）；
    - 固定使用 CPUExecutionProvider，单线程执行（intra/inter op num threads=1）；
    - 异常消息不泄露解析器内部信息（不包含底层异常文本）。

    Attributes:
        _timeout_seconds: 推理超时秒数（用于 predict 超时保护）。
        _max_artifact_bytes: 工件最大字节数。
        _enforce_security: 是否强制签名/发布者/算子三层校验（fail-closed）。
        _allowed_publishers: 允许的发布者集合（空集合时拒绝一切发布者）。
        _allowed_op_types: 允许的算子集合。
        _session: onnxruntime 推理会话（加载后非空）。
        _contract: 已加载的模型契约。
        _loaded: 已加载的模型引用。
    """

    def __init__(
        self,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_artifact_bytes: int = _MAX_ONNX_ARTIFACT_BYTES,
        *,
        enforce_security: bool = False,
        allowed_publishers: frozenset[str] | None = None,
        allowed_op_types: frozenset[str] | None = None,
    ) -> None:
        """初始化 ONNX 适配器。

        Args:
            timeout_seconds: 推理超时秒数（默认 300）。
            max_artifact_bytes: 工件最大字节数（默认 256 MB）。
            enforce_security: 是否强制签名/发布者/算子三层校验（默认 False，
                表示直接构造时的遗留/可信部署路径；生产入口 build_adapter
                默认传 True，fail-closed）。
            allowed_publishers: 允许的发布者集合。None 时从环境变量
                ``IRIP_MODEL_ALLOWED_PUBLISHERS`` 解析，缺省为空集合
                （fail-closed：拒绝一切发布者）。
            allowed_op_types: 允许的算子集合。None 时采用保守默认集合。
        """
        self._timeout_seconds: int = timeout_seconds
        self._max_artifact_bytes: int = max_artifact_bytes
        self._enforce_security: bool = enforce_security
        self._allowed_publishers: frozenset[str] = (
            allowed_publishers if allowed_publishers is not None else _resolve_publishers()
        )
        self._allowed_op_types: frozenset[str] = (
            allowed_op_types if allowed_op_types is not None else _DEFAULT_ALLOWED_OP_TYPES
        )
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
        2. （enforce_security）校验 Ed25519 签名（缺签名即拒绝，fail-closed）；
        3. （enforce_security）校验发布者在白名单内；
        4. （enforce_security）校验图中全部算子属于算子白名单；
        5. 校验工件大小不超限；
        6. 延迟导入 onnxruntime 并构建单线程 CPU 推理会话。

        Args:
            artifact_bytes: ONNX 模型工件字节内容。
            contract: 模型契约（含 artifact_sha256 / artifact_signature /
                signing_public_key / publisher）。

        Returns:
            LoadedModel: 已加载的模型引用（artifact_ref 为 "<in-memory>"）。

        Raises:
            AppError: code="invalid_model_artifact"，当哈希/签名/发布者/算子
                校验失败、工件过大或不是有效 ONNX 格式时。
            AppError: code="model_failed"，当 onnxruntime 未安装时。
        """
        _verify_sha256(artifact_bytes, contract.artifact_sha256)

        if self._enforce_security:
            self._verify_signature(artifact_bytes, contract)
            self._verify_publisher(contract)
            self._verify_op_types(artifact_bytes)

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

    def _verify_signature(
        self,
        artifact_bytes: bytes,
        contract: ModelContract,
    ) -> None:
        """校验工件 Ed25519 签名（fail-closed）。"""
        _verify_artifact_signature(artifact_bytes, contract, enforce=True)

    def _verify_publisher(self, contract: ModelContract) -> None:
        """校验契约声明的发布者在允许白名单内（fail-closed）。

        发布者缺失或不在白名单（含白名单为空）时拒绝加载。

        Args:
            contract: 模型契约（含 publisher）。

        Raises:
            AppError: code="invalid_model_artifact"，当发布者缺失或未授权时。
        """
        publisher: str = (contract.publisher or "").strip()
        if not publisher:
            raise AppError(
                code="invalid_model_artifact",
                message="模型契约未声明发布者，拒绝加载",
                retryable=False,
                fields={},
            )
        if publisher not in self._allowed_publishers:
            raise AppError(
                code="invalid_model_artifact",
                message="模型发布者不在允许白名单内，拒绝加载",
                retryable=False,
                fields={"publisher": publisher},
            )

    def _verify_op_types(self, artifact_bytes: bytes) -> None:
        """校验图中全部算子属于算子白名单（fail-closed）。

        任一算子不在白名单内即拒绝加载。

        Args:
            artifact_bytes: ONNX 模型字节内容。

        Raises:
            AppError: code="invalid_model_artifact"，当存在未授权算子时。
        """
        op_types: set[str] = _extract_op_types(artifact_bytes)
        disallowed: set[str] = op_types - self._allowed_op_types
        if disallowed:
            raise AppError(
                code="invalid_model_artifact",
                message="模型包含未授权的算子，拒绝加载",
                retryable=False,
                fields={"disallowed_op_types": sorted(disallowed)},
            )

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
        output_names_contract: list[str] = list(out_props.keys()) if out_props else []

        predictions: dict[str, Any] = {}
        if len(results) == 1 and len(output_names_contract) > 1:
            flat = np.asarray(results[0]).reshape(-1).tolist()
            for i, name in enumerate(output_names_contract):
                predictions[name] = flat[i] if i < len(flat) else 0.0
        else:
            for i, res in enumerate(results):
                name = output_names_contract[i] if i < len(output_names_contract) else f"output_{i}"
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

    onnx 类型默认开启 enforce_security=True（B4）：加载前强制校验
    Ed25519 签名、发布者白名单与算子白名单，三者缺一即拒绝。
    可通过 executor 中的 ``enforce_security: false`` 显式关闭（仅限
    可信内部部署），``allowed_publishers``（数组）覆盖发布者白名单，
    ``allowed_op_types``（数组）覆盖算子白名单。

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
        timeout_seconds: int = int(executor.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))
        max_artifact_bytes: int = int(executor.get("max_artifact_bytes", _MAX_ONNX_ARTIFACT_BYTES))
        enforce_security: bool = bool(executor.get("enforce_security", True))
        raw_publishers = executor.get("allowed_publishers")
        allowed_publishers: frozenset[str] | None = (
            frozenset(str(p) for p in raw_publishers)
            if isinstance(raw_publishers, list)
            else None
        )
        raw_op_types = executor.get("allowed_op_types")
        allowed_op_types: frozenset[str] | None = (
            frozenset(str(op) for op in raw_op_types)
            if isinstance(raw_op_types, list)
            else None
        )
        return OnnxModelAdapter(
            timeout_seconds=timeout_seconds,
            max_artifact_bytes=max_artifact_bytes,
            enforce_security=enforce_security,
            allowed_publishers=allowed_publishers,
            allowed_op_types=allowed_op_types,
        )

    raise AppError(
        code="unsafe_model_format",
        message="不支持的模型执行格式，仅允许声明式 ONNX 工件在进程内执行",
        retryable=False,
        fields={"executor_type": adapter_type} if adapter_type else {},
    )
