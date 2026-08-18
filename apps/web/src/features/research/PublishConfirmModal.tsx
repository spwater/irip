/**
 * PublishConfirmModal — 发布确认弹窗
 *
 * 选定成果区 + 成果包信息区 + 权限与可见范围区 + 溯源引用区
 * 从 Workspace 发布成果时弹出
 */
import { useState, useCallback, useEffect } from 'react';
import {
  Modal,
  Input,
  Select,
  Tag,
  Space,
  Typography,
  Divider,
  Spin,
  Alert,
  message,
  Checkbox,
  Button,
} from 'antd';
import {
  DatabaseOutlined,
  BarChartOutlined,
  BulbOutlined,
  SafetyOutlined,
  WarningOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import type { ProductSummary } from '@/api/researchProducts';
import { apiPublishResult, type ResultVersionRef } from '@/api/researchPublish';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

export type PublishConfirmModalProps = {
  open: boolean;
  workspaceId: string;
  products: ProductSummary[];
  onClose: () => void;
  onPublished?: (versionRef: ResultVersionRef) => void;
};

type AclType = 'private' | 'tree' | 'explicit' | 'all';

const ACL_OPTIONS = [
  { label: '私有（仅自己可见）', value: 'private' },
  { label: '部门（同部门可见）', value: 'tree' },
  { label: '指定用户', value: 'explicit' },
  { label: '公开（全员可见）', value: 'all' },
];

export function PublishConfirmModal({
  open,
  workspaceId,
  products,
  onClose,
  onPublished,
}: PublishConfirmModalProps): JSX.Element {
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [releaseNotes, setReleaseNotes] = useState('');
  const [aclType, setAclType] = useState<AclType>('private');
  const [explicitUserIds, setExplicitUserIds] = useState<string[]>([]);
  const [explicitUserInput, setExplicitUserInput] = useState('');
  const [declassifyReason, setDeclassifyReason] = useState('');
  const [publishing, setPublishing] = useState(false);

  // 选中的产物
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<Set<string>>(new Set());
  const [selectedViewIds, setSelectedViewIds] = useState<Set<string>>(new Set());
  const [selectedInsightIds, setSelectedInsightIds] = useState<Set<string>>(new Set());

  // 重置状态
  useEffect(() => {
    if (open) {
      setTitle('');
      setSummary('');
      setTags([]);
      setTagInput('');
      setReleaseNotes('');
      setAclType('private');
      setExplicitUserIds([]);
      setExplicitUserInput('');
      setDeclassifyReason('');

      // 默认选中全部已确认产物
      const dsIds = new Set<string>();
      const vwIds = new Set<string>();
      const insIds = new Set<string>();
      for (const p of products) {
        if (p.product_type === 'derived_dataset') dsIds.add(p.product_id);
        else if (p.product_type === 'view') vwIds.add(p.product_id);
        else if (p.product_type === 'insight') insIds.add(p.product_id);
      }
      setSelectedDatasetIds(dsIds);
      setSelectedViewIds(vwIds);
      setSelectedInsightIds(insIds);
    }
  }, [open, products]);

  // 按类型分组
  const datasets = products.filter((p) => p.product_type === 'derived_dataset');
  const views = products.filter((p) => p.product_type === 'view');
  const insights = products.filter((p) => p.product_type === 'insight');

  const toggleProduct = useCallback(
    (type: string, id: string) => {
      const setter =
        type === 'derived_dataset'
          ? setSelectedDatasetIds
          : type === 'view'
            ? setSelectedViewIds
            : setSelectedInsightIds;
      setter((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    },
    [],
  );

  const handleAddTag = useCallback(() => {
    const trimmed = tagInput.trim();
    if (trimmed && !tags.includes(trimmed)) {
      setTags([...tags, trimmed]);
    }
    setTagInput('');
  }, [tagInput, tags]);

  const handleAddExplicitUser = useCallback(() => {
    const trimmed = explicitUserInput.trim();
    if (trimmed && !explicitUserIds.includes(trimmed)) {
      setExplicitUserIds([...explicitUserIds, trimmed]);
    }
    setExplicitUserInput('');
  }, [explicitUserInput, explicitUserIds]);

  // is_declassify 判断：选择 all 或 tree 且超出包络
  const isDeclassify = aclType === 'all';

  const canPublish = useCallback((): boolean => {
    if (!title.trim()) return false;
    if (selectedDatasetIds.size === 0 && selectedViewIds.size === 0) {
      return false; // 至少需要一个数据集或视图
    }
    if (selectedDatasetIds.size === 0 && selectedViewIds.size === 0 && selectedInsightIds.size === 0) {
      return false;
    }
    if (aclType === 'explicit' && explicitUserIds.length === 0) return false;
    if (isDeclassify && !declassifyReason.trim()) return false;
    return true;
  }, [title, selectedDatasetIds, selectedViewIds, selectedInsightIds, aclType, explicitUserIds, isDeclassify, declassifyReason]);

  const handlePublish = useCallback(async () => {
    if (!title.trim()) {
      message.warning('请输入成果包标题');
      return;
    }
    if (selectedDatasetIds.size === 0 && selectedViewIds.size === 0) {
      message.warning('发布成果包至少需要包含一个数据集或视图，Insight 不能单独发布');
      return;
    }
    if (selectedDatasetIds.size === 0 && selectedViewIds.size === 0 && selectedInsightIds.size === 0) {
      message.warning('请至少选择一个已确认产物');
      return;
    }
    if (aclType === 'explicit' && explicitUserIds.length === 0) {
      message.warning('指定用户模式下请输入至少一个用户 ID');
      return;
    }
    if (isDeclassify && !declassifyReason.trim()) {
      message.warning('突破权限包络时请填写理由');
      return;
    }

    setPublishing(true);
    try {
      const ref = await apiPublishResult(workspaceId, {
        title: title.trim(),
        summary: summary.trim(),
        tags,
        release_notes: releaseNotes.trim(),
        dataset_ids: Array.from(selectedDatasetIds),
        view_ids: Array.from(selectedViewIds),
        insight_ids: Array.from(selectedInsightIds),
        requested_acl: aclType,
        explicit_user_ids: explicitUserIds,
        is_declassify: isDeclassify,
        declassify_reason: isDeclassify ? declassifyReason.trim() : undefined,
      });
      message.success(`成果包已发布（v${ref.version_number}）`);
      onPublished?.(ref);
      onClose();
    } catch {
      message.error('发布失败');
    } finally {
      setPublishing(false);
    }
  }, [
    title,
    summary,
    tags,
    releaseNotes,
    selectedDatasetIds,
    selectedViewIds,
    selectedInsightIds,
    aclType,
    explicitUserIds,
    isDeclassify,
    declassifyReason,
    workspaceId,
    onPublished,
    onClose,
  ]);

  const totalSelected = selectedDatasetIds.size + selectedViewIds.size + selectedInsightIds.size;

  const renderProductList = (
    items: ProductSummary[],
    selectedIds: Set<string>,
    type: string,
    icon: JSX.Element,
    label: string,
  ) => {
    if (items.length === 0) return null;
    return (
      <div style={{ marginBottom: 8 }}>
        <Text strong style={{ fontSize: 13 }}>
          {icon} {label} ({items.length})
        </Text>
        <div style={{ paddingLeft: 16, marginTop: 4 }}>
          {items.map((item) => (
            <div
              key={item.product_id}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}
            >
              <Checkbox
                checked={selectedIds.has(item.product_id)}
                onChange={() => toggleProduct(type, item.product_id)}
              >
                <Text style={{ fontSize: 12 }}>{item.name}</Text>
                <Tag style={{ fontSize: 10, margin: '0 4px' }}>v{item.current_version}</Tag>
              </Checkbox>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <Modal
      title="发布研究成果包"
      open={open}
      onOk={handlePublish}
      onCancel={onClose}
      confirmLoading={publishing}
      okText={`发布 (${totalSelected} 个产物)`}
      cancelText="取消"
      okButtonProps={{ disabled: !canPublish() }}
      width={720}
      destroyOnHidden
    >
      <Spin spinning={publishing}>
        {/* 选定成果区 */}
        <div style={{ marginBottom: 16 }}>
          <Text strong style={{ fontSize: 14, display: 'block', marginBottom: 8 }}>
            <CheckCircleOutlined style={{ color: 'var(--ocean-accent, #1689ae)' }} /> 选定成果
          </Text>
          {products.length === 0 ? (
            <Alert
              type="warning"
              message="暂无已确认产物，请先在 Workspace 中确认产物"
              showIcon
            />
          ) : (
            <>
              {renderProductList(datasets, selectedDatasetIds, 'derived_dataset', <DatabaseOutlined />, '数据集')}
              {renderProductList(views, selectedViewIds, 'view', <BarChartOutlined />, '视图')}
              {renderProductList(insights, selectedInsightIds, 'insight', <BulbOutlined />, 'Insight')}
              <Text type="secondary" style={{ fontSize: 12 }}>
                已选 {totalSelected} / {products.length} 个产物
              </Text>
            </>
          )}
        </div>

        <Divider style={{ margin: '12px 0' }} />

        {/* 成果包信息区 */}
        <div style={{ marginBottom: 16 }}>
          <Text strong style={{ fontSize: 14, display: 'block', marginBottom: 8 }}>
            成果包信息
          </Text>
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>标题 *</Text>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="为成果包命名…"
                maxLength={256}
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>摘要</Text>
              <TextArea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="简要描述研究成果…"
                rows={2}
                maxLength={4096}
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>标签</Text>
              <Space.Compact style={{ width: '100%', marginTop: 4 }}>
                <Input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onPressEnter={handleAddTag}
                  placeholder="输入标签后回车添加…"
                />
                <Button onClick={handleAddTag}>添加</Button>
              </Space.Compact>
              {tags.length > 0 && (
                <Space size={4} wrap style={{ marginTop: 4 }}>
                  {tags.map((tag) => (
                    <Tag
                      key={tag}
                      closable
                      onClose={() => setTags(tags.filter((t) => t !== tag))}
                    >
                      {tag}
                    </Tag>
                  ))}
                </Space>
              )}
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>发布说明</Text>
              <TextArea
                value={releaseNotes}
                onChange={(e) => setReleaseNotes(e.target.value)}
                placeholder="版本变更说明…"
                rows={2}
                maxLength={4096}
                style={{ marginTop: 4 }}
              />
            </div>
          </Space>
        </div>

        <Divider style={{ margin: '12px 0' }} />

        {/* 权限与可见范围区 */}
        <div style={{ marginBottom: 16 }}>
          <Text strong style={{ fontSize: 14, display: 'block', marginBottom: 8 }}>
            <SafetyOutlined /> 权限与可见范围
          </Text>
          <Select
            value={aclType}
            onChange={(v) => setAclType(v as AclType)}
            options={ACL_OPTIONS}
            style={{ width: '100%', marginBottom: 8 }}
          />
          {aclType === 'explicit' && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>指定用户 ID（UUID 格式，逗号分隔）</Text>
              <Space.Compact style={{ width: '100%', marginTop: 4 }}>
                <Input
                  value={explicitUserInput}
                  onChange={(e) => setExplicitUserInput(e.target.value)}
                  onPressEnter={handleAddExplicitUser}
                  placeholder="输入用户 ID 后回车添加…"
                />
                <Button onClick={handleAddExplicitUser}>添加</Button>
              </Space.Compact>
              {explicitUserIds.length > 0 && (
                <Space size={4} wrap style={{ marginTop: 4 }}>
                  {explicitUserIds.map((uid) => (
                    <Tag
                      key={uid}
                      closable
                      onClose={() => setExplicitUserIds(explicitUserIds.filter((u) => u !== uid))}
                    >
                      {uid.substring(0, 8)}…
                    </Tag>
                  ))}
                </Space>
              )}
            </div>
          )}
          {isDeclassify && (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message="突破权限包络"
              description={
                <div>
                  <Paragraph style={{ margin: '4px 0', fontSize: 12 }}>
                    公开权限可能超出源数据的权限包络交集，需要填写突破理由。
                  </Paragraph>
                  <TextArea
                    value={declassifyReason}
                    onChange={(e) => setDeclassifyReason(e.target.value)}
                    placeholder="请说明突破权限包络的理由…"
                    rows={2}
                    maxLength={1024}
                  />
                </div>
              }
              style={{ marginTop: 8 }}
            />
          )}
        </div>

        {/* 溯源引用区 */}
        <div>
          <Text strong style={{ fontSize: 14, display: 'block', marginBottom: 8 }}>
            溯源引用
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            发布后将自动记录 Workspace → 成果包版本、各产物版本 → 成果包版本的溯源边。
            成果包内容哈希为全部选定产物版本引用的 SHA-256 哈希。
          </Text>
        </div>
      </Spin>
    </Modal>
  );
}
