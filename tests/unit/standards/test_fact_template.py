"""事实模板、方法与标准包单元测试（IRIP Task 12）。

验证：
- 创建模板 → status=draft；
- 添加观测到模板；
- 验证模板：缺少必需观测 → missing_observation；
- 验证模板：重复观测编码 → duplicate_observation；
- 验证模板：引用未发布变量 → reference_not_published；
- 验证模板：合法模板 → valid=True；
- 方法生命周期：创建 → 提交 → 发布 → 不可变；
- 标准包：创建 → 添加引用 → 提交（验证已发布）→ 发布（冻结引用）；
- 标准包：草稿变量引用 → 提交失败 reference_not_published:variable；
- 标准包：已发布后不可添加引用；
- 标准包生命周期：draft → submit → publish → deprecate。

依赖数据库（需设置 IRIP_TEST_DATABASE_URL）。
"""

import pytest
from uuid import UUID

from packages.common.errors import AppError
from packages.standards.methods import MethodService
from packages.standards.packages import PackageService
from packages.standards.service import StandardService
from packages.standards.templates import TemplateService


class TestFactTemplate:
    """事实模板创建、观测添加与验证测试。"""

    @pytest.mark.asyncio
    async def test_create_template_draft(
        self, template_service: TemplateService
    ) -> None:
        """创建模板 → status=draft, version_count=0。"""
        template = await template_service.create_template(
            code="exp_run_tpl",
            display_name="实验运行模板",
            fact_type="experiment_run",
        )
        assert template.status == "draft"
        assert template.version_count == 0
        assert template.code == "exp_run_tpl"
        assert template.fact_type == "experiment_run"

    @pytest.mark.asyncio
    async def test_add_observation_to_template(
        self,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """添加观测到模板，草稿版本被创建。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="obs_var",
            display_name="观测变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建模板并添加观测
        template = await template_service.create_template(
            code="obs_tpl",
            display_name="观测模板",
            fact_type="experiment_run",
        )
        draft_version = await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=True,
            cardinality="one",
        )
        assert draft_version.status == "draft"
        assert draft_version.version == 1
        assert len(draft_version.observations) == 1
        obs = draft_version.observations[0]
        assert obs["variable_version_id"] == str(version.id)
        assert obs["required"] is True
        assert obs["cardinality"] == "one"

    @pytest.mark.asyncio
    async def test_validate_missing_required_observation(
        self,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """验证模板：必需观测引用未发布变量 → missing_observation。"""
        # 创建变量但只提交不发布
        variable = await standard_service.create_variable(
            code="missing_var",
            display_name="缺失变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        # 不发布，version 处于 in_review

        # 创建模板并添加必需观测
        template = await template_service.create_template(
            code="missing_obs_tpl",
            display_name="缺失观测模板",
            fact_type="experiment_run",
        )
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=True,
            cardinality="one",
        )

        # 验证 → 应报告 missing_observation
        report = await template_service.validate_template(template.id)
        assert not report.valid
        assert any(c.startswith("missing_observation:") for c in report.codes)

    @pytest.mark.asyncio
    async def test_validate_duplicate_observation(
        self,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """验证模板：重复观测编码 → duplicate_observation。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="dup_var",
            display_name="重复变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建模板并添加两个相同变量的观测
        template = await template_service.create_template(
            code="dup_obs_tpl",
            display_name="重复观测模板",
            fact_type="experiment_run",
        )
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=True,
            cardinality="one",
        )
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=False,
            cardinality="many",
        )

        # 验证 → 应报告 duplicate_observation
        report = await template_service.validate_template(template.id)
        assert not report.valid
        assert any(
            c.startswith("duplicate_observation:") for c in report.codes
        )

    @pytest.mark.asyncio
    async def test_validate_unpublished_variable_reference(
        self,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """验证模板：非必需观测引用未发布变量 → reference_not_published。"""
        # 创建变量但只提交不发布
        variable = await standard_service.create_variable(
            code="unpub_var",
            display_name="未发布变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)

        # 创建模板并添加非必需观测
        template = await template_service.create_template(
            code="unpub_ref_tpl",
            display_name="未发布引用模板",
            fact_type="experiment_run",
        )
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=False,
            cardinality="one",
        )

        # 验证 → 应报告 reference_not_published
        report = await template_service.validate_template(template.id)
        assert not report.valid
        assert any(
            c.startswith("reference_not_published:")
            for c in report.codes
        )

    @pytest.mark.asyncio
    async def test_validate_valid_template(
        self,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """验证合法模板 → valid=True。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="valid_var",
            display_name="合法变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建模板并添加必需观测
        template = await template_service.create_template(
            code="valid_tpl",
            display_name="合法模板",
            fact_type="experiment_run",
        )
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=True,
            cardinality="one",
        )

        # 验证 → 应通过
        report = await template_service.validate_template(template.id)
        assert report.valid
        assert len(report.codes) == 0

    @pytest.mark.asyncio
    async def test_template_full_lifecycle(
        self,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """模板完整生命周期：创建 → 添加观测 → 提交 → 发布。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="lifecycle_var",
            display_name="生命周期变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建模板
        template = await template_service.create_template(
            code="lifecycle_tpl",
            display_name="生命周期模板",
            fact_type="experiment_run",
        )
        assert template.status == "draft"

        # 添加观测
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=True,
            cardinality="one",
        )

        # 提交
        submitted = await template_service.submit_template(template.id)
        assert submitted.status == "in_review"

        # 发布
        published = await template_service.publish_template(template.id)
        assert published.status == "published"
        assert published.published_at is not None

        # 验证模板状态
        detail = await template_service.get_template(template.id)
        assert detail["status"] == "published"
        assert detail["version_count"] == 1


class TestMethod:
    """方法生命周期测试。"""

    @pytest.mark.asyncio
    async def test_method_lifecycle(
        self, method_service: MethodService
    ) -> None:
        """方法生命周期：创建 → 提交 → 发布 → 不可变。"""
        # 创建
        method = await method_service.create_method(
            code="test_method",
            display_name="测试方法",
            description="用于测试的方法",
        )
        assert method.status == "draft"
        assert method.version_count == 0

        # 提交
        v1 = await method_service.submit_method(method.id)
        assert v1.status == "in_review"
        assert v1.version == 1

        # 发布
        v1_pub = await method_service.publish_method(method.id)
        assert v1_pub.status == "published"
        assert v1_pub.published_at is not None

        # 验证方法状态
        detail = await method_service.get_method(method.id)
        assert detail["status"] == "published"
        assert detail["version_count"] == 1

    @pytest.mark.asyncio
    async def test_published_method_immutable(
        self, method_service: MethodService
    ) -> None:
        """已发布方法不可再次提交（invalid_transition）。"""
        method = await method_service.create_method(
            code="immutable_method",
            display_name="不可变方法",
        )
        await method_service.submit_method(method.id)
        await method_service.publish_method(method.id)

        # 尝试再次提交 → 应抛出 invalid_transition
        with pytest.raises(AppError) as exc_info:
            await method_service.submit_method(method.id)
        assert exc_info.value.code == "invalid_transition"

    @pytest.mark.asyncio
    async def test_method_deprecate(
        self, method_service: MethodService
    ) -> None:
        """方法弃用：published → deprecated。"""
        method = await method_service.create_method(
            code="deprecate_method",
            display_name="弃用方法",
        )
        await method_service.submit_method(method.id)
        await method_service.publish_method(method.id)

        v_dep = await method_service.deprecate_method(method.id)
        assert v_dep.status == "deprecated"
        assert v_dep.deprecated_at is not None

        detail = await method_service.get_method(method.id)
        assert detail["status"] == "deprecated"


class TestStandardPackage:
    """标准包测试。"""

    @pytest.mark.asyncio
    async def test_create_package_draft(
        self, package_service: PackageService
    ) -> None:
        """创建包 → status=draft, version_count=0。"""
        pkg = await package_service.create_package(
            code="test_pkg",
            display_name="测试包",
            description="用于测试的标准包",
        )
        assert pkg.status == "draft"
        assert pkg.version_count == 0
        assert pkg.code == "test_pkg"

    @pytest.mark.asyncio
    async def test_package_with_published_variable(
        self,
        package_service: PackageService,
        standard_service: StandardService,
    ) -> None:
        """包：添加已发布变量引用 → 提交通过 → 发布冻结。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="pkg_var",
            display_name="包变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建包
        pkg = await package_service.create_package(
            code="pub_var_pkg",
            display_name="已发布变量包",
        )

        # 添加变量引用
        await package_service.add_variable_ref(
            pkg.id, variable.id, version=1
        )

        # 提交 → 验证通过
        report = await package_service.submit_package(pkg.id)
        assert report.valid
        assert len(report.codes) == 0

        # 发布
        published = await package_service.publish_package(pkg.id)
        assert published.status == "published"
        assert published.published_at is not None

        # 验证包状态
        detail = await package_service.get_package(pkg.id)
        assert detail["status"] == "published"
        assert detail["version_count"] == 1

    @pytest.mark.asyncio
    async def test_package_draft_variable_ref_submit_fails(
        self,
        package_service: PackageService,
        standard_service: StandardService,
    ) -> None:
        """包：草稿变量引用 → 提交失败 reference_not_published:variable。"""
        # 创建变量但只提交不发布
        variable = await standard_service.create_variable(
            code="draft_ref_var",
            display_name="草稿引用变量",
            data_type="number",
            canonical_unit="mm",
        )
        await standard_service.submit_for_review(variable.id)
        # 不发布

        # 创建包
        pkg = await package_service.create_package(
            code="draft_ref_pkg",
            display_name="草稿引用包",
        )

        # 添加变量引用（引用的是 version 1，处于 in_review，未发布）
        await package_service.add_variable_ref(
            pkg.id, variable.id, version=1
        )

        # 提交 → 应失败
        with pytest.raises(AppError) as exc_info:
            await package_service.submit_package(pkg.id)
        assert exc_info.value.code == "validation_failed"
        codes = exc_info.value.fields.get("codes", [])
        assert any(
            c == "reference_not_published:variable" for c in codes
        )

    @pytest.mark.asyncio
    async def test_published_package_immutable(
        self,
        package_service: PackageService,
        standard_service: StandardService,
    ) -> None:
        """已发布包不可添加引用。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="immut_var",
            display_name="不可变变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建包并完成生命周期
        pkg = await package_service.create_package(
            code="immut_pkg",
            display_name="不可变包",
        )
        await package_service.add_variable_ref(
            pkg.id, variable.id, version=1
        )
        await package_service.submit_package(pkg.id)
        await package_service.publish_package(pkg.id)

        # 尝试添加引用 → 应抛出 invalid_transition
        with pytest.raises(AppError) as exc_info:
            await package_service.add_variable_ref(
                pkg.id, variable.id, version=1
            )
        assert exc_info.value.code == "invalid_transition"

    @pytest.mark.asyncio
    async def test_package_full_lifecycle(
        self,
        package_service: PackageService,
        standard_service: StandardService,
    ) -> None:
        """包完整生命周期：draft → submit → publish → deprecate。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="lifecycle_pkg_var",
            display_name="生命周期包变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建包
        pkg = await package_service.create_package(
            code="lifecycle_pkg",
            display_name="生命周期包",
        )
        assert pkg.status == "draft"

        # 添加引用
        await package_service.add_variable_ref(
            pkg.id, variable.id, version=1
        )

        # 提交
        report = await package_service.submit_package(pkg.id)
        assert report.valid

        # 发布
        published = await package_service.publish_package(pkg.id)
        assert published.status == "published"

        # 弃用
        deprecated = await package_service.deprecate_package(pkg.id)
        assert deprecated.status == "deprecated"
        assert deprecated.deprecated_at is not None

        # 验证包状态
        detail = await package_service.get_package(pkg.id)
        assert detail["status"] == "deprecated"

    @pytest.mark.asyncio
    async def test_package_with_method_ref(
        self,
        package_service: PackageService,
        method_service: MethodService,
    ) -> None:
        """包：添加已发布方法引用 → 提交通过。"""
        # 创建并发布方法
        method = await method_service.create_method(
            code="pkg_method",
            display_name="包方法",
        )
        await method_service.submit_method(method.id)
        await method_service.publish_method(method.id)

        # 创建包
        pkg = await package_service.create_package(
            code="method_ref_pkg",
            display_name="方法引用包",
        )

        # 添加方法引用
        await package_service.add_method_ref(
            pkg.id, method.id, version=1
        )

        # 提交 → 验证通过
        report = await package_service.submit_package(pkg.id)
        assert report.valid
        assert len(report.codes) == 0

    @pytest.mark.asyncio
    async def test_package_with_template_ref(
        self,
        package_service: PackageService,
        template_service: TemplateService,
        standard_service: StandardService,
    ) -> None:
        """包：添加已发布模板引用 → 提交通过。"""
        # 创建并发布变量
        variable = await standard_service.create_variable(
            code="tpl_ref_var",
            display_name="模板引用变量",
            data_type="number",
            canonical_unit="mm",
        )
        version = await standard_service.submit_for_review(variable.id)
        await standard_service.publish_variable(variable.id)

        # 创建并发布模板
        template = await template_service.create_template(
            code="pkg_template",
            display_name="包模板",
            fact_type="experiment_run",
        )
        await template_service.add_observation(
            template_id=template.id,
            variable_version_id=version.id,
            required=True,
            cardinality="one",
        )
        await template_service.submit_template(template.id)
        await template_service.publish_template(template.id)

        # 创建包
        pkg = await package_service.create_package(
            code="template_ref_pkg",
            display_name="模板引用包",
        )

        # 添加模板引用
        await package_service.add_template_ref(
            pkg.id, template.id, version=1
        )

        # 提交 → 验证通过
        report = await package_service.submit_package(pkg.id)
        assert report.valid
        assert len(report.codes) == 0
