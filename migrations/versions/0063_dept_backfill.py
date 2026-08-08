"""0063: 多租户隔离键升级 — 阶段1回填 department_id

回填 0062 新增的 department_id 列（及 A 类表的 visible_departments /
visibility_scope / owner_user_id）。

回填依据：
- fact/parameter/evidence_set/model/transformation_recipe:
  created_by 用户的 primary department；无 created_by → root
- artifact: 关联 job/对象的归属；孤立 → root
- component: root（内置组件全组织共享）
- flow_definition: root 或创建者部门
- industrial_object: 已有 department_id（nullable → 回填 root）
- equipment: 已有 department_id，无需回填
- job: 提交者部门快照
- flow_run: 关联 flow_definition 的 department_id
- derivation_run: 执行者部门
- audit_event: actor 部门；系统事件 → system
- secret/backup_record: system
- app_user: 已有 department_id 沿用

回填后输出逐表审计报告（RAISE NOTICE）。

Revision ID: 0063
Revises: 0062
Create Date: 2026-08-21
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """回填 department_id + A 类其余列 + 审计报告。"""
    op.execute(
        """
        DO $$
        DECLARE
            v_root_id UUID;
            v_system_id UUID;
            v_total_before BIGINT;
            v_total_after BIGINT;
            v_null_count BIGINT;
        BEGIN
            -- 获取哨兵部门 ID
            SELECT id INTO v_root_id FROM department WHERE code = 'root' AND parent_id IS NULL LIMIT 1;
            SELECT id INTO v_system_id FROM department WHERE code = 'system' LIMIT 1;

            IF v_root_id IS NULL THEN
                RAISE EXCEPTION 'root 哨兵部门不存在，请先执行 0062 迁移';
            END IF;

            -- ================================================================
            -- A 类表回填（department_id + visible_departments + visibility_scope + owner_user_id）
            -- ================================================================

            -- fact: created_by 用户的 primary dept，无则 root
            UPDATE fact SET
                department_id = COALESCE(
                    (SELECT aud.department_id FROM app_user_department aud
                     WHERE aud.user_id = fact.created_by AND aud.is_primary = true),
                    v_root_id
                ),
                visible_departments = COALESCE(fact.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(fact.visibility_scope, 'tree'),
                owner_user_id = COALESCE(fact.created_by, (SELECT id FROM app_user LIMIT 1))
            WHERE fact.department_id IS NULL;

            -- parameter: created_by 用户的 primary dept，无则 root
            UPDATE parameter SET
                department_id = COALESCE(
                    (SELECT aud.department_id FROM app_user_department aud
                     WHERE aud.user_id = parameter.created_by AND aud.is_primary = true),
                    v_root_id
                ),
                visible_departments = COALESCE(parameter.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(parameter.visibility_scope, 'tree'),
                owner_user_id = COALESCE(parameter.created_by, (SELECT id FROM app_user LIMIT 1))
            WHERE parameter.department_id IS NULL;

            -- evidence_set: created_by 用户的 primary dept，无则 root
            UPDATE evidence_set SET
                department_id = COALESCE(
                    (SELECT aud.department_id FROM app_user_department aud
                     WHERE aud.user_id = evidence_set.created_by AND aud.is_primary = true),
                    v_root_id
                ),
                visible_departments = COALESCE(evidence_set.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(evidence_set.visibility_scope, 'tree'),
                owner_user_id = COALESCE(evidence_set.created_by, (SELECT id FROM app_user LIMIT 1))
            WHERE evidence_set.department_id IS NULL;

            -- artifact: 关联 uploaded_by 用户部门，无则 root
            UPDATE artifact SET
                department_id = COALESCE(
                    (SELECT aud.department_id FROM app_user_department aud
                     WHERE aud.user_id = artifact.uploaded_by AND aud.is_primary = true),
                    v_root_id
                ),
                visible_departments = COALESCE(artifact.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(artifact.visibility_scope, 'tree'),
                owner_user_id = COALESCE(artifact.uploaded_by, (SELECT id FROM app_user LIMIT 1))
            WHERE artifact.department_id IS NULL;

            -- model: 无 created_by → root
            UPDATE model SET
                department_id = v_root_id,
                visible_departments = COALESCE(model.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(model.visibility_scope, 'tree'),
                owner_user_id = COALESCE((SELECT id FROM app_user LIMIT 1))
            WHERE model.department_id IS NULL;

            -- transformation_recipe: 无 created_by → root
            UPDATE transformation_recipe SET
                department_id = v_root_id,
                visible_departments = COALESCE(transformation_recipe.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(transformation_recipe.visibility_scope, 'tree'),
                owner_user_id = COALESCE((SELECT id FROM app_user LIMIT 1))
            WHERE transformation_recipe.department_id IS NULL;

            -- component: root（内置组件全组织共享）
            UPDATE component SET
                department_id = v_root_id,
                visible_departments = COALESCE(component.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(component.visibility_scope, 'tree'),
                owner_user_id = COALESCE((SELECT id FROM app_user LIMIT 1))
            WHERE component.department_id IS NULL;

            -- flow_definition: 已有 department_id（nullable）→ 回填 root
            UPDATE flow_definition SET
                department_id = v_root_id,
                visible_departments = COALESCE(flow_definition.visible_departments, '[]'::jsonb),
                visibility_scope = COALESCE(flow_definition.visibility_scope, 'tree'),
                owner_user_id = COALESCE((SELECT id FROM app_user LIMIT 1))
            WHERE flow_definition.department_id IS NULL;

            -- industrial_object: 已有 department_id（nullable）→ 回填 root
            UPDATE industrial_object SET
                visibility_scope = COALESCE(industrial_object.visibility_scope, 'tree'),
                owner_user_id = COALESCE((SELECT id FROM app_user LIMIT 1))
            WHERE industrial_object.visibility_scope IS NULL;

            UPDATE industrial_object SET
                department_id = v_root_id
            WHERE industrial_object.department_id IS NULL;

            -- equipment: 已有 department_id（NOT NULL）→ 仅回填 visibility_scope + owner_user_id
            UPDATE equipment SET
                visibility_scope = COALESCE(equipment.visibility_scope, 'tree'),
                owner_user_id = COALESCE(
                    (SELECT au.id FROM app_user au
                     JOIN app_user_department ad ON au.id = ad.user_id
                     WHERE ad.department_id = equipment.department_id AND ad.is_primary = true
                     LIMIT 1),
                    (SELECT id FROM app_user LIMIT 1)
                )
            WHERE equipment.visibility_scope IS NULL;

            -- ================================================================
            -- B 类表回填（仅 department_id）
            -- ================================================================

            -- job: 提交者部门快照
            UPDATE job SET
                department_id = COALESCE(
                    (SELECT aud.department_id FROM app_user_department aud
                     WHERE aud.user_id = job.created_by AND aud.is_primary = true),
                    v_root_id
                )
            WHERE job.department_id IS NULL;

            -- flow_run: 关联 flow_definition 的 department_id
            UPDATE flow_run SET
                department_id = COALESCE(
                    (SELECT fd.department_id FROM flow_definition fd
                     JOIN flow_definition_version fv ON fv.flow_definition_id = fd.id
                     WHERE fv.id = flow_run.flow_version_id),
                    v_root_id
                )
            WHERE flow_run.department_id IS NULL;

            -- derivation_run: 无执行者字段 → root
            UPDATE derivation_run SET
                department_id = v_root_id
            WHERE derivation_run.department_id IS NULL;

            -- audit_event: actor 部门；系统事件 → system
            UPDATE audit_event SET
                department_id = COALESCE(
                    (SELECT aud.department_id FROM app_user_department aud
                     WHERE aud.user_id = audit_event.actor_user_id AND aud.is_primary = true),
                    v_system_id
                )
            WHERE audit_event.department_id IS NULL;

            -- secret: system
            UPDATE secret SET
                department_id = v_system_id
            WHERE secret.department_id IS NULL;

            -- backup_record: system
            UPDATE backup_record SET
                department_id = v_system_id
            WHERE backup_record.department_id IS NULL;

            -- app_user: 已有 department_id 沿用，无则 root
            UPDATE app_user SET
                department_id = v_root_id
            WHERE app_user.department_id IS NULL;

            -- scope_grant: 表可能已删除（0055-0059 清理批次），安全跳过
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'scope_grant') THEN
                UPDATE scope_grant SET
                    department_id = v_root_id
                WHERE scope_grant.department_id IS NULL;
            END IF;

            -- ================================================================
            -- 审计报告
            -- ================================================================

            RAISE NOTICE '====== 0063 回填审计报告 ======';

            -- fact
            SELECT count(*) INTO v_total_before FROM fact;
            SELECT count(*) INTO v_null_count FROM fact WHERE department_id IS NULL;
            RAISE NOTICE 'fact: total=%, null_dept=%', v_total_before, v_null_count;

            -- parameter
            SELECT count(*) INTO v_total_before FROM parameter;
            SELECT count(*) INTO v_null_count FROM parameter WHERE department_id IS NULL;
            RAISE NOTICE 'parameter: total=%, null_dept=%', v_total_before, v_null_count;

            -- evidence_set
            SELECT count(*) INTO v_total_before FROM evidence_set;
            SELECT count(*) INTO v_null_count FROM evidence_set WHERE department_id IS NULL;
            RAISE NOTICE 'evidence_set: total=%, null_dept=%', v_total_before, v_null_count;

            -- artifact
            SELECT count(*) INTO v_total_before FROM artifact;
            SELECT count(*) INTO v_null_count FROM artifact WHERE department_id IS NULL;
            RAISE NOTICE 'artifact: total=%, null_dept=%', v_total_before, v_null_count;

            -- model
            SELECT count(*) INTO v_total_before FROM model;
            SELECT count(*) INTO v_null_count FROM model WHERE department_id IS NULL;
            RAISE NOTICE 'model: total=%, null_dept=%', v_total_before, v_null_count;

            -- transformation_recipe
            SELECT count(*) INTO v_total_before FROM transformation_recipe;
            SELECT count(*) INTO v_null_count FROM transformation_recipe WHERE department_id IS NULL;
            RAISE NOTICE 'transformation_recipe: total=%, null_dept=%', v_total_before, v_null_count;

            -- component
            SELECT count(*) INTO v_total_before FROM component;
            SELECT count(*) INTO v_null_count FROM component WHERE department_id IS NULL;
            RAISE NOTICE 'component: total=%, null_dept=%', v_total_before, v_null_count;

            -- flow_definition
            SELECT count(*) INTO v_total_before FROM flow_definition;
            SELECT count(*) INTO v_null_count FROM flow_definition WHERE department_id IS NULL;
            RAISE NOTICE 'flow_definition: total=%, null_dept=%', v_total_before, v_null_count;

            -- industrial_object
            SELECT count(*) INTO v_total_before FROM industrial_object;
            SELECT count(*) INTO v_null_count FROM industrial_object WHERE department_id IS NULL;
            RAISE NOTICE 'industrial_object: total=%, null_dept=%', v_total_before, v_null_count;

            -- equipment
            SELECT count(*) INTO v_total_before FROM equipment;
            SELECT count(*) INTO v_null_count FROM equipment WHERE department_id IS NULL;
            RAISE NOTICE 'equipment: total=%, null_dept=%', v_total_before, v_null_count;

            -- job
            SELECT count(*) INTO v_total_before FROM job;
            SELECT count(*) INTO v_null_count FROM job WHERE department_id IS NULL;
            RAISE NOTICE 'job: total=%, null_dept=%', v_total_before, v_null_count;

            -- flow_run
            SELECT count(*) INTO v_total_before FROM flow_run;
            SELECT count(*) INTO v_null_count FROM flow_run WHERE department_id IS NULL;
            RAISE NOTICE 'flow_run: total=%, null_dept=%', v_total_before, v_null_count;

            -- derivation_run
            SELECT count(*) INTO v_total_before FROM derivation_run;
            SELECT count(*) INTO v_null_count FROM derivation_run WHERE department_id IS NULL;
            RAISE NOTICE 'derivation_run: total=%, null_dept=%', v_total_before, v_null_count;

            -- audit_event
            SELECT count(*) INTO v_total_before FROM audit_event;
            SELECT count(*) INTO v_null_count FROM audit_event WHERE department_id IS NULL;
            RAISE NOTICE 'audit_event: total=%, null_dept=%', v_total_before, v_null_count;

            -- secret
            SELECT count(*) INTO v_total_before FROM secret;
            SELECT count(*) INTO v_null_count FROM secret WHERE department_id IS NULL;
            RAISE NOTICE 'secret: total=%, null_dept=%', v_total_before, v_null_count;

            -- backup_record
            SELECT count(*) INTO v_total_before FROM backup_record;
            SELECT count(*) INTO v_null_count FROM backup_record WHERE department_id IS NULL;
            RAISE NOTICE 'backup_record: total=%, null_dept=%', v_total_before, v_null_count;

            -- app_user
            SELECT count(*) INTO v_total_before FROM app_user;
            SELECT count(*) INTO v_null_count FROM app_user WHERE department_id IS NULL;
            RAISE NOTICE 'app_user: total=%, null_dept=%', v_total_before, v_null_count;

            -- scope_grant（表可能已删除，安全跳过）
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'scope_grant') THEN
                SELECT count(*) INTO v_total_before FROM scope_grant;
                SELECT count(*) INTO v_null_count FROM scope_grant WHERE department_id IS NULL;
                RAISE NOTICE 'scope_grant: total=%, null_dept=%', v_total_before, v_null_count;
            ELSE
                RAISE NOTICE 'scope_grant: table not exists, skipped';
            END IF;

            RAISE NOTICE '====== 0063 回填审计报告完成 ======';
        END
        $$;
        """
    )


def downgrade() -> None:
    """回滚回填：将 department_id 及 A 类列置回 NULL。"""
    # A 类表
    for table in [
        "fact",
        "parameter",
        "evidence_set",
        "artifact",
        "model",
        "transformation_recipe",
        "component",
        "flow_definition",
        "industrial_object",
        "equipment",
    ]:
        op.execute(f"UPDATE {table} SET department_id = NULL")
        if table not in ("industrial_object", "equipment"):
            op.execute(
                f"UPDATE {table} SET visible_departments = NULL, visibility_scope = NULL, owner_user_id = NULL"
            )
        else:
            op.execute(f"UPDATE {table} SET visibility_scope = NULL, owner_user_id = NULL")

    # B 类表
    for table in [
        "job",
        "flow_run",
        "derivation_run",
        "audit_event",
        "secret",
        "backup_record",
        "app_user",
        "scope_grant",
    ]:
        op.execute(f"UPDATE {table} SET department_id = NULL")
