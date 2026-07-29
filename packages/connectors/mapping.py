"""映射评分与映射配置生命周期服务（IRIP Task 13）。

本模块包含四个核心组件：

1. SecretStore: 密钥存储，按 secret_id 解析凭据（组织隔离）。
2. MappingService: 源字段→已发布标准变量的评分排序（rank）。
3. MappingProfileService: 映射配置生命周期（创建/提交/发布/拒绝/查询），
   发布后规则不可变。
4. IngestionService: 数据源预览（按 kind 构造连接器并预览）。

安全约定：
- 密钥凭据绝不返回、绝不记录日志；
- 已发布配置规则不可变（published_version_immutable）。
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.connectors.contracts import (
    ConnectorSource,
    MappingCandidate,
    MappingRule,
    PreviewTable,
)
from packages.connectors.entities import (
    MappingProfile,
    MappingProfileVersion,
    Secret,
)
from packages.standards.state_machine import StandardStatus, assert_transition
from packages.standards.units import UnitConverter
from packages.standards.variables import Variable, VariableAlias, VariableVersion

# ---- 评分权重 ----
# 名称匹配组件互斥（源字段名只会命中 code / alias / display_name 之一），
# 故实际最大可达分数 = max(名称组件) + unit_dimension + data_type + prior_confirmed = 1.00。
# 约束：exact_code + unit_dimension + data_type = 0.55 + 0.25 + 0.15 = 0.95 ≥ 0.90。
_WEIGHT_EXACT_CODE: float = 0.55
_WEIGHT_ALIAS_MATCH: float = 0.50
_WEIGHT_BILINGUAL_NAME: float = 0.50
_WEIGHT_UNIT_DIMENSION: float = 0.25
_WEIGHT_DATA_TYPE: float = 0.15
_WEIGHT_PRIOR_CONFIRMED: float = 0.05

# ---- JSON Schema 路径 ----
_SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[2] / "schemas" / "mapping-profile" / "v1.schema.json"
)


class SecretStore:
    """密钥存储：按 secret_id 解析凭据（组织隔离）。

    F-12: 使用 envelope encryption 加密存储密钥值。
    写入时加密，读取时解密，绝不返回凭据给 API 层，仅由连接器内部使用。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID（隔离过滤）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
    ) -> None:
        """初始化密钥存储。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
        """
        self._factory = session_factory
        self._org_id = organization_id

    async def get(self, secret_id: UUID) -> str:
        """按 ID 解析密钥值（读取时解密）。

        F-12: 使用 envelope encryption 解密存储的密钥。

        Args:
            secret_id: 密钥 UUID。

        Returns:
            str: 凭据明文。

        Raises:
            AppError: code="secret_not_found"，当密钥不存在或不属于当前组织时。
        """
        async with self._factory() as session:
            result = await session.execute(
                sa.select(Secret).where(
                    Secret.id == secret_id,
                    Secret.organization_id == self._org_id,
                )
            )
            secret = result.scalar_one_or_none()
        if secret is None:
            raise AppError(
                code="secret_not_found",
                message="密钥不存在或无权访问",
                retryable=False,
                fields={"secret_id": str(secret_id)},
            )
        # F-12: 解密密钥值
        from packages.common.crypto import EnvelopeCrypto

        crypto = EnvelopeCrypto.from_env()
        try:
            return crypto.decrypt(secret.value)
        except ValueError:
            # 兼容旧版明文存储（迁移期间）
            return secret.value


# ---- 辅助：规则序列化 ----


def _rule_to_dict(rule: MappingRule) -> dict:
    """将 MappingRule 序列化为 JSONB 可存字典。"""
    return {
        "source_path": rule.source_path,
        "target_variable_version_id": str(rule.target_variable_version_id),
        "source_unit": rule.source_unit,
        "missing_policy": rule.missing_policy,
        "default_value": rule.default_value,
    }


def _rule_from_dict(raw: dict) -> MappingRule:
    """从字典反序列化 MappingRule。"""
    return MappingRule(
        source_path=str(raw["source_path"]),
        target_variable_version_id=UUID(str(raw["target_variable_version_id"])),
        source_unit=raw.get("source_unit"),
        missing_policy=str(raw["missing_policy"]),
        default_value=raw.get("default_value"),
    )


def _rules_to_json(rules: list[MappingRule]) -> list[dict]:
    """规则列表序列化为 JSONB 数组。"""
    return [_rule_to_dict(r) for r in rules]


def _rules_from_json(raw: list[dict]) -> list[MappingRule]:
    """JSONB 数组反序列化为规则列表。"""
    if raw is None:
        return []
    return [_rule_from_dict(item) for item in raw]


# ---- JSON Schema 校验 ----


def _load_schema() -> dict:
    """加载映射配置 JSON Schema。"""
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _validate_profile_document(document: dict) -> None:
    """校验映射配置文档是否符合 v1 JSON Schema。

    Args:
        document: 待校验文档（含 name / source / rules）。

    Raises:
        AppError: code="validation_failed"，当校验不通过时。
    """
    import jsonschema
    from jsonschema import Draft202012Validator, FormatChecker

    schema = _load_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    try:
        validator.validate(instance=document)
    except jsonschema.ValidationError as exc:
        raise AppError(
            code="validation_failed",
            message=f"映射配置文档校验失败：{exc.message}",
            retryable=False,
            fields={"path": list(exc.absolute_path)},
        ) from exc


class MappingService:
    """映射评分服务：对源字段评分，返回按分数降序的已发布变量候选。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID（可选）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化映射评分服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID（可选）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def rank(
        self,
        source_name: str,
        source_unit: str | None,
        data_type: str,
    ) -> tuple[MappingCandidate, ...]:
        """对源字段名评分，返回按分数降序排列的候选。

        评分组件（命中加分，最终 clamp 到 0.0-1.0）：
        1. exact_code: source_name == variable.code
        2. alias_match: source_name 命中任意 VariableAlias.alias
        3. bilingual_name: source_name == variable.display_name
        4. unit_dimension: source_unit 与 canonical_unit 同维度（via UnitConverter）
        5. data_type: source data_type == variable.data_type
        6. prior_confirmed: 已发布映射配置中存在确认过的同目标规则

        仅返回 PUBLISHED 状态的变量版本作为候选；分数为 0 的候选过滤掉。

        Args:
            source_name: 源字段名。
            source_unit: 源单位代码（可选）。
            data_type: 源数据类型（number / text / boolean / datetime）。

        Returns:
            tuple[MappingCandidate, ...]: 按分数降序排列的候选。
        """
        async with self._factory() as session:
            candidates = await self._score_candidates(session, source_name, source_unit, data_type)

        candidates_sorted = sorted(candidates, key=lambda c: c.score, reverse=True)
        return tuple(candidates_sorted)

    async def _score_candidates(
        self,
        session: AsyncSession,
        source_name: str,
        source_unit: str | None,
        data_type: str,
    ) -> list[MappingCandidate]:
        """计算所有已发布变量版本的评分候选。"""
        published_versions = await self._load_published_versions(session)
        if not published_versions:
            return []

        prior_targets = await self._load_prior_confirmed_targets(session)

        aliases_by_var = await self._load_aliases(
            session, [vv.variable_id for vv in published_versions]
        )

        candidates: list[MappingCandidate] = []
        for vv in published_versions:
            reasons: list[str] = []
            score = 0.0

            if source_name == vv.code:
                score += _WEIGHT_EXACT_CODE
                reasons.append("exact_code")

            aliases = aliases_by_var.get(vv.variable_id, ())
            if any(a == source_name for a in aliases):
                score += _WEIGHT_ALIAS_MATCH
                reasons.append("alias_match")

            if source_name == vv.display_name:
                score += _WEIGHT_BILINGUAL_NAME
                reasons.append("bilingual_name")

            dim_hit = self._same_dimension(source_unit, vv.canonical_unit)
            if dim_hit:
                score += _WEIGHT_UNIT_DIMENSION
                reasons.append("unit_dimension")

            if data_type == vv.data_type:
                score += _WEIGHT_DATA_TYPE
                reasons.append("data_type")

            if vv.id in prior_targets:
                score += _WEIGHT_PRIOR_CONFIRMED
                reasons.append("prior_confirmed")

            score = min(score, 1.0)
            if score > 0.0:
                candidates.append(
                    MappingCandidate(
                        variable_code=vv.code,
                        variable_version_id=vv.id,
                        score=score,
                        reasons=tuple(reasons),
                    )
                )
        return candidates

    async def _load_published_versions(self, session: AsyncSession) -> list[VariableVersion]:
        """加载当前组织内所有已发布的变量版本。"""
        result = await session.execute(
            sa.select(VariableVersion)
            .join(Variable, Variable.id == VariableVersion.variable_id)
            .where(
                Variable.organization_id == self._org_id,
                VariableVersion.status == StandardStatus.PUBLISHED,
            )
        )
        return list(result.scalars().all())

    async def _load_aliases(
        self, session: AsyncSession, variable_ids: list[UUID]
    ) -> dict[UUID, list[str]]:
        """批量加载变量的别名，返回 variable_id → [alias, ...] 映射。"""
        if not variable_ids:
            return {}
        result = await session.execute(
            sa.select(VariableAlias).where(VariableAlias.variable_id.in_(variable_ids))
        )
        mapping: dict[UUID, list[str]] = {}
        for alias in result.scalars().all():
            mapping.setdefault(alias.variable_id, []).append(alias.alias)
        return mapping

    async def _load_prior_confirmed_targets(self, session: AsyncSession) -> set[UUID]:
        """加载已发布映射配置中确认过的目标变量版本 ID 集合。"""
        result = await session.execute(
            sa.select(MappingProfileVersion)
            .join(
                MappingProfile,
                MappingProfile.id == MappingProfileVersion.profile_id,
            )
            .where(
                MappingProfile.organization_id == self._org_id,
                MappingProfileVersion.status == StandardStatus.PUBLISHED,
            )
        )
        targets: set[UUID] = set()
        for mpv in result.scalars().all():
            rules_raw = mpv.rules or []
            if not isinstance(rules_raw, list):
                continue
            for rule in rules_raw:
                if not isinstance(rule, dict):
                    continue
                target = rule.get("target_variable_version_id")
                if target:
                    try:
                        targets.add(UUID(str(target)))
                    except (ValueError, TypeError):
                        continue
        return targets

    @staticmethod
    def _same_dimension(source_unit: str | None, target_unit: str | None) -> bool:
        """判断源单位与目标单位是否同维度（via UnitConverter 注册表）。

        任一单位未知或为 None 时返回 False（不加分、不扣分）。
        """
        if not source_unit or not target_unit:
            return False
        return UnitConverter.is_compatible(source_unit, target_unit)


class MappingProfileService:
    """映射配置生命周期服务：创建/提交/发布/拒绝/查询。

    已发布配置的规则不可变（published_version_immutable）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _actor_id: 当前操作人 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        """初始化映射配置服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            actor_id: 当前操作人 ID。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._actor_id = actor_id

    async def create_profile(
        self,
        name: str,
        source: dict,
        rules: list[MappingRule],
    ) -> dict:
        """创建草稿映射配置（含首个草稿版本）。

        Args:
            name: 配置名称（组织内唯一）。
            source: 数据源描述（含 kind + kind 特定配置）。
            rules: 映射规则列表。

        Returns:
            dict: 新创建的配置详情。

        Raises:
            AppError: code="conflict"，当名称已存在时。
            AppError: code="validation_failed"，当文档不符合 JSON Schema 时。
        """
        document = {
            "name": name,
            "source": source,
            "rules": [_rule_to_dict(r) for r in rules],
        }
        await asyncio.to_thread(_validate_profile_document, document)

        async with session_scope(self._factory) as session:
            existing = await session.execute(
                sa.select(MappingProfile).where(
                    MappingProfile.organization_id == self._org_id,
                    MappingProfile.name == name,
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise AppError(
                    code="conflict",
                    message="映射配置名称已存在",
                    retryable=False,
                    fields={"name": name},
                )

            profile = MappingProfile(
                id=new_id(),
                organization_id=self._org_id,
                name=name,
                source_kind=str(source.get("kind")),
                source_config=source,
                status=StandardStatus.DRAFT,
                lock_version=0,
                created_by=self._actor_id,
            )
            session.add(profile)
            await session.flush()

            version = MappingProfileVersion(
                id=new_id(),
                profile_id=profile.id,
                version=1,
                rules=_rules_to_json(rules),
                status=StandardStatus.DRAFT,
                lock_version=0,
            )
            session.add(version)
            await session.flush()

            return await self._build_detail(profile, version)

    async def update_rules(
        self,
        profile_id: UUID,
        rules: list[MappingRule],
    ) -> dict:
        """更新映射配置规则（仅草稿/拒绝状态可编辑）。

        Args:
            profile_id: 配置 UUID。
            rules: 新规则列表。

        Returns:
            dict: 更新后的配置详情。

        Raises:
            AppError: code="not_found"，当配置不存在时。
            AppError: code="published_version_immutable"，当配置已发布/弃用时。
            AppError: code="validation_failed"，当文档不符合 JSON Schema 时。
        """
        async with session_scope(self._factory) as session:
            profile = await self._get_profile(session, profile_id)
            if profile.status in (
                StandardStatus.PUBLISHED,
                StandardStatus.DEPRECATED,
                StandardStatus.IN_REVIEW,
            ):
                raise AppError(
                    code="published_version_immutable",
                    message="已发布/审核中/弃用的配置规则不可修改",
                    retryable=False,
                    fields={"status": profile.status},
                )

            latest = await self._get_latest_version(session, profile_id)
            document = {
                "name": profile.name,
                "source": profile.source_config,
                "rules": [_rule_to_dict(r) for r in rules],
            }
            await asyncio.to_thread(_validate_profile_document, document)

            await session.execute(
                sa.update(MappingProfileVersion)
                .values(
                    rules=_rules_to_json(rules),
                    lock_version=MappingProfileVersion.lock_version + 1,
                )
                .where(MappingProfileVersion.id == latest.id)
            )
            await session.flush()

            refreshed = await self._get_latest_version(session, profile_id)
            return await self._build_detail(profile, refreshed)

    async def submit_profile(self, profile_id: UUID) -> dict:
        """提交审核（DRAFT → IN_REVIEW）。

        Args:
            profile_id: 配置 UUID。

        Returns:
            dict: 更新后的配置详情。

        Raises:
            AppError: code="not_found"，当配置不存在时。
            AppError: code="invalid_transition"，当状态非 draft 时。
        """
        async with session_scope(self._factory) as session:
            profile = await self._get_profile(session, profile_id)
            assert_transition(profile.status, StandardStatus.IN_REVIEW)

            latest = await self._get_latest_version(session, profile_id)
            await session.execute(
                sa.update(MappingProfileVersion)
                .values(
                    status=StandardStatus.IN_REVIEW,
                    lock_version=MappingProfileVersion.lock_version + 1,
                )
                .where(MappingProfileVersion.id == latest.id)
            )
            await self._bump_profile_status(
                session, profile_id, profile.lock_version, StandardStatus.IN_REVIEW
            )

            profile = await self._get_profile(session, profile_id)
            refreshed = await self._get_latest_version(session, profile_id)
            return await self._build_detail(profile, refreshed)

    async def publish_profile(self, profile_id: UUID) -> dict:
        """发布配置（IN_REVIEW → PUBLISHED，规则此后不可变）。

        Args:
            profile_id: 配置 UUID。

        Returns:
            dict: 已发布的配置详情。

        Raises:
            AppError: code="not_found"，当配置不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
        """
        async with session_scope(self._factory) as session:
            profile = await self._get_profile(session, profile_id)
            assert_transition(profile.status, StandardStatus.PUBLISHED)

            latest = await self._get_latest_version(session, profile_id)
            await session.execute(
                sa.update(MappingProfileVersion)
                .values(
                    status=StandardStatus.PUBLISHED,
                    published_at=sa.func.now(),
                    lock_version=MappingProfileVersion.lock_version + 1,
                )
                .where(MappingProfileVersion.id == latest.id)
            )
            await self._bump_profile_status(
                session, profile_id, profile.lock_version, StandardStatus.PUBLISHED
            )

            profile = await self._get_profile(session, profile_id)
            refreshed = await self._get_latest_version(session, profile_id)
            return await self._build_detail(profile, refreshed)

    async def reject_profile(self, profile_id: UUID) -> dict:
        """拒绝配置（IN_REVIEW → REJECTED）。

        Args:
            profile_id: 配置 UUID。

        Returns:
            dict: 已拒绝的配置详情。

        Raises:
            AppError: code="not_found"，当配置不存在时。
            AppError: code="invalid_transition"，当状态非 in_review 时。
        """
        async with session_scope(self._factory) as session:
            profile = await self._get_profile(session, profile_id)
            assert_transition(profile.status, StandardStatus.REJECTED)

            latest = await self._get_latest_version(session, profile_id)
            await session.execute(
                sa.update(MappingProfileVersion)
                .values(
                    status=StandardStatus.REJECTED,
                    lock_version=MappingProfileVersion.lock_version + 1,
                )
                .where(MappingProfileVersion.id == latest.id)
            )
            await self._bump_profile_status(
                session, profile_id, profile.lock_version, StandardStatus.REJECTED
            )

            profile = await self._get_profile(session, profile_id)
            refreshed = await self._get_latest_version(session, profile_id)
            return await self._build_detail(profile, refreshed)

    async def get_profile(self, profile_id: UUID) -> dict:
        """查询单个映射配置详情。

        Args:
            profile_id: 配置 UUID。

        Returns:
            dict: 配置详情（含规则）。

        Raises:
            AppError: code="not_found"，当配置不存在时。
        """
        async with self._factory() as session:
            profile = await self._get_profile(session, profile_id)
            latest = await self._get_latest_version(session, profile_id)
            return await self._build_detail(profile, latest)

    async def list_profiles(
        self,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[dict], str | None]:
        """分页查询映射配置列表。

        Keyset 分页：按 (created_at, id) 排序，游标编码上一页最后一条的
        (created_at_iso, id)，避免跨页跳过/重复。

        Args:
            cursor: 分页游标（base64url JSON，None 表示第一页）。
            page_size: 每页数量。

        Returns:
            tuple[list[dict], str | None]: (配置列表, 下一页游标)。

        Raises:
            AppError: code="invalid_cursor"，当游标格式不合法时。
        """
        from packages.common.pagination import MAX_PAGE_SIZE

        effective_size = min(max(page_size, 1), MAX_PAGE_SIZE)

        async with self._factory() as session:
            query = (
                sa.select(MappingProfile)
                .where(MappingProfile.organization_id == self._org_id)
                .order_by(MappingProfile.created_at.asc(), MappingProfile.id.asc())
                .limit(effective_size + 1)
            )
            if cursor is not None:
                cursor_created_at, cursor_id = _decode_list_cursor(cursor)
                query = query.where(
                    sa.or_(
                        MappingProfile.created_at > cursor_created_at,
                        sa.and_(
                            MappingProfile.created_at == cursor_created_at,
                            MappingProfile.id > cursor_id,
                        ),
                    )
                )
            result = await session.execute(query)
            profiles = list(result.scalars().all())

            items: list[dict] = []
            for profile in profiles[:effective_size]:
                latest = await self._get_latest_version(session, profile.id)
                items.append(await self._build_detail(profile, latest))

        has_more = len(profiles) > effective_size
        next_cursor: str | None = None
        if has_more and len(items) > 0:
            last = profiles[effective_size - 1]
            next_cursor = _encode_list_cursor(last.created_at, last.id)
        return items, next_cursor

    # ---- 内部辅助 ----

    async def _get_profile(self, session: AsyncSession, profile_id: UUID) -> MappingProfile:
        """读取配置并校验组织归属。"""
        result = await session.execute(
            sa.select(MappingProfile).where(MappingProfile.id == profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None or profile.organization_id != self._org_id:
            raise AppError(
                code="not_found",
                message="映射配置不存在",
                retryable=False,
                fields={"profile_id": str(profile_id)},
            )
        return profile

    async def _get_latest_version(
        self, session: AsyncSession, profile_id: UUID
    ) -> MappingProfileVersion:
        """读取最新版本。"""
        result = await session.execute(
            sa.select(MappingProfileVersion)
            .where(MappingProfileVersion.profile_id == profile_id)
            .order_by(MappingProfileVersion.version.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise AppError(
                code="not_found",
                message="映射配置无版本记录",
                retryable=False,
                fields={"profile_id": str(profile_id)},
            )
        return version

    @staticmethod
    async def _bump_profile_status(
        session: AsyncSession,
        profile_id: UUID,
        lock_version: int,
        new_status: str,
    ) -> None:
        """更新配置状态（乐观锁）。"""
        await session.execute(
            sa.update(MappingProfile)
            .values(
                status=new_status,
                updated_at=sa.func.now(),
                lock_version=MappingProfile.lock_version + 1,
            )
            .where(
                MappingProfile.id == profile_id,
                MappingProfile.lock_version == lock_version,
            )
        )

    @staticmethod
    async def _build_detail(
        profile: MappingProfile,
        version: MappingProfileVersion,
    ) -> dict:
        """构造配置详情字典。"""
        return {
            "id": str(profile.id),
            "organization_id": str(profile.organization_id),
            "name": profile.name,
            "source_kind": profile.source_kind,
            "source_config": profile.source_config,
            "status": profile.status,
            "lock_version": profile.lock_version,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            "created_by": str(profile.created_by) if profile.created_by else None,
            "version": {
                "id": str(version.id),
                "profile_id": str(version.profile_id),
                "version": version.version,
                "rules": _rules_from_json(version.rules or []),
                "status": version.status,
                "published_at": version.published_at,
                "lock_version": version.lock_version,
                "created_at": version.created_at,
            },
        }


class IngestionService:
    """数据源预览服务：按 kind 构造连接器并预览。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
    ) -> None:
        """初始化预览服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
        """
        self._factory = session_factory
        self._org_id = organization_id

    async def preview(self, source: ConnectorSource, limit: int = 100) -> PreviewTable:
        """预览数据源。

        Args:
            source: 数据源描述。
            limit: 预览行数上限。

        Returns:
            PreviewTable: 预览结果。
        """
        # 延迟导入以避免与 packages.connectors.__init__ 的循环依赖。
        from packages.connectors import build_connector

        secret_store = SecretStore(self._factory, self._org_id)
        connector = build_connector(source, secret_store=secret_store)
        return await connector.preview(source, limit=limit)


# ---- 游标编解码（keyset 分页）----


def _encode_list_cursor(created_at: datetime, profile_id: UUID) -> str:
    """编码 keyset 分页游标。

    格式：base64url( JSON {"v": created_at_iso, "id": uuid_str} )
    """
    payload = json.dumps(
        {"v": created_at.isoformat(), "id": str(profile_id)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    import base64

    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_list_cursor(cursor: str) -> tuple[datetime, UUID]:
    """解码 keyset 分页游标。

    Returns:
        tuple[datetime, UUID]: (created_at, profile_id)。

    Raises:
        AppError: code="invalid_cursor"，当游标格式不合法时。
    """
    import base64
    import binascii

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：base64url 解码失败",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：JSON 解析失败",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    if not isinstance(payload, dict) or "v" not in payload or "id" not in payload:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：缺少必要字段 v / id",
            retryable=False,
            fields={"cursor": cursor},
        )

    try:
        created_at = datetime.fromisoformat(str(payload["v"]))
    except (ValueError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：v 字段不是合法 ISO 时间",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    try:
        profile_id = UUID(str(payload["id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError(
            code="invalid_cursor",
            message="分页游标无效：id 字段不是合法 UUID",
            retryable=False,
            fields={"cursor": cursor},
        ) from exc

    return created_at, profile_id
