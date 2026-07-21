"""认证数据仓库：用户与刷新会话的数据库操作。

所有方法均接受 AsyncSession 参数，由调用方（AuthService）管理事务边界。
刷新会话查询使用 SELECT ... FOR UPDATE 防止并发旋转竞态
（docs/arch-v0.md §8.2 风险 "refresh token 重放检测竞态"）。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.entities import AppUser, RefreshSession


class AuthRepository:
    """认证持久化仓库。

    提供用户查询、刷新会话创建/查询/旋转/撤销等操作。
    所有方法为纯数据访问，不含业务逻辑——业务编排由 AuthService 负责。
    """

    @staticmethod
    async def find_user_by_email(
        session: AsyncSession,
        email: str,
    ) -> AppUser | None:
        """按邮箱查找用户（CITEXT 大小写不敏感）。

        Args:
            session: 数据库异步会话。
            email: 用户邮箱。

        Returns:
            AppUser | None: 找到返回用户实体，否则返回 None。
        """
        result = await session.execute(
            sa.select(AppUser).where(AppUser.email == email)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_user_by_id(
        session: AsyncSession,
        user_id: UUID,
    ) -> AppUser | None:
        """按 ID 查找用户。

        Args:
            session: 数据库异步会话。
            user_id: 用户 UUID。

        Returns:
            AppUser | None: 找到返回用户实体，否则返回 None。
        """
        result = await session.execute(
            sa.select(AppUser).where(AppUser.id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_refresh_session(
        session: AsyncSession,
        session_id: UUID,
        family_id: UUID,
        user_id: UUID,
        token_digest: str,
        issued_at: datetime,
        expires_at: datetime,
        created_ip: str | None = None,
        user_agent: str | None = None,
    ) -> RefreshSession:
        """创建一条刷新会话记录。

        Args:
            session: 数据库异步会话。
            session_id: 会话 UUID（由调用方通过 new_id() 生成）。
            family_id: 家族 UUID。
            user_id: 用户 UUID。
            token_digest: refresh token 的 SHA-256 摘要。
            issued_at: 签发时间。
            expires_at: 过期时间。
            created_ip: 客户端 IP（审计辅助）。
            user_agent: User-Agent（审计辅助）。

        Returns:
            RefreshSession: 已创建的会话实体。
        """
        refresh_session = RefreshSession(
            id=session_id,
            family_id=family_id,
            user_id=user_id,
            token_digest=token_digest,
            issued_at=issued_at,
            expires_at=expires_at,
            created_ip=created_ip,
            user_agent=user_agent,
        )
        session.add(refresh_session)
        await session.flush()
        return refresh_session

    @staticmethod
    async def find_session_by_digest(
        session: AsyncSession,
        digest: str,
    ) -> RefreshSession | None:
        """按摘要查找刷新会话（不加锁）。

        用于 logout 等不需要并发控制的场景。

        Args:
            session: 数据库异步会话。
            digest: refresh token 的 SHA-256 摘要。

        Returns:
            RefreshSession | None: 找到返回会话实体，否则返回 None。
        """
        result = await session.execute(
            sa.select(RefreshSession).where(
                RefreshSession.token_digest == digest
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_session_by_digest_for_update(
        session: AsyncSession,
        digest: str,
    ) -> RefreshSession | None:
        """按摘要查找刷新会话并加行锁（SELECT ... FOR UPDATE）。

        用于 refresh 旋转流程，防止并发刷新同一 token 的竞态。
        锁在当前事务提交/回滚后自动释放。

        Args:
            session: 数据库异步会话（必须在事务内）。
            digest: refresh token 的 SHA-256 摘要。

        Returns:
            RefreshSession | None: 找到返回会话实体（已锁定），否则返回 None。
        """
        result = await session.execute(
            sa.select(RefreshSession)
            .where(RefreshSession.token_digest == digest)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def rotate_session(
        session: AsyncSession,
        old_id: UUID,
        new_id: UUID,
        now: datetime,
    ) -> None:
        """旋转会话：旧行设置 replaced_by 和 revoked_at。

        Args:
            session: 数据库异步会话。
            old_id: 旧会话 UUID。
            new_id: 新会话 UUID（替换旧会话）。
            now: 当前时刻（撤销时间）。
        """
        await session.execute(
            sa.update(RefreshSession)
            .where(RefreshSession.id == old_id)
            .values(replaced_by=new_id, revoked_at=now)
        )

    @staticmethod
    async def revoke_family(
        session: AsyncSession,
        family_id: UUID,
        now: datetime,
    ) -> None:
        """整族撤销：将同 family_id 且未撤销的会话全部标记为已撤销。

        用于检测到重放攻击时，撤销该家族的所有剩余会话。

        Args:
            session: 数据库异步会话。
            family_id: 家族 UUID。
            now: 当前时刻（撤销时间）。
        """
        await session.execute(
            sa.update(RefreshSession)
            .where(RefreshSession.family_id == family_id)
            .where(RefreshSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    @staticmethod
    async def revoke_session(
        session: AsyncSession,
        session_id: UUID,
        now: datetime,
    ) -> None:
        """撤销单个会话（用于 logout）。

        幂等：若已撤销则不覆盖原 revoked_at。

        Args:
            session: 数据库异步会话。
            session_id: 会话 UUID。
            now: 当前时刻（撤销时间）。
        """
        await session.execute(
            sa.update(RefreshSession)
            .where(RefreshSession.id == session_id)
            .where(RefreshSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    @staticmethod
    async def count_by_email(
        session: AsyncSession,
        email: str,
    ) -> int:
        """按邮箱统计用户数（用于 bootstrap 幂等性验证）。

        Args:
            session: 数据库异步会话。
            email: 用户邮箱。

        Returns:
            int: 匹配邮箱的用户数量。
        """
        result = await session.execute(
            sa.select(sa.func.count(AppUser.id)).where(AppUser.email == email)
        )
        count: int = result.scalar() or 0
        return count
