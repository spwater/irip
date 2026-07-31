"""引用值对象：AI 回答中指向平台对象的溯源引用。

Citation 是不可变值对象，用于在 AI 回答中标注回答依据的来源对象
（标准变量、事实修订、参数版本、推导运行、模型版本等）。

前端通过 ``href`` 跳转到对应对象详情页，通过 ``label`` 展示可读标题，
通过 ``object_type`` + ``object_id`` + ``version`` 构成稳定三元组。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    """引用（不可变值对象）。

    Attributes:
        object_type: 对象类型（如 ``"parameter_version"``、``"fact"``、
            ``"derivation_run"``、``"model_version"``、``"standard_variable"``）。
        object_id: 对象 UUID（字符串形式）。
        version: 版本标识（如 ``"v3"``、``"rev 2"``、``"run #5"``），
            无版本概念时为空字符串。
        label: 可读标签（如 ``"粒度参数 D50 v3"``），供前端展示。
        href: 前端路由路径（如 ``"/parameters/abc-123"``），供点击跳转。
    """

    object_type: str
    object_id: str
    version: str
    label: str
    href: str

    def to_dict(self) -> dict[str, str]:
        """序列化为 JSON 可存储的字典。"""
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "version": self.version,
            "label": self.label,
            "href": self.href,
        }
