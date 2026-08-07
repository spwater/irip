"""批量创建部门 + 用户 + 多部门关联。

用法:
  cd irip && set -a && source .env && set +a && \
  IRIP_DATABASE_URL="postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip" \
  .venv/bin/python scripts/seed_users.py
"""
import asyncio
import os
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---- 配置 ----

DEPARTMENTS = [
    "研发中心",
    "机理仿真实验室",
    "粉磨技术实验室",
    "先进控制实验室",
    "固废资源化实验室",
    "无机非金属材料实验室",
    "工业烟气污染控制实验室",
    "热化学工程实验室",
    "科技管理部",
    "复合耐磨材料实验室",
]

# (email, display_name, password, role, [dept_names])
# primary = dept_names[0], 其余为 secondary
# 密码从环境变量 IRIP_SEED_PASSWORD 读取（默认 asdf1234 仅供开发）
_SEED_PASSWORD = os.getenv("IRIP_SEED_PASSWORD", "asdf1234")
USERS = [
    ("shuipei@hcrdi.com", "水沛", _SEED_PASSWORD, "platform_administrator", ["研发中心", "机理仿真实验室"]),
    ("13955184486@163.com", "高霖", _SEED_PASSWORD, "lab_director", ["粉磨技术实验室"]),
    ("chub@hcrdi.com", "褚彪", _SEED_PASSWORD, "platform_administrator", ["研发中心", "先进控制实验室"]),
    ("lining@hcrdi.com", "李宁", _SEED_PASSWORD, "lab_director", ["固废资源化实验室"]),
    ("sh@hcrdi.com", "宋昊", _SEED_PASSWORD, "lab_director", ["无机非金属材料实验室"]),
    ("18654180525@irip.com", "王梦瑜", _SEED_PASSWORD, "lab_director", ["工业烟气污染控制实验室"]),
    ("lyj@hcrdi.com", "刘银杰", _SEED_PASSWORD, "lab_director", ["热化学工程实验室"]),
    ("lzq@hcrdi.com", "刘志强", _SEED_PASSWORD, "lab_member", ["热化学工程实验室"]),
    ("15755537388@irip.com", "陈宝新", _SEED_PASSWORD, "lab_member", ["热化学工程实验室"]),
    ("18225512770@irio.com", "苏明雪", _SEED_PASSWORD, "lab_member", ["固废资源化实验室"]),
    ("253218588@qq.com", "丁浩", _SEED_PASSWORD, "lab_member", ["粉磨技术实验室"]),
    ("1401666768@qq.com", "王广", _SEED_PASSWORD, "lab_member", ["机理仿真实验室"]),
    ("fanwei971129@163.com", "范威", _SEED_PASSWORD, "lab_member", ["机理仿真实验室"]),
    ("ytf@hcrdi.com", "殷腾飞", _SEED_PASSWORD, "lab_member", ["机理仿真实验室"]),
    ("15927211562@irip.com", "袁鹏", _SEED_PASSWORD, "lab_member", ["机理仿真实验室"]),
    ("hg@hcrdi.com", "胡光", _SEED_PASSWORD, "platform_administrator", ["科技管理部"]),
    ("liutao@cbmi.com.cn", "刘韬", _SEED_PASSWORD, "platform_administrator", ["研发中心", "先进控制实验室"]),
    ("13855189911@irip.com", "王虔虔", _SEED_PASSWORD, "platform_auditor", ["合肥水泥研究设计院有限公司"]),
    ("zza@cbmi.com.cn", "朱子昂", _SEED_PASSWORD, "lab_director", ["复合耐磨材料实验室"]),
]


async def main():
    from packages.auth.passwords import hash_password
    from packages.common.ids import new_id

    if os.getenv("IRIP_SEED_PASSWORD") is None:
        print("⚠️  WARNING: IRIP_SEED_PASSWORD 未设置，使用默认密码 asdf1234。")
        print("   生产环境请通过环境变量指定安全密码。\n")

    db_url = os.getenv("IRIP_DATABASE_URL", "").replace(
        "postgresql+psycopg://", "postgresql+psycopg_async://", 1
    )
    if not db_url:
        print("ERROR: IRIP_DATABASE_URL not set")
        return

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session: AsyncSession = session

        # 1. 查 root dept
        root_result = await session.execute(
            sa.text("SELECT id FROM department WHERE code = 'root'")
        )
        root_id = root_result.scalar()
        if not root_id:
            print("ERROR: root 哨兵部门不存在，请先跑 bootstrap")
            return
        print(f"Root dept: {root_id}")

        # 2. 创建部门（挂在 root 下）
        dept_map: dict[str, UUID] = {}
        for name in DEPARTMENTS:
            # 先查是否已存在
            existing = await session.execute(
                sa.text("SELECT id FROM department WHERE display_name = :name AND parent_id = :root_id"),
                {"name": name, "root_id": str(root_id)},
            )
            existing_id = existing.scalar()
            if existing_id:
                dept_map[name] = existing_id
                print(f"  部门已存在: {name} ({existing_id})")
            else:
                dept_id = new_id()
                await session.execute(
                    sa.text(
                        "INSERT INTO department (id, code, display_name, description, status, sort_order, created_at, updated_at, lock_version, parent_id) "
                        "VALUES (:id, :code, :name, NULL, 'active', 0, now(), now(), 0, :parent_id)"
                    ),
                    {"id": str(dept_id), "code": f"dept_{dept_id.hex[:8]}", "name": name, "parent_id": str(root_id)},
                )
                dept_map[name] = dept_id
                print(f"  创建部门: {name} ({dept_id})")

        # root 也加进去（王虔虔归属 root）
        dept_map["合肥水泥研究设计院有限公司"] = root_id

        await session.commit()
        print(f"\n共 {len(dept_map)} 个部门就绪")

        # 3. 创建用户
        created = 0
        skipped = 0
        for email, name, password, role, dept_names in USERS:
            # 查是否已存在
            existing = await session.execute(
                sa.text("SELECT id FROM app_user WHERE email = :email"),
                {"email": email},
            )
            user_id = existing.scalar()
            if user_id:
                print(f"  用户已存在，跳过: {email}")
                skipped += 1
                continue

            # 解析部门
            primary_dept_name = dept_names[0]
            primary_dept_id = dept_map.get(primary_dept_name)
            if not primary_dept_id:
                print(f"  WARNING: 部门不存在 '{primary_dept_name}'，跳过用户 {email}")
                continue

            # 创建用户
            user_id = new_id()
            pwd_hash = hash_password(password)
            await session.execute(
                sa.text(
                    "INSERT INTO app_user (id, email, display_name, password_hash, status, roles, department_id, token_version, lock_version, created_at, updated_at) "
                    "VALUES (:id, :email, :name, :pwd, 'active', CAST(:roles AS jsonb), :dept_id, 0, 0, now(), now())"
                ),
                {
                    "id": str(user_id),
                    "email": email,
                    "name": name,
                    "pwd": pwd_hash,
                    "roles": f'["{role}"]',
                    "dept_id": str(primary_dept_id),
                },
            )

            # 创建 app_user_department 关联（primary）
            await session.execute(
                sa.text(
                    "INSERT INTO app_user_department (user_id, department_id, is_primary, created_at) "
                    "VALUES (:uid, :did, true, now()) ON CONFLICT DO NOTHING"
                ),
                {"uid": str(user_id), "did": str(primary_dept_id)},
            )

            # 创建 secondary 部门关联
            for sec_dept_name in dept_names[1:]:
                sec_dept_id = dept_map.get(sec_dept_name)
                if sec_dept_id:
                    await session.execute(
                        sa.text(
                            "INSERT INTO app_user_department (user_id, department_id, is_primary, created_at) "
                            "VALUES (:uid, :did, false, now()) ON CONFLICT DO NOTHING"
                        ),
                        {"uid": str(user_id), "did": str(sec_dept_id)},
                    )

            created += 1
            extra = f" + {', '.join(dept_names[1:])}" if len(dept_names) > 1 else ""
            print(f"  创建用户: {name} ({email}) → {role} @ {primary_dept_name}{extra}")

        await session.commit()

    print(f"\n=== 完成 ===")
    print(f"新建用户: {created}")
    print(f"跳过(已存在): {skipped}")
    print(f"总用户: {created + skipped}")


if __name__ == "__main__":
    asyncio.run(main())
