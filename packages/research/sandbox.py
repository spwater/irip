"""沙箱运行时接口 + Docker 实现 + 保温池管理器。

SandboxRuntime Protocol 定义沙箱运行时抽象接口：
- create_container: 创建隔离容器（断网、非 root、只读、资源限制）
- execute: 在容器中执行 Python 脚本
- cancel: 取消执行
- collect_output: 收集白名单输出文件
- destroy_container: 销毁容器
- keep_warm: 保温容器（不计入槽位，独立上限 5 个）

DockerSandboxRuntime 是开发环境实现（使用 aiodocker）。
生产环境可替换为 K8sPodRuntime（接口兼容）。

安全清单：
- 断网: network_mode='none'
- 非 root: user='nonroot:nonroot'
- 只读基础镜像: read_only=True
- 只读输入挂载: volumes={input: {'bind': '/input', 'mode': 'ro'}}
- 临时工作目录: tmpfs={'/workspace': 'size=Ng'}
- 无核心凭据: 不注入环境变量
- CPU/内存/时间/PID/capability 限制
"""

import asyncio
import hashlib
import logging
import os
from typing import Protocol, runtime_checkable

from packages.research.models_trusted import ExecutionResult, OutputFile, ResourceLimits

logger = logging.getLogger("research.sandbox")

#: 保温容器 Redis key 前缀。
WARM_KEY_PREFIX: str = "research:warm:"

#: 保温容器上限。
WARM_POOL_LIMIT: int = int(os.getenv("RESEARCH_WARM_POOL_LIMIT", "5"))

#: 保温 TTL（秒）。
WARM_TTL_SECONDS: int = int(os.getenv("RESEARCH_WARM_TTL_SECONDS", "180"))


@runtime_checkable
class SandboxRuntime(Protocol):
    """沙箱运行时接口抽象。

    开发环境使用 DockerSandboxRuntime，生产环境可替换为 K8sPodRuntime。
    接口隔离容器调度细节，Orchestrator 不感知底层实现。
    """

    async def create_container(
        self,
        input_package_path: str,
        image_digest: str,
        resource_limits: ResourceLimits,
    ) -> str:
        """创建隔离容器并挂载只读输入包。

        Args:
            input_package_path: 受控输入包路径（只读挂载到 /input）。
            image_digest: 固定版本科学计算镜像 digest。
            resource_limits: CPU/内存/时间/磁盘/输出限制。

        Returns:
            str: 容器标识符。
        """
        ...

    async def execute(
        self,
        container_id: str,
        script_content: str,
        timeout_seconds: int = 1200,
    ) -> ExecutionResult:
        """在容器中执行 Python 脚本。

        Args:
            container_id: 容器标识符。
            script_content: Python 脚本内容。
            timeout_seconds: 超时秒数（默认 20 分钟）。

        Returns:
            ExecutionResult: 执行结果（exit_code, stdout, stderr, timed_out）。
        """
        ...

    async def cancel(self, container_id: str) -> None:
        """取消容器中的执行。"""
        ...

    async def collect_output(
        self,
        container_id: str,
        whitelist: list[str],
    ) -> list[OutputFile]:
        """收集白名单输出文件。

        Args:
            container_id: 容器标识符。
            whitelist: 允许的文件名 glob 模式列表。

        Returns:
            list[OutputFile]: 输出文件列表。
        """
        ...

    async def destroy_container(self, container_id: str) -> None:
        """销毁容器。"""
        ...

    async def keep_warm(
        self,
        container_id: str,
        duration_seconds: int = 180,
    ) -> None:
        """将容器标记为保温状态（不计入槽位，独立上限 5 个）。

        Args:
            container_id: 容器标识符。
            duration_seconds: 保温时长（秒，默认 180）。
        """
        ...


class WarmPoolManager:
    """保温容器池管理器。

    使用 Redis 跟踪保温容器，TTL 过期后自动清理。
    独立上限（默认 5 个），不计入 20 用户槽位。

    Attributes:
        _redis: Redis 客户端。
        _limit: 保温容器上限。
        _ttl: 保温 TTL（秒）。
    """

    def __init__(
        self,
        redis_client: object,
        limit: int = WARM_POOL_LIMIT,
        ttl: int = WARM_TTL_SECONDS,
    ) -> None:
        """初始化保温池管理器。

        Args:
            redis_client: Redis 客户端实例。
            limit: 保温容器上限。
            ttl: 保温 TTL（秒）。
        """
        self._redis = redis_client
        self._limit = limit
        self._ttl = ttl

    async def acquire_warm_slot(self, container_id: str, run_id: str) -> bool:
        """尝试获取保温槽位。

        Args:
            container_id: 容器 ID。
            run_id: Run ID。

        Returns:
            bool: 成功获取返回 True，已达上限返回 False。
        """
        import redis as redis_lib

        # 检查当前保温容器数量
        count = self._redis.scard("research:warm_pool")
        if count >= self._limit:
            return False

        # 添加到保温池
        self._redis.sadd("research:warm_pool", container_id)
        self._redis.set(f"{WARM_KEY_PREFIX}{container_id}", run_id, ex=self._ttl)
        return True

    async def release_warm_slot(self, container_id: str) -> None:
        """释放保温槽位。

        Args:
            container_id: 容器 ID。
        """
        self._redis.srem("research:warm_pool", container_id)
        self._redis.delete(f"{WARM_KEY_PREFIX}{container_id}")

    async def get_warm_container(self, run_id: str) -> str | None:
        """查找指定 Run 的保温容器。

        Args:
            run_id: Run ID。

        Returns:
            str | None: 保温容器 ID，不存在时返回 None。
        """
        members = self._redis.smembers("research:warm_pool")
        for container_id in members:
            stored_run_id = self._redis.get(f"{WARM_KEY_PREFIX}{container_id}")
            if stored_run_id and stored_run_id.decode() == run_id:
                return container_id.decode() if isinstance(container_id, bytes) else container_id
        return None

    async def cleanup_expired(self) -> int:
        """清理过期的保温容器记录。

        Returns:
            int: 清理的记录数。
        """
        members = self._redis.smembers("research:warm_pool")
        cleaned = 0
        for container_id in members:
            cid = container_id.decode() if isinstance(container_id, bytes) else container_id
            if not self._redis.exists(f"{WARM_KEY_PREFIX}{cid}"):
                self._redis.srem("research:warm_pool", cid)
                cleaned += 1
        return cleaned


class DockerSandboxRuntime:
    """Docker 沙箱运行时实现（开发环境）。

    使用 aiodocker 库管理容器生命周期。

    安全配置：
    - network_mode='none'（断网）
    - user='nonroot:nonroot'（非 root）
    - read_only=True（只读基础镜像）
    - volumes={input: {'bind': '/input', 'mode': 'ro'}}（只读输入挂载）
    - tmpfs={'/workspace': 'size=Ng'}（临时工作目录）
    - cpu_period + cpu_quota 限制 CPU
    - mem_limit 限制内存
    - pids_limit=100（防 fork 炸弹）
    - cap_drop=['ALL'] + security_opt=['no-new-privileges']
    """

    def __init__(
        self,
        docker_url: str = "unix:///var/run/docker.sock",
        warm_pool: WarmPoolManager | None = None,
    ) -> None:
        """初始化 Docker 沙箱运行时。

        Args:
            docker_url: Docker API URL。
            warm_pool: 保温池管理器（可选）。
        """
        self._docker_url = docker_url
        self._warm_pool = warm_pool
        self._client: object | None = None

    async def _get_client(self) -> object:
        """获取 aiodocker 客户端（延迟初始化）。

        Returns:
            aiodocker.Docker: Docker 客户端实例。
        """
        if self._client is None:
            import aiodocker

            self._client = aiodocker.Docker(url=self._docker_url)
        return self._client

    async def create_container(
        self,
        input_package_path: str,
        image_digest: str,
        resource_limits: ResourceLimits,
    ) -> str:
        """创建隔离容器并挂载只读输入包。

        Args:
            input_package_path: 受控输入包路径（只读挂载到 /input）。
            image_digest: 固定版本科学计算镜像 digest。
            resource_limits: CPU/内存/时间/磁盘/输出限制。

        Returns:
            str: 容器 ID。
        """
        client = await self._get_client()

        config = self._build_container_config(
            input_package_path, image_digest, resource_limits
        )

        container = await client.containers.create(config)
        container_id = container.id if hasattr(container, "id") else str(container._id)

        await container.start()

        logger.info("Created container: %s (image=%s)", container_id, image_digest)
        return container_id

    async def execute(
        self,
        container_id: str,
        script_content: str,
        timeout_seconds: int = 1200,
    ) -> ExecutionResult:
        """在容器中执行 Python 脚本。

        将脚本写入 /workspace/script.py，然后执行 python /workspace/script.py。

        Args:
            container_id: 容器 ID。
            script_content: Python 脚本内容。
            timeout_seconds: 超时秒数。

        Returns:
            ExecutionResult: 执行结果。
        """
        client = await self._get_client()
        container = client.containers.container(container_id)

        # 将脚本写入容器
        import base64

        encoded_script = base64.b64encode(script_content.encode("utf-8")).decode("ascii")

        try:
            # 用 base64 + python -c 写入脚本文件（最可靠方式）
            import base64 as _b64

            b64 = _b64.b64encode(script_content.encode("utf-8")).decode("ascii")
            exec_write = await container.exec(
                ["python", "-c", f"import base64;open('/workspace/script.py','wb').write(base64.b64decode('{b64}'))"],
            )
            write_stream = exec_write.start()
            while True:
                msg = await write_stream.read_out()
                if msg is None:
                    break

            write_info = await exec_write.inspect()
            write_exit = write_info.get("ExitCode") if write_info else None
            logger.info("Script write via base64: exit=%s, b64_len=%d", write_exit, len(b64))
            if write_exit is not None and write_exit != 0:
                return ExecutionResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"Failed to write script (exit={write_exit})",
                    timed_out=False,
                    duration_seconds=0,
                )
            import time

            start_time = time.time()
            exec_run = await container.exec(
                ["python", "/workspace/script.py"],
                stdout=True,
                stderr=True,
            )

            try:
                stream = exec_run.start()
                stdout_parts: list[bytes] = []
                stderr_parts: list[bytes] = []

                async def read_stream():
                    while True:
                        msg = await stream.read_out()
                        if msg is None:
                            break
                        if msg.stream == 1:
                            stdout_parts.append(msg.data)
                        elif msg.stream == 2:
                            stderr_parts.append(msg.data)

                await asyncio.wait_for(read_stream(), timeout=timeout_seconds)
                duration = int(time.time() - start_time)

                # 获取退出码
                exec_info = await exec_run.inspect()
                exit_code = exec_info.get("ExitCode", 0) if exec_info else 0

                stdout = b"".join(stdout_parts).decode("utf-8", errors="replace")
                stderr = b"".join(stderr_parts).decode("utf-8", errors="replace")

                return ExecutionResult(
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=False,
                    duration_seconds=duration,
                )
            except asyncio.TimeoutError:
                duration = int(time.time() - start_time)
                logger.warning("Container execution timed out: %s", container_id)
                return ExecutionResult(
                    exit_code=-1,
                    stdout="",
                    stderr="Execution timed out",
                    timed_out=True,
                    duration_seconds=duration,
                )

        except Exception as exc:
            logger.exception("Container execution failed: %s", exc)
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                timed_out=False,
                duration_seconds=0,
            )

    async def cancel(self, container_id: str) -> None:
        """取消容器中的执行。

        Args:
            container_id: 容器 ID。
        """
        client = await self._get_client()
        container = client.containers.container(container_id)
        try:
            await container.kill(signal="SIGTERM")
        except Exception as exc:
            logger.warning("Failed to cancel container %s: %s", container_id, exc)

    async def collect_output(
        self,
        container_id: str,
        whitelist: list[str],
    ) -> list[OutputFile]:
        """收集白名单输出文件。

        从 /workspace/output/ 目录读取文件，按白名单 glob 模式过滤。

        Args:
            container_id: 容器 ID。
            whitelist: 允许的文件名 glob 模式列表。

        Returns:
            list[OutputFile]: 输出文件列表。
        """
        import fnmatch

        client = await self._get_client()
        container = client.containers.container(container_id)
        output_files: list[OutputFile] = []

        try:
            # 列出 /workspace/output/ 目录
            exec_ls = await container.exec(["ls", "/workspace/output/"])
            ls_stream = exec_ls.start()
            ls_chunks: list[bytes] = []
            while True:
                msg = await ls_stream.read_out()
                if msg is None:
                    break
                ls_chunks.append(msg.data)
            ls_output = b"".join(ls_chunks).decode("utf-8", errors="replace")

            filenames = [f.strip() for f in ls_output.split("\n") if f.strip()]

            for filename in filenames:
                # 白名单过滤
                if not any(fnmatch.fnmatch(filename, pattern) for pattern in whitelist):
                    continue

                # 读取文件内容
                exec_cat = await container.exec(["cat", f"/workspace/output/{filename}"])
                cat_stream = exec_cat.start()
                cat_chunks: list[bytes] = []
                while True:
                    msg = await cat_stream.read_out()
                    if msg is None:
                        break
                    cat_chunks.append(msg.data)
                cat_output = b"".join(cat_chunks)

                if isinstance(cat_output, bytes):
                    content = cat_output
                elif isinstance(cat_output, str):
                    content = cat_output.encode("utf-8")
                else:
                    content = b""

                content_hash = hashlib.sha256(content).hexdigest()

                output_files.append(
                    OutputFile(
                        filename=filename,
                        content=content,
                        content_hash=content_hash,
                        size_bytes=len(content),
                    )
                )

        except Exception as exc:
            logger.warning("Failed to collect output from %s: %s", container_id, exc)

        return output_files

    async def destroy_container(self, container_id: str) -> None:
        """销毁容器。

        Args:
            container_id: 容器 ID。
        """
        client = await self._get_client()
        container = client.containers.container(container_id)
        try:
            await container.kill()
        except Exception:
            pass
        try:
            await container.delete(force=True)
        except Exception as exc:
            logger.warning("Failed to delete container %s: %s", container_id, exc)

        # 释放保温槽位
        if self._warm_pool is not None:
            await self._warm_pool.release_warm_slot(container_id)

    async def keep_warm(
        self,
        container_id: str,
        duration_seconds: int = 180,
    ) -> None:
        """将容器标记为保温状态。

        注册到 WarmPoolManager，设置 Redis TTL。
        保温期间容器保持运行状态，支持连续图表调整。
        超过保温窗口后由 cleanup_expired 清理。

        Args:
            container_id: 容器 ID。
            duration_seconds: 保温时长（秒）。
        """
        if self._warm_pool is not None:
            acquired = await self._warm_pool.acquire_warm_slot(container_id, "unknown")
            if not acquired:
                # 保温池已满，直接销毁容器
                await self.destroy_container(container_id)
                return

        logger.info("Container %s kept warm for %d seconds", container_id, duration_seconds)

    def _build_container_config(
        self,
        input_package_path: str,
        image_digest: str,
        limits: ResourceLimits,
    ) -> dict:
        """构建容器配置。

        安全配置：
        - network_mode='none'（断网）
        - user='nonroot:nonroot'（非 root）
        - read_only=True（只读基础镜像）
        - volumes={input: {'bind': '/input', 'mode': 'ro'}}
        - tmpfs={'/workspace': 'size=Ng'}
        - cpu_period + cpu_quota
        - mem_limit
        - pids_limit=100
        - cap_drop=['ALL']
        - security_opt=['no-new-privileges']

        Args:
            input_package_path: 输入包路径。
            image_digest: 镜像 digest。
            limits: 资源限制。

        Returns:
            dict: Docker 容器配置。
        """
        cpu_quota = int(limits.cpu_count * 100000)

        return {
            "Image": image_digest,
            "NetworkMode": "none",
            "User": "sandbox",
            "HostConfig": {
                "ReadOnly": True,
                "Binds": [f"{input_package_path}:/input:ro"],
                "Tmpfs": {"/workspace": f"size={limits.disk_gb}g,uid=1000,gid=1000"},
                "CpuPeriod": 100000,
                "CpuQuota": cpu_quota,
                "Memory": limits.memory_mb * 1024 * 1024,
                "PidsLimit": 100,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
            },
            "WorkingDir": "/workspace",
            "Cmd": ["sleep", str(limits.timeout_seconds)],
        }
