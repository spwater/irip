"""研究成果包生命周期管理服务：PublicationService（向后兼容 re-export）。

本文件原为 PublicationService 的单体实现（1868 行），现已按功能域拆分为：
- _base.py：_PublicationBase（共享属性 / _require_actor / _check_result_visible）
- publisher.py：_PublishMixin（发布）+ PublicationService 组装
- acl.py：_AclMixin（ACL 修改）
- revision.py：_RevisionMixin（版本管理 / 详情查询）
- reuse.py：_ReuseMixin（内部对象引用 / 复用 / 收藏）

为保持向后兼容，``from packages.research.publication.publication import PublicationService``
仍可正常工作。
"""

from packages.research.publication.publisher import PublicationService  # noqa: F401

__all__ = ["PublicationService"]
