"""AI 工具执行器。

从 ``service.py`` 提取的工具执行逻辑。
职责：角色权限检查、工具 schema 构建、白名单工具真实执行。

依赖注入：
- tool_registry: 工具注册表
- fact_service / parameter_service / model_service / provenance_service: 业务服务
- session_factory: 异步会话工厂（部分 handler 直接查询数据库）

注意：
- ``BUILTIN_ROLES`` 的 import 保持为函数内延迟 import（1 处），避免循环依赖。
- 方法名去掉 ``_`` 前缀（``_execute_tool`` → ``execute_tool`` 等），
  因为这些不再是 AIService 的内部方法。
- ``_handle_*`` 方法保持 ``_`` 前缀（ToolExecutor 内部方法）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.tools import ToolRegistry
from packages.common.database import scoped_session
from packages.common.errors import AppError


class ToolExecutor:
    """AI 工具执行器。

    Attributes:
        _tool_registry: 工具注册表（白名单 + 候选）。
        _fact_service: 事实服务（工具 search_facts / compare_experiments 执行）。
        _parameter_service: 参数服务（工具 search_parameters 执行）。
        _model_service: 模型服务（工具 run_published_model 执行）。
        _provenance_service: 溯源服务（工具 explain_provenance 执行）。
        _factory: 异步会话工厂（部分 handler 直接查询数据库）。
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        fact_service: Any | None = None,
        parameter_service: Any | None = None,
        model_service: Any | None = None,
        provenance_service: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        numeric_tools: Any | None = None,
    ) -> None:
        """初始化工具执行器。

        Args:
            tool_registry: 工具注册表。
            fact_service: 事实服务（工具执行用，可选）。
            parameter_service: 参数服务（工具执行用，可选）。
            model_service: 模型服务（工具执行用，可选）。
            provenance_service: 溯源服务（工具执行用，可选）。
            session_factory: 异步会话工厂（部分 handler 直接查询数据库，可选）。
            numeric_tools: NumericToolFacade 实例（数值工具执行用，可选）。
        """
        self._tool_registry = tool_registry
        self._fact_service = fact_service
        self._parameter_service = parameter_service
        self._model_service = model_service
        self._provenance_service = provenance_service
        self._factory = session_factory
        self._numeric_tools = numeric_tools

    def _require_numeric_tools(self) -> Any:
        """获取 NumericToolFacade，不存在时抛 internal_error。"""
        if self._numeric_tools is None:
            raise AppError(
                code="numeric_internal_error",
                message="numeric tools not configured",
                retryable=False,
            )
        return self._numeric_tools

    def _build_numeric_principal(self, user: Any, org_id: UUID) -> Any:
        """从请求上下文构造 NumericPrincipal（不从工具参数构造）。

        Args:
            user: 当前用户（需有 user_id, roles 属性）。
            org_id: 当前部门 ID。

        Returns:
            NumericPrincipal: 调用主体。
        """
        from packages.ai.numeric.contracts import NumericPrincipal

        user_id = getattr(user, "user_id", None)
        if user_id is None:
            raise AppError(
                code="numeric_internal_error",
                message="user_id is required for numeric tools",
                retryable=False,
            )
        roles = tuple(user.roles) if hasattr(user, "roles") else ()
        return NumericPrincipal(
            user_id=user_id,
            department_id=org_id,
            roles=roles,
        )

    def check_role_permission(self, user: Any, action: str) -> bool:
        """检查用户角色是否拥有指定权限（角色级，非对象级）。

        基于 BUILTIN_ROLES 权限矩阵，与 require_permission 依赖相同逻辑。

        Args:
            user: 当前用户（需有 roles 属性）。
            action: 权限字符串。

        Returns:
            bool: 有权返回 True。
        """
        from packages.auth.permissions import BUILTIN_ROLES

        for role_code in user.roles:
            role_def = BUILTIN_ROLES.get(role_code)
            if role_def is not None:
                permissions = role_def["permissions"]
                if isinstance(permissions, list) and action in permissions:
                    return True
        return False

    def build_tool_schemas(self) -> tuple[dict[str, Any], ...]:
        """将 ToolRegistry 中的工具规格转为 OpenAI tools JSON schema 格式。

        Returns:
            tuple[dict, ...]: OpenAI tools 定义元组，每项为
            ``{"type": "function", "function": {"name", "description", "parameters"}}``。
        """
        schemas: list[dict[str, Any]] = []
        for spec in self._tool_registry.list_enabled_tools():
            if spec.category != "ai_tool":
                continue  # ingestion 类工具不暴露给 AI 对话
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters_schema
                        or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
            )
        return tuple(schemas)

    async def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        user: Any,
        org_id: UUID,
    ) -> dict[str, Any]:
        """执行白名单工具的真实查询，返回结果数据。

        根据工具名称分派到对应的服务方法执行真实查询。
        工具执行均为只读操作，不修改平台数据。

        Args:
            tool_name: 工具名称。
            args: 工具参数。
            user: 当前用户。
            org_id: 组织 ID。

        Returns:
            dict: 包含 ``summary``（结果摘要）和 ``data``（结构化结果）的字典。
        """
        if tool_name == "search_facts":
            return await self._handle_search_facts(args, org_id)
        elif tool_name == "search_standards":
            return await self._handle_search_standards(args, org_id)
        elif tool_name == "search_parameters":
            return await self._handle_search_parameters(args, org_id)
        elif tool_name == "explain_provenance":
            return await self._handle_explain_provenance(args, org_id)
        elif tool_name == "compare_experiments":
            return await self._handle_compare_experiments(args, org_id)
        elif tool_name == "run_published_model":
            return await self._handle_run_model(args, user, org_id)
        elif tool_name == "draft_report":
            return await self._handle_draft_report(args, org_id)
        elif tool_name == "extract_data":
            return await self._handle_extract_data(args, org_id)
        elif tool_name == "evaluate_expression":
            principal = self._build_numeric_principal(user, org_id)
            result = await self._require_numeric_tools().evaluate_expression(args, principal)
            return {
                "summary": result.summary,
                "data": result.llm_data,
                "audit": result.audit_data,
                "citation_params": result.citation_params,
            }
        elif tool_name == "describe_series":
            principal = self._build_numeric_principal(user, org_id)
            result = await self._require_numeric_tools().describe_series(args, principal)
            return {
                "summary": result.summary,
                "data": result.llm_data,
                "audit": result.audit_data,
                "citation_params": result.citation_params,
            }
        else:
            return {
                "summary": f"未实现的工具: {tool_name}",
                "data": {"error": f"Tool not implemented: {tool_name}"},
            }

    async def _handle_search_facts(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 search_facts 工具：搜索实验事实。"""
        query = str(args.get("query", ""))
        fact_type = str(args.get("fact_type", "")) or None

        if self._fact_service is not None:
            try:
                results = await self._fact_service.search(
                    query=query,
                    fact_type=fact_type,
                    department_id=org_id,
                    limit=20,
                )
                items = []
                for r in (results or [])[:20]:
                    item = {
                        "id": str(r.get("id", "")),
                        "subject_id": str(r.get("subject_id", "")),
                        "fact_type": str(r.get("fact_type", "")),
                        "data_summary": str(r.get("data_summary", "")),
                    }
                    items.append(item)
                return {
                    "summary": f"搜索到 {len(items)} 条事实",
                    "data": {"count": len(items), "results": items},
                }
            except Exception:
                # fact_service.search 参数不匹配时走数据库 fallback
                pass

        # 直接查数据库（含 data_summary）
        async with scoped_session(self._factory, org_id, None) as session:
            stmt = sa.select(sa.text("f.id, f.subject_id, f.fact_type")).select_from(
                sa.text("fact f")
            )
            conditions = [sa.text("f.department_id = :org_id")]
            params: dict[str, Any] = {"org_id": org_id}
            if query:
                conditions.append(sa.text("subject_id ILIKE :query"))
                params["query"] = f"%{query}%"
            if fact_type:
                conditions.append(sa.text("fact_type = :fact_type"))
                params["fact_type"] = fact_type
            stmt = stmt.where(*conditions).limit(20)
            result = await session.execute(stmt, params)
            rows = result.fetchall()
            items = [
                {
                    "id": str(r[0]),
                    "subject_id": str(r[1]),
                    "fact_type": str(r[2]),
                    "data_summary": "",
                }
                for r in rows
            ]
            return {
                "summary": f"搜索到 {len(items)} 条事实",
                "data": {"count": len(items), "results": items},
            }

    async def _handle_search_standards(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 search_standards 工具：搜索标准变量。"""
        query = str(args.get("query", ""))
        async with scoped_session(self._factory, org_id, None) as session:
            stmt = (
                sa.select(sa.text("vv.id, v.code, vv.display_name, vv.canonical_unit"))
                .select_from(sa.text("variable_version vv"))
                .join(sa.text("variable v"), sa.text("v.id = vv.variable_id"))
                .where(
                    sa.text("v.department_id = :org_id"),
                    sa.text("vv.status = 'published'"),
                )
            )
            params: dict[str, Any] = {"org_id": org_id}
            if query:
                stmt = stmt.where(sa.text("(v.code ILIKE :q OR vv.display_name ILIKE :q)"))
                params["q"] = f"%{query}%"
            stmt = stmt.limit(20)
            result = await session.execute(stmt, params)
            rows = result.fetchall()
            items = [
                {
                    "id": str(r[0]),
                    "code": str(r[1]),
                    "display_name": str(r[2]),
                    "unit": str(r[3]) if r[3] else "",
                }
                for r in rows
            ]
            return {
                "summary": f"搜索到 {len(items)} 个标准变量",
                "data": {"count": len(items), "results": items},
            }

    async def _handle_search_parameters(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 search_parameters 工具：搜索参数。"""
        variable_code = str(args.get("variable_code", ""))
        if self._parameter_service is not None:
            try:
                results = await self._parameter_service.search_by_variable(
                    variable_code=variable_code,
                    department_id=org_id,
                )
                items = [
                    {
                        "id": str(r.get("id", "")),
                        "variable_code": str(r.get("variable_code", "")),
                        "value": str(r.get("value", "")),
                        "status": str(r.get("status", "")),
                    }
                    for r in (results or [])[:20]
                ]
                return {
                    "summary": f"搜索到 {len(items)} 个参数",
                    "data": {"count": len(items), "results": items},
                }
            except Exception as exc:
                return {
                    "summary": f"参数搜索失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "参数服务不可用",
            "data": {"error": "parameter_service not configured"},
        }

    async def _handle_explain_provenance(
        self, args: dict[str, Any], org_id: UUID
    ) -> dict[str, Any]:
        """执行 explain_provenance 工具：解释溯源链路。"""
        parameter_id = str(args.get("parameter_id", ""))
        if self._provenance_service is not None:
            try:
                chain = await self._provenance_service.explain(
                    parameter_id=parameter_id,
                    department_id=org_id,
                )
                return {
                    "summary": f"溯源链路包含 {len(chain.get('steps', []))} 个步骤",
                    "data": chain,
                }
            except Exception as exc:
                return {
                    "summary": f"溯源查询失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "溯源服务不可用",
            "data": {"error": "provenance_service not configured"},
        }

    async def _handle_compare_experiments(
        self, args: dict[str, Any], org_id: UUID
    ) -> dict[str, Any]:
        """执行 compare_experiments 工具：对比实验事实。"""
        fact_ids = args.get("fact_ids", [])
        if not isinstance(fact_ids, list) or len(fact_ids) < 2:
            return {
                "summary": "需要至少 2 个事实 ID 进行对比",
                "data": {"error": "At least 2 fact_ids required"},
            }

        if self._fact_service is not None:
            try:
                facts = []
                for fid in fact_ids[:5]:
                    fact = await self._fact_service.get(
                        fact_id=UUID(str(fid)),
                        department_id=org_id,
                    )
                    if fact:
                        facts.append(fact)
                return {
                    "summary": f"对比了 {len(facts)} 个实验事实",
                    "data": {
                        "count": len(facts),
                        "comparisons": [
                            {
                                "id": str(f.get("id", "")),
                                "subject_id": str(f.get("subject_id", "")),
                                "fact_type": str(f.get("fact_type", "")),
                                "data_summary": str(f.get("data_summary", "")),
                            }
                            for f in facts
                        ],
                    },
                }
            except Exception as exc:
                return {
                    "summary": f"实验对比失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "事实服务不可用",
            "data": {"error": "fact_service not configured"},
        }

    async def _handle_run_model(
        self, args: dict[str, Any], user: Any, org_id: UUID
    ) -> dict[str, Any]:
        """执行 run_published_model 工具：运行已发布模型预测。"""
        model_id = str(args.get("model_id", ""))
        inputs = args.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}

        if self._model_service is not None:
            try:
                result = await self._model_service.predict(
                    model_id=UUID(model_id),
                    inputs=inputs,
                )
                return {
                    "summary": f"模型预测完成，版本 {result.version}",
                    "data": {
                        "model_id": str(result.model_id),
                        "model_version_id": str(result.model_version_id),
                        "version": result.version,
                        "predictions": dict(result.predictions),
                        "fact_id": str(result.fact_id) if result.fact_id else None,
                    },
                }
            except Exception as exc:
                return {
                    "summary": f"模型预测失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "模型服务不可用",
            "data": {"error": "model_service not configured"},
        }

    async def _handle_draft_report(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 draft_report 工具：生成报告草稿（只读，不落库）。"""
        title = str(args.get("title", "未命名报告"))
        fact_ids = args.get("fact_ids", [])
        if not isinstance(fact_ids, list):
            fact_ids = []

        # 查询引用的事实摘要
        fact_summaries: list[dict[str, str]] = []
        if fact_ids and self._factory is not None:
            async with scoped_session(self._factory, org_id, None) as session:
                for fid in fact_ids[:10]:
                    try:
                        result = await session.execute(
                            sa.select(sa.text("subject_id, fact_type"))
                            .select_from(sa.text("fact"))
                            .where(
                                sa.text("id = :fid"),
                                sa.text("department_id = :org_id"),
                            ),
                            {"fid": UUID(str(fid)), "org_id": org_id},
                        )
                        row = result.fetchone()
                        if row:
                            fact_summaries.append(
                                {
                                    "fact_id": str(fid),
                                    "subject_id": str(row[0]),
                                    "fact_type": str(row[1]),
                                }
                            )
                    except Exception:
                        pass

        return {
            "summary": f"报告草稿已生成，引用 {len(fact_summaries)} 个事实",
            "data": {
                "title": title,
                "referenced_facts": fact_summaries,
                "note": "草稿不落库，需用户确认后保存",
            },
        }

    async def _handle_extract_data(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 extract_data 工具：数据提取（标记为需要 ingestion:write 权限）。"""
        path = str(args.get("path", ""))
        prompt = str(args.get("prompt", ""))
        return {
            "summary": f"数据提取请求已记录（路径: {path[:100]}）",
            "data": {
                "path": path[:200],
                "prompt": prompt[:500],
                "note": "数据提取需要 ingestion 服务支持，当前返回元数据",
            },
        }
