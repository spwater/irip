# 数据上线指南 — 映射配置（MappingProfile）

> 适用版本：IRIP V1+
> 关联文档：`docs/user-guide/particle-size.md`、`docs/architecture/domain-invariants.md`

本指南描述如何将外部数据源接入 IRIP 平台：从源数据预览到 MappingProfile 创建、字段映射配置、连接器配置，最终通过数据摄入组件完成标准化导入。

---

## 1. 概述

数据上线流程：

```
源数据预览 → MappingProfile 创建 → 字段映射配置 → 连接器配置 → 审批 → 发布 → 数据摄入组件使用
```

**MappingProfile** 是字段映射的不可变版本化配置，定义源字段到 IRIP L1 标准变量的映射规则。发布后不可修改，修改需创建新版本。

---

## 2. MappingProfile 创建

### 2.1 通过 Web 控制台

1. 进入 **数据摄入** 页面（`/ingestions`）。
2. 上传源数据文件（Excel/CSV/PDF）。
3. 系统自动解析并预览数据结构（字段名、数据类型、样例值）。
4. 进入字段映射环节（详见 §3）。

### 2.2 通过 API

```bash
# 创建 MappingProfile
curl -X POST http://localhost:8000/api/v1/mapping-profiles \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "lab_excel_mapping_v1",
    "display_name": "实验室 Excel 数据映射 v1",
    "source_format": "xlsx",
    "field_mappings": [
      {
        "source_field": "D50_um",
        "target_variable": "particle.d50",
        "transform": {"type": "unit_convert", "from": "μm", "to": "μm"},
        "confidence": 0.95
      }
    ]
  }'
```

### 2.3 MappingProfile 结构

```json
{
  "code": "lab_excel_mapping_v1",
  "display_name": "实验室 Excel 数据映射 v1",
  "source_format": "xlsx",
  "field_mappings": [
    {
      "source_field": "D50_um",
      "target_variable": "particle.d50",
      "transform": {"type": "unit_convert", "from": "μm", "to": "μm"},
      "confidence": 0.95
    },
    {
      "source_field": "sample_date",
      "target_variable": null,
      "transform": null,
      "confidence": 0.30
    }
  ],
  "sha256": "<profile_content_sha256>"
}
```

| 字段 | 说明 |
|------|------|
| `code` | 映射配置唯一代码 |
| `source_format` | 源数据格式（xlsx / csv / pdf / postgres / rest） |
| `field_mappings` | 字段映射列表 |
| `field_mappings[].source_field` | 源字段名 |
| `field_mappings[].target_variable` | 目标 L1 标准变量代码（null = 不映射） |
| `field_mappings[].transform` | 转换规则（单位转换/缺失值处理等） |
| `field_mappings[].confidence` | 映射置信度评分（0.0–1.0） |
| `sha256` | 映射配置内容的 SHA-256 校验和 |

---

## 3. 字段映射配置

### 3.1 映射评分机制

系统自动对每个源字段与已发布的 L1 标准变量进行匹配评分：

| 评分范围 | 含义 | 操作 |
|---------|------|------|
| > 0.9 | 高置信度自动匹配 | 自动选中（可手动调整） |
| 0.6–0.9 | 中置信度匹配 | 需人工确认 |
| < 0.6 | 低置信度/无匹配 | 需手动指定标准变量或标记"不映射" |

评分依据：
- 字段名相似度（编辑距离 / 子串匹配）。
- 单位兼容性（是否在标准变量的允许单位列表中）。
- 数据类型匹配（数值型/文本型/时间型）。

### 3.2 逐一确认映射

1. 在字段映射面板，每个源字段列出 Top-3 匹配候选标准变量。
2. 数据管理员逐一确认：
   - 选择正确的目标标准变量。
   - 配置转换规则（如单位转换：`μm → mm`）。
   - 不需要的字段标记为"不映射"（target_variable = null）。
3. **映射确认门控**：未确认所有映射时，"确认并导入"按钮禁用。

### 3.3 单位转换配置

映射时可配置单位转换（仿射变换 `y = ax + b`）：

```json
{
  "source_field": "thickness_mm",
  "target_variable": "grate.bed_thickness",
  "transform": {
    "type": "unit_convert",
    "from": "mm",
    "to": "m",
    "a": 0.001,
    "b": 0
  }
}
```

---

## 4. 连接器配置

MappingProfile 支持多种数据源连接器。

### 4.1 PostgreSQL 连接器

适用于从数据库直接查询数据：

```bash
# 创建 PostgreSQL 连接器配置
curl -X POST http://localhost:8000/api/v1/connectors \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "postgres",
    "code": "lab_db_connector",
    "display_name": "实验室数据库连接器",
    "config": {
      "host": "lab-db.internal",
      "port": 5432,
      "database": "lab_data",
      "username_secret_id": "secret_lab_db_user",
      "password_secret_id": "secret_lab_db_pass"
    }
  }'
```

**安全约束**：
- 凭据（用户名/密码）仅以 `secret_id` 引用，绝不内联明文。
- 凭据值不出现在日志/输出/错误信息中。
- SQL 查询仅允许 SELECT 语句（sqlparse 解析拦截非查询语句）。

### 4.2 REST 连接器

适用于从 REST API 获取数据：

```bash
curl -X POST http://localhost:8000/api/v1/connectors \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "kind": "rest",
    "code": "lab_api_connector",
    "display_name": "实验室 API 连接器",
    "config": {
      "base_url": "https://api.lab.internal",
      "auth_type": "bearer",
      "token_secret_id": "secret_lab_api_token",
      "endpoint": "/v1/experiments",
      "method": "GET"
    }
  }'
```

**安全约束**：
- SSRF 防护：禁止访问内网地址（127.0.0.0/8、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16）。
- 强制 HTTPS（可配置豁免 localhost）。
- 响应大小限制。

### 4.3 文件连接器

适用于 Excel/CSV/JSON 文件（默认连接器）：

- 上传文件时自动使用 FileConnector。
- 支持 `.xlsx`（openpyxl）、`.csv`（标准库 csv）、`.json`、`.pdf`（pdfplumber）。

---

## 5. 发布 MappingProfile

### 5.1 审批与发布

1. 确认所有字段映射后，提交 MappingProfile 审批。
2. 审批通过后发布版本（不可变）。
3. 发布后可通过数据摄入组件引用。

```bash
# 发布 MappingProfile 版本
curl -X POST http://localhost:8000/api/v1/mapping-profiles/{id}/publish \
  -H "Authorization: Bearer <jwt>"
```

### 5.2 不可变性

- MappingProfile 版本发布后不可修改（只 INSERT 不 UPDATE）。
- 修改字段映射需创建新版本。
- `sha256` 字段确保映射配置内容完整性。

---

## 6. 数据摄入组件使用

### 6.1 在流程中编排数据摄入

MappingProfile 发布后，可在流程（Flow）中编排数据摄入组件：

```
[excel_reader] → [field_mapper(引用 MappingProfile)] → [quality_check] → [fact_writer]
```

1. `excel_reader` 组件读取 Excel 文件，输出 ObservationTable。
2. `field_mapper` 组件引用已发布的 MappingProfile，将源字段映射到 L1 标准变量。
3. `quality_check` 组件执行 Schema/Range/Order 检查，输出 DiagnosticReport。
4. 下游步骤（独立服务或流程节点）创建事实。

### 6.2 通过 API 触发摄入

```bash
# 创建摄入作业
curl -X POST http://localhost:8000/api/v1/ingestions \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "artifact_id": "<uploaded_file_artifact_id>",
    "mapping_profile_version_id": "<published_mapping_profile_version_id>",
    "object_id": "<industrial_object_id>"
  }'
```

返回 `202 Accepted` + `job_id`，异步执行摄入。

### 6.3 查看摄入结果

```bash
# 查询作业状态
curl http://localhost:8000/api/v1/jobs/{job_id} \
  -H "Authorization: Bearer <jwt>"
```

作业完成后：
- 创建事实（Fact + FactRevision，不可变）。
- 质量评估自动生成。
- 可在实验事实页面查看结果。

---

## 7. 密钥管理

### 7.1 创建 Secret

连接器凭据通过 `secret_id` 引用，不内联明文：

```bash
# 创建 PostgreSQL 密码 secret
curl -X POST http://localhost:8000/api/v1/secrets \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "secret_lab_db_pass",
    "display_name": "实验室数据库密码",
    "value": "<actual_password>"
  }'
```

### 7.2 安全约束

- Secret 值仅存储于加密存储中，API 返回时不包含明文。
- 连接器执行时通过 `secret_id` 解析获取凭据值。
- 凭据值不出现在日志、输出、错误信息或审计 payload 中（脱敏处理）。

---

## 8. 常见问题

### Q: 映射评分总是很低怎么办？
A: 检查源字段命名是否与标准变量代码接近；确保标准变量已发布；必要时手动指定映射目标。

### Q: 如何修改已发布的 MappingProfile？
A: 已发布版本不可修改。创建新版本，修改字段映射后重新发布。旧版本保留不变，已有流程可继续引用旧版本。

### Q: 连接器密码如何安全存储？
A: 使用 Secret API 创建密钥，连接器配置中仅引用 `secret_id`。凭据值加密存储，不在日志/输出中泄露。

### Q: PostgreSQL 连接器可以执行 INSERT/UPDATE 吗？
A: 不可以。PostgreSQL 连接器仅允许 SELECT 语句，非查询语句被 sqlparse 解析拦截。这是只读边界约束。
