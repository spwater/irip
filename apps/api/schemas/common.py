"""跨路由共享的通用 Pydantic 响应模型。

提供简单的状态响应模型，供多个路由文件复用，
避免在每个文件中重复定义相同结构的小型响应模型。
"""

from pydantic import BaseModel


class OkResponse(BaseModel):
    """通用 ``{"ok": True}`` 响应模型。

    用于仅返回操作成功标志的端点（如 delete、withdraw 等）。
    """

    ok: bool


class StatusResponse(BaseModel):
    """通用 ``{"status": "..."}`` 响应模型。

    用于返回单字段状态字符串的端点（如 archive、restore、activate 等）。
    """

    status: str
