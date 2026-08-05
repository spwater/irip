# IRIP AI 助手协作功能需求说明书

## 1. 项目现状

IRIP 已完成 AI 助手分析橱窗及可视化升级（Alpha v0.8.0），具备：
- 三栏布局（左对话列表 + 中对话区 + 右分析橱窗）
- AI 对话 + 数据加载 + ECharts/Plotly 可视化 + 公式渲染
- 橱窗留存 + 原文定位 + 成果导出
- 历史

对话搜索

当前 AI 助手为**单用户模式**——每个用户的对话完全隔离（`ai_conversation.user_id == me`），不支持多人协作。

## 2. 本次建设目标

在现有 AI 助手对话模型上扩展协作能力：
1. 同一对话支持多个用户参与
2. 用户可在对话中 @ 他人分配任务、@ AI 助手执行分析
3. 基于角色的权限控制 + 数据范围隔离
4. 个人账户基础管理（改密码、改头像）
5. 实验室负责人可管理本组织成员角色

## 3. 建设原则

- 基于现有系统增量建设，不另起炉灶
- **对话共享，数据隔离**——对话是协作空间，数据按操作者 org_id 隔离，两者正交
- 合作基于互信——橱窗、数据摘要对参与者全可见，不做脱敏
- 一期做同 org 协作，跨 org 协作二期实现（跨 org 对话需 lab_director 以上创建）

## 4. 现有权限体系

### 4.1 内置角色（5 个）

| 角色 | 定位 | 数据范围 |
|------|------|---------|
| platform_administrator | 平台管理员 | 全平台 |
| platform_auditor | 平台监督员 | 全平台（只读） |
| lab_director | 实验室负责人 | 本 org |
| lab_member | 实验室成员 | 本 org |
| lab_viewer | 实验室成员(只读) | 本 org |

### 4.2 现有数据模型

- `ai_conversation`: id / org_id / user_id / title / provider_mode / pinned / archived / system_context / created_at / updated_at
- `ai_message`: id / conversation_id(FK CASCADE) / role / content / tool_calls / citations / uncertainty / created_at
- `app_user`: id / org_id / email / password_hash / display_name / roles
- `organization`: id / name
- 最新 Alembic 迁移：0052

### 4.3 关键约束

- org_id 即组织/实验室，无部门概念
- 跨部门 = 跨 org_id
- 一个用户只属于一个 org_id

## 5. 权限设计

### 5.1 新增基础权限（所有角色拥有，不通过角色分配）

| 权限 | 说明 |
|------|------|
| account:profile | 修改自己的头像、显示名 |
| account:password | 修改自己的密码 |

实现方式：在 `Principal.has_permission()` 中硬编码放行，不查角色。

### 5.2 新增协作权限

| 权限 | 说明 |
|------|------|
| conversation:create | 创建对话 |
| conversation:invite | 邀请他人加入对话 |
| conversation:remove_member | 移除对话成员（仅 owner） |
| conversation:delete | 删除对话（owner 或 admin） |
| conversation:manage | 管理对话设置（标题/归档/置顶） |

### 5.3 角色权限矩阵

| 能力 | 平台管理员 | 实验室负责人 | 实验室成员 | 只读成员 |
|------|-----------|------------|-----------|---------|
| 创建对话 | ✅ | ✅ | ✅ | ❌ |
| 邀请同 org 成员 | ✅ | ✅ | ✅ | ❌ |
| 创建跨 org 对话 | ✅ | ✅ | ❌ | ❌ |
| @ 人 | ✅ | ✅ | ✅ | ✅ |
| @ AI 助手 | ✅ | ✅ | ✅ | ✅ |
| 移除对话成员 | ✅ | ✅(自己创建的) | ❌ | ❌ |
| 删除对话 | ✅(全部) | ✅(自己的) | ✅(自己的) | ❌ |
| 分配角色 | ✅(全部) | ✅(本 org, lab_member/viewer) | ❌ | ❌ |
| 修改密码/头像 | ✅ | ✅ | ✅ | ✅ |
| 查看审计日志 | ✅ | ❌ | ❌ | ❌ |

### 5.4 数据范围控制

| 角色 | 对话可见范围 | @人范围 | 数据查询范围 |
|------|------------|---------|------------|
| 平台管理员 | 全平台 | 全平台 | 全部 |
| 平台监督员 | 全平台(只读) | ❌ | 全部(只读) |
| 实验室负责人 | 本 org + 参与的 | 本 org | 本 org |
| 实验室成员 | 本 org + 参与的 | 本 org | 本 org |
| 只读成员 | 本 org + 参与的 | 本 org | 本 org(只读) |

数据查询范围始终按**当前操作者的 org_id** 过滤，AI 加载数据时也按操作者 org_id。

## 6. 数据模型变更

### 6.1 新增表

```sql
-- 对话参与者关联表
CREATE TABLE conversation_participant (
  conversation_id UUID NOT NULL REFERENCES ai_conversation(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL DEFAULT 'member',  -- owner / member
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (conversation_id, user_id)
);

CREATE INDEX idx_conv_participant_user ON conversation_participant(user_id);
```

### 6.2 修改表

```sql
-- ai_message 加 mentions 字段
ALTER TABLE ai_message ADD COLUMN mentions JSONB DEFAULT '[]'::jsonb;
-- mentions 存 @ 的人 ID 数组: ["uuid1", "uuid2"]

-- app_user 加头像字段
ALTER TABLE app_user ADD COLUMN avatar_url TEXT;
```

## 7. 功能需求

### 7.1 P0（必须完成）

| 编号 | 标题 | 描述 |
|------|------|------|
| P0-01 | 对话参与者管理 | 创建对话时创建者为 owner；支持邀请同 org 成员加入；owner 可移除成员；成员可退出 |
| P0-02 | 对话查询改造 | 对话列表查询从 `user_id == me` 改为 `user_id == me OR participant`；按"私有/同 org/跨 org"三栏筛选 |
| P0-03 | @ 人提及 | 消息发送时支持 @ 人，mentions 存 JSONB；输入 @ 弹出本 org 成员列表 |
| P0-04 | 消息区分发送者 | 消息渲染时显示发送者头像、姓名、角色标签 |
| P0-05 | 个人设置 | 修改密码（旧密码验证）；修改头像（上传到 MinIO）和显示名 |
| P0-06 | 角色管理 | lab_director 可给本 org 成员分配 lab_member / lab_viewer 角色；范围校验 |
| P0-07 | @ 人列表接口 | 按当前用户 org_id 返回可 @ 的成员列表（含 id、display_name、avatar_url、role） |
| P0-08 | 数据隔离 | AI 加载数据时按操作者 org_id 过滤；对话内数据不跨 org 共享 |
| P0-09 | 对话列表三栏 | 私有对话 / 同 org 对话 / 跨 org 对话（跨 org 占位不可用，二期实现） |

### 7.2 P1（重要）

| 编号 | 标题 | 描述 |
|------|------|------|
| P1-01 | 未读消息标记 | 对话列表显示未读消息数（最后阅读时间戳 vs 最后消息时间） |
| P1-02 | @ 提及通知 | 被 @ 的人在对话列表看到红点标记 |
| P1-03 | 对话成员列表面板 | 对话区可展开成员列表（头像、姓名、角色、加入时间） |
| P1-04 | 消息时间线 | 多人对话按时间排序，清晰展示谁在什么时候说了什么 |

### 7.3 P2（后续）

| 编号 | 标题 | 描述 |
|------|------|------|
| P2-01 | 跨 org 对话 | lab_director 创建跨 org 对话，邀请其他 org 成员 |
| P2-02 | 实时消息推送 | WebSocket/SSE 实时推送新消息 |
| P2-03 | 消息已读回执 | 显示谁已读、谁未读 |
| P2-04 | 对话 @ 设置 | 允许/禁止 @ AI 助手、允许/禁止成员互邀 |
| P2-05 | 自定义角色 | 组织内自定义角色（基于 5 个内置角色扩展） |

## 8. 界面改造

### 8.1 对话列表（左侧）

- 顶部三栏筛选标签：`私有` | `同 org` | `跨 org`（跨 org 灰色占位，标注"二期"）
- 私有：仅自己创建的对话
- 同 org：本 org 内我创建的 + 我被邀请的
- 每条对话显示：标题、参与者头像组（最多 3 个 + 数量）、未读数

### 8.2 对话区（中间）

- 消息区分发送者：头像 + 姓名 + 角色标签 + 时间
- @ 人高亮显示（蓝色背景 + @ 符号）
- 输入框支持 @ 触发成员选择弹窗
- 对话头部显示参与者数量，点击展开成员列表

### 8.3 个人设置页

- 路径：`/platform` → 个人设置
- 修改头像（上传图片，裁剪为方形，存 MinIO）
- 修改显示名
- 修改密码（输入旧密码 + 新密码 + 确认新密码）

### 8.4 角色管理页

- 路径：`/governance` → 用户管理 → 角色分配
- 仅 lab_director 和 platform_administrator 可见
- lab_director 只能操作本 org 成员，只能分配 lab_member / lab_viewer
- platform_administrator 可操作全部，可分配全部角色

## 9. 关键设计决策

### 9.1 对话归属

- 对话创建者自动成为 owner
- 对话保留 `user_id`（创建者）和 `org_id`（创建者 org）
- 参与者通过 `conversation_participant` 关联
- 同 org 协作：参与者 org_id 与对话 org_id 相同
- 跨 org 协作（二期）：参与者 org_id 可不同，对话不限定单一 org

### 9.2 数据隔离原则

- 对话内容（消息、橱窗卡片）对参与者全可见
- AI 加载数据按**当前操作者 org_id** 过滤，不按对话 org_id
- 橱窗卡片 data_source 对参与者全可见（含样品名、字段名），不做脱敏
- 合作基于互信，数据隔离是基本管理不是互相提防

### 9.3 不做内部 IM

- 复用现有对话模型，不新建独立消息系统
- 消息依附于科研对话，不是独立聊天实体
- AI 助手天然在对话中，@AI 和 @人 机制一致

### 9.4 通知机制

- 一期用轮询（TanStack Query refetch）拉取新消息和 @ 通知
- 二期可升级 WebSocket/SSE 实时推送

## 10. 本次工作范围

### 必须完成（一期）：
1. 对话参与者关联表 + 迁移
2. 对话查询逻辑改造（user_id == me → user_id == me OR participant）
3. 对话列表三栏筛选（私有 / 同 org / 跨 org 占位）
4. @ 人提及（输入框 + mentions 存储 + 渲染高亮）
5. 消息区分发送者（头像 + 姓名 + 角色标签）
6. 个人设置（改密码 + 改头像 + 改显示名）
7. 角色管理（lab_director 分配本 org 成员角色）
8. @ 人列表接口（按 org_id 返回成员）
9. AI 数据加载按操作者 org_id 隔离
10. 基础权限扩展（account:profile, account:password, conversation:* 权限）

### 本次不做：
- 跨 org 对话（二期）
- WebSocket 实时推送（二期）
- 消息已读回执（二期）
- 自定义角色（二期）
- 不重新开发对话系统
- 不改变现有 AI 助手核心能力

## 11. 兼容性要求

- 不影响现有单人对话功能
- 现有对话自动转为"私有"（参与者只有自己）
- 现有橱窗、搜索、Plotly、公式渲染等功能不受影响
- 现有权限校验逻辑（require_permission）保持不变，只新增权限
