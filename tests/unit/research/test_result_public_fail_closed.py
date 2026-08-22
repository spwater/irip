"""0090 迁移：公开 Result 读路径 fail-closed 校验（代码审查式测试）。

Remediation B1：0088 引入的 ACL 感知 RLS 谓词中，已发布且
``current_acl_type='all'`` 的成果包对**缺失/空** ``app.current_user_id`` GUC
的会话仍可见（``all`` 分支完全不引用用户 GUC），导致用户上下文缺失时泄露
"公开 Result"，违背计划 §6 风险 #2 的 fail-closed 要求。

0090 将整个 "published + ACL" 子分支门控在 ``_UID IS NOT NULL`` 之后，
使公开 Result 仅在存在有效用户上下文时可见，GUC 缺失即返回空集。
本测试为代码审查（不依赖数据库），验证迁移模块 revision 链、谓词形状
及对 ``research_result`` / ``research_result_version`` 两表的加固。
"""

import importlib


def _load_module() -> object:
    """动态导入 0090 迁移模块。"""
    return importlib.import_module("migrations.versions.0090_public_result_fail_closed")


class TestMigration0090:
    """验证 0090 迁移链与加固谓词。"""

    def test_revision_and_down_revision(self) -> None:
        """0090.revision == '0090' 且 down_revision == '0089'。"""
        mod = _load_module()
        assert mod.revision == "0090"
        assert mod.down_revision == "0089"

    def test_public_all_branch_still_present(self) -> None:
        """加固后仍保留 acl_type='all' 公开分支（同上下文内可见）。"""
        mod = _load_module()
        pred = mod._result_acl_read("research_result")
        assert "current_acl_type = 'all'" in pred

    def test_published_branch_gated_on_non_null_user(self) -> None:
        """published 子分支被 _UID IS NOT NULL 门控（缺失 GUC 即 fail-closed）。"""
        mod = _load_module()
        pred = mod._result_acl_read("research_result")
        assert "IS NOT NULL" in pred
        # 门控必须位于 status = 'published' 之前，确保整段公开读都受约束。
        assert pred.index("IS NOT NULL") < pred.index("status = 'published'")

    def test_owner_branch_present(self) -> None:
        """owner 分支仍引用 owner_user_id = current_user（NULL 即 false）。"""
        mod = _load_module()
        pred = mod._result_acl_read("research_result")
        assert "owner_user_id = NULLIF(" in pred

    def test_tree_branch_uses_visible_depts(self) -> None:
        """tree 分支仍锚定 current_visible_dept_ids()。"""
        mod = _load_module()
        pred = mod._result_acl_read("r")
        assert "current_visible_dept_ids()" in pred


class TestMigration0090Coverage:
    """验证 0090 同时加固 research_result 与 research_result_version。"""

    def test_upgrade_recreates_both_policies(self) -> None:
        """upgrade() 对两张表重建策略，_recreate_policy 含 DROP IF EXISTS + CREATE。"""
        import inspect

        mod = _load_module()
        upgrade_src = inspect.getsource(mod.upgrade)
        assert "research_result" in upgrade_src
        assert "research_result_version" in upgrade_src

        recreate_src = inspect.getsource(mod._recreate_policy)
        assert "DROP POLICY IF EXISTS research_workspace_isolation" in recreate_src
        assert "CREATE POLICY research_workspace_isolation" in recreate_src
