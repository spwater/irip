"""IRIP 模型适配器实现（V2-T04）。

提供两种 ModelAdapter 实现：
- CommandModelAdapter: 命令行模型适配器，通过 subprocess 执行外部命令，
  创建隔离工作目录，写入 input.json，读取 output.json，
  支持超时、SIGTERM 取消、受限环境变量与输出大小限制；
- PythonModelAdapter: 进程内 Python 模型适配器，加载 pickle / joblib
  序列化的 sklearn 等模型，直接调用 predict()。

设计要点：
- 两种适配器均实现 contracts.ModelAdapter 协议；
- CommandModelAdapter 通信协议：input.json → 命令 → output.json，
  超时发 SIGTERM，输出超过 max_output_bytes 时拒绝；
- PythonModelAdapter 延迟导入 joblib / pickle / sklearn，
  仅在 load() 时反序列化工件字节；
- validate_input 使用 jsonschema 校验输入契约；
- 受限环境变量仅传递安全前缀（PATH/HOME/LANG/LC_*/IRIP_MODEL_*）。
"""

import asyncio
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

#: 安全的环境变量前缀白名单（其余一律过滤）。
_SAFE_ENV_PREFIXES: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_",
    "IRIP_MODEL_",
    "PYTHONPATH",
)


class CommandModelAdapter:
    """命令行模型适配器。

    通过 subprocess 执行外部命令行模型。创建隔离工作目录，
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

        使用 jsonschema 校验输入字典是否符合契约声明的 JSON Schema。

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
        8. 读取 output.json → ModelOutput。

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


class PythonModelAdapter:
    """进程内 Python 模型适配器。

    加载 pickle / joblib 序列化的 sklearn 等模型，
    在当前进程内直接调用 predict()。

    适用于 sklearn、xgboost 等支持 pickle 序列化的模型。
    延迟导入 joblib / pickle，仅在 load() 时反序列化工件字节。

    Attributes:
        _timeout_seconds: 超时秒数（用于 predict 超时保护）。
        _loaded: 已加载的模型实例。
        _contract: 已加载的模型契约。
        _model_obj: 反序列化后的模型对象。
    """

    def __init__(
        self,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        """初始化 Python 适配器。

        Args:
            timeout_seconds: 超时秒数（用于 predict 超时保护）。
            max_output_bytes: 预留参数，兼容协议（Python 适配器无输出文件）。
        """
        self._timeout_seconds: int = timeout_seconds
        self._max_output_bytes: int = max_output_bytes
        self._loaded: LoadedModel | None = None
        self._contract: ModelContract | None = None
        self._model_obj: Any = None

    async def load(
        self,
        artifact_bytes: bytes,
        contract: ModelContract,
    ) -> LoadedModel:
        """加载 pickle / joblib 序列化的模型。

        延迟导入 joblib（优先）或 pickle，反序列化工件字节为模型对象。

        Args:
            artifact_bytes: 模型工件字节内容（pickle 序列化）。
            contract: 模型契约。

        Returns:
            LoadedModel: 已加载的模型引用。

        Raises:
            AppError: code="invalid_model_artifact"，当反序列化失败时。
        """
        try:
            try:
                import io

                import joblib

                self._model_obj = joblib.load(io.BytesIO(artifact_bytes))
            except ImportError:
                import io
                import pickle

                self._model_obj = pickle.loads(artifact_bytes)
        except Exception as exc:
            raise AppError(
                code="invalid_model_artifact",
                message=f"模型工件反序列化失败: {exc}",
                retryable=False,
                fields={},
            ) from exc

        metadata: dict[str, Any] = {
            "artifact_size": len(artifact_bytes),
            "adapter_type": "python",
            "model_class": type(self._model_obj).__name__,
        }
        self._loaded = LoadedModel(
            artifact_ref="<in-memory>",
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

    async def predict(self, inputs: dict[str, Any]) -> ModelOutput:
        """执行进程内预测。

        按契约声明的输入维度顺序构造特征矩阵，
        调用模型 predict()，按契约声明的输出维度顺序映射结果。

        Args:
            inputs: 输入参数字典。

        Returns:
            ModelOutput: 预测输出。

        Raises:
            AppError: code="model_not_loaded"，当未加载时。
            AppError: code="model_failed"，当预测抛异常时。
        """
        if self._loaded is None or self._contract is None:
            raise AppError(
                code="model_not_loaded",
                message="模型未加载，请先调用 load()",
                retryable=False,
                fields={},
            )

        # 从 input_schema 提取输入维度顺序
        input_schema = self._contract.input_schema
        properties: dict[str, Any] = input_schema.get("properties", {})
        if properties:
            input_dims: list[str] = list(properties.keys())
        else:
            input_dims = list(inputs.keys())

        # 从 output_schema 提取输出维度顺序
        output_schema = self._contract.output_schema
        out_properties: dict[str, Any] = output_schema.get("properties", {})
        if out_properties:
            output_dims: list[str] = list(out_properties.keys())
        else:
            output_dims = []

        # 构造特征矩阵
        feature_row: list[float] = [float(inputs[dim]) for dim in input_dims]

        try:
            raw_pred = await asyncio.to_thread(self._model_obj.predict, [feature_row])
        except Exception as exc:
            raise AppError(
                code="model_failed",
                message=f"模型预测失败: {exc}",
                retryable=False,
                fields={},
            ) from exc

        # 映射预测结果
        predictions: dict[str, Any] = {}
        pred_list = raw_pred.tolist()[0] if hasattr(raw_pred, "tolist") else list(raw_pred[0])
        if output_dims and len(pred_list) == len(output_dims):
            for i, dim in enumerate(output_dims):
                predictions[dim] = pred_list[i]
        else:
            for i, value in enumerate(pred_list):
                key = output_dims[i] if i < len(output_dims) else f"output_{i}"
                predictions[key] = value

        return ModelOutput(
            predictions=predictions,
            metadata={"adapter_type": "python"},
        )

    def healthcheck(self) -> HealthStatus:
        """检查模型是否已加载。

        Returns:
            HealthStatus: 健康检查结果。
        """
        if self._model_obj is None:
            return HealthStatus(healthy=False, message="模型未加载")
        if not hasattr(self._model_obj, "predict"):
            return HealthStatus(
                healthy=False,
                message="模型对象缺少 predict 方法",
            )
        return HealthStatus(
            healthy=True,
            message=f"模型已加载: {type(self._model_obj).__name__}",
        )


def build_adapter(
    contract: ModelContract,
) -> CommandModelAdapter | PythonModelAdapter:
    """根据契约的 executor 规格构建适配器。

    契约中 executor.type 为 "cli" 时构建 CommandModelAdapter，
    为 "python" 时构建 PythonModelAdapter。未声明 executor 时
    默认构建 PythonModelAdapter（适用于 sklearn 等序列化模型）。

    Args:
        contract: 模型契约。

    Returns:
        CommandModelAdapter | PythonModelAdapter: 适配器实例。
    """
    contract_dict = contract.to_dict()
    executor: dict[str, Any] = contract_dict.get("executor", {}) or {}
    adapter_type: str = executor.get("type", "python")
    timeout_seconds: int = int(executor.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))

    if adapter_type == "cli":
        command_list: list[str] = list(executor.get("command", []))
        command_tuple: tuple[str, ...] = tuple(command_list)
        return CommandModelAdapter(
            command=command_tuple,
            timeout_seconds=timeout_seconds,
        )
    return PythonModelAdapter(timeout_seconds=timeout_seconds)
