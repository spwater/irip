/**
 * 数据库备份管理页面
 *
 * 功能（docs/prd-db-backup.md §4.2 / docs/arch-db-backup.md §6 T04）：
 * - 顶部汇总栏：备份总用量、每日快照数、里程碑数
 * - "创建里程碑备份"按钮 + Modal（名称 + 描述）
 * - 两个子 Tab：每日镜像 / 里程碑备份
 * - 每日镜像表格：日期、大小、状态、迁移版本、[回滚] 按钮
 * - 里程碑表格：名称、描述、创建时间、大小、状态、[恢复][删除] 按钮
 * - 回滚确认 Modal：危险操作，需输入"确认回滚"
 * - 用 Ant Design 组件（Table, Tabs, Modal, Button, Statistic, Tag, message）
 * - 用 TanStack Query 管理数据（useQuery, useMutation）
 * - 复用 ocean-panel 样式类
 */

import { useMemo, useState } from 'react';
import {
  Button,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import {
  apiCreateBackup,
  apiDeleteBackup,
  apiGetBackupStats,
  apiListBackups,
  apiRestoreBackup,
  type BackupRecordItem,
  type BackupStats,
  type BackupStatus,
  type BackupType,
} from '@/api/backups';
import { extractApiError } from '@/api/types';
import { DataTableShell, StatusMark } from '@/shared/ui';
import { QueryStateDisplay } from '@/features/components/StateDisplay';
import type { StatusSemantic } from '@/theme/tokens';

const { Text } = Typography;

/** 备份状态 → 语义映射 */
const STATUS_SEMANTIC: Record<BackupStatus, StatusSemantic> = {
  pending: 'warning',
  succeeded: 'success',
  failed: 'danger',
};

/** 备份状态 → 中文标签 */
const STATUS_LABEL: Record<BackupStatus, string> = {
  pending: '进行中',
  succeeded: '成功',
  failed: '失败',
};

/** 备份类型 → 标签颜色 */
const TYPE_TAG_COLOR: Record<BackupType, string> = {
  daily: 'blue',
  milestone: 'gold',
  pre_restore: 'purple',
};

/** 备份类型 → 中文标签 */
const TYPE_LABEL: Record<BackupType, string> = {
  daily: '每日镜像',
  milestone: '里程碑',
  pre_restore: '回滚前备份',
};

/** 格式化文件大小为人类可读字符串 */
function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return '-';
  const units: string[] = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size: number = bytes;
  let unitIndex: number = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 2)} ${units[unitIndex]}`;
}

/** 格式化日期时间 */
function formatDateTime(val: string | null | undefined): string {
  if (!val) return '-';
  try {
    return new Date(val).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return val;
  }
}

/** 格式化日期（仅日期部分） */
function formatDate(val: string | null | undefined): string {
  if (!val) return '-';
  try {
    return new Date(val).toLocaleDateString('zh-CN');
  } catch {
    return val;
  }
}

/**
 * 数据库备份管理页面组件
 */
export function DatabaseBackupPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<string>('daily');
  const [createModalOpen, setCreateModalOpen] = useState<boolean>(false);
  const [restoreTarget, setRestoreTarget] = useState<BackupRecordItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BackupRecordItem | null>(null);
  const [createForm] = Form.useForm();
  const [restoreConfirmText, setRestoreConfirmText] = useState<string>('');

  // ---- 数据查询：汇总统计 ----
  const { data: stats, isLoading: statsLoading } = useQuery<BackupStats>({
    queryKey: ['backups', 'stats'],
    queryFn: apiGetBackupStats,
    refetchInterval: 10000,
  });

  // ---- 数据查询：每日镜像列表 ----
  const {
    data: dailyData,
    isLoading: dailyLoading,
    isError: dailyError,
    error: dailyErr,
    refetch: refetchDaily,
  } = useQuery({
    queryKey: ['backups', 'list', 'daily'],
    queryFn: () => apiListBackups({ type: 'daily', limit: 14 }),
    refetchInterval: 10000,
  });

  // ---- 数据查询：里程碑备份列表 ----
  const {
    data: milestoneData,
    isLoading: milestoneLoading,
    isError: milestoneError,
    error: milestoneErr,
    refetch: refetchMilestone,
  } = useQuery({
    queryKey: ['backups', 'list', 'milestone'],
    queryFn: () => apiListBackups({ type: 'milestone', limit: 100 }),
    refetchInterval: 10000,
  });

  const dailyItems: BackupRecordItem[] = dailyData?.items ?? [];
  const milestoneItems: BackupRecordItem[] = milestoneData?.items ?? [];

  // ---- 创建里程碑备份 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateBackup,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['backups'] });
      message.success(`里程碑备份已创建（作业 ID: ${result.job_id.slice(0, 8)}...）`);
      setCreateModalOpen(false);
      createForm.resetFields();
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 恢复备份 Mutation ----
  const restoreMutation = useMutation({
    mutationFn: (id: string) => apiRestoreBackup(id),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['backups'] });
      message.success(`恢复作业已创建（作业 ID: ${result.job_id.slice(0, 8)}...）`);
      setRestoreTarget(null);
      setRestoreConfirmText('');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 删除备份 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiDeleteBackup(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['backups'] });
      message.success('备份已删除');
      setDeleteTarget(null);
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 汇总栏数据 ----
  const totalSize: string = useMemo(() => formatFileSize(stats?.total_size_bytes ?? 0), [stats]);
  const dailyCount: number = stats?.daily_count ?? 0;
  const milestoneCount: number = stats?.milestone_count ?? 0;

  // ---- 每日镜像表格列定义 ----
  const dailyColumns: ColumnsType<BackupRecordItem> = [
    {
      title: '日期',
      dataIndex: 'backup_date',
      key: 'backup_date',
      width: 120,
      render: (val: string | null) => formatDate(val),
    },
    {
      title: '大小',
      dataIndex: 'file_size',
      key: 'file_size',
      width: 120,
      render: (val: number | null) => formatFileSize(val),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: BackupStatus) => (
        <StatusMark
          semantic={STATUS_SEMANTIC[status] ?? 'neutral'}
          label={STATUS_LABEL[status] ?? status}
        />
      ),
    },
    {
      title: '迁移版本',
      dataIndex: 'migration_version',
      key: 'migration_version',
      width: 140,
      ellipsis: true,
      render: (val: string | null) => val || <Text type="secondary">-</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: BackupRecordItem) => (
        <Button
          type="link"
          size="small"
          danger
          disabled={record.status !== 'succeeded'}
          onClick={() => {
            setRestoreTarget(record);
            setRestoreConfirmText('');
          }}
        >
          回滚
        </Button>
      ),
    },
  ];

  // ---- 里程碑备份表格列定义 ----
  const milestoneColumns: ColumnsType<BackupRecordItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 160,
      ellipsis: true,
      render: (val: string | null) => val || <Text type="secondary">-</Text>,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      width: 220,
      ellipsis: true,
      render: (val: string | null) => val || <Text type="secondary">-</Text>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (val: string) => formatDateTime(val),
    },
    {
      title: '大小',
      dataIndex: 'file_size',
      key: 'file_size',
      width: 100,
      render: (val: number | null) => formatFileSize(val),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: BackupStatus) => (
        <StatusMark
          semantic={STATUS_SEMANTIC[status] ?? 'neutral'}
          label={STATUS_LABEL[status] ?? status}
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_: unknown, record: BackupRecordItem) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            disabled={record.status !== 'succeeded'}
            onClick={() => {
              setRestoreTarget(record);
              setRestoreConfirmText('');
            }}
          >
            恢复
          </Button>
          <Button
            type="link"
            size="small"
            danger
            onClick={() => setDeleteTarget(record)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  // ---- 创建里程碑备份表单提交 ----
  const handleCreateSubmit = (): void => {
    createForm
      .validateFields()
      .then((values: { name: string; description?: string }) => {
        createMutation.mutate({
          type: 'milestone',
          name: values.name,
          description: values.description,
        });
      })
      .catch(() => {
        // 校验失败，不提交
      });
  };

  // ---- 确认恢复 ----
  const handleRestoreConfirm = (): void => {
    if (restoreTarget) {
      restoreMutation.mutate(restoreTarget.id);
    }
  };

  // ---- 确认删除 ----
  const handleDeleteConfirm = (): void => {
    if (deleteTarget) {
      deleteMutation.mutate(deleteTarget.id);
    }
  };

  return (
    <div className="ocean-page-enter">
      {/* ---- 顶部汇总栏 ---- */}
      <div className="ocean-panel" style={{ padding: 20, marginBottom: 16 }}>
        <Row gutter={48}>
          <Col span={8}>
            <Statistic
              title="备份总用量"
              value={totalSize}
              loading={statsLoading}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="每日快照数"
              value={dailyCount}
              suffix={`/ 14`}
              loading={statsLoading}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="里程碑备份数"
              value={milestoneCount}
              loading={statsLoading}
            />
          </Col>
        </Row>
      </div>

      {/* ---- 创建里程碑备份按钮 ---- */}
      <div style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          onClick={() => setCreateModalOpen(true)}
        >
          创建里程碑备份
        </Button>
      </div>

      {/* ---- 备份列表（Tab 切换） ---- */}
      <div className="ocean-panel" style={{ padding: 16 }}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'daily',
              label: '每日镜像',
              children: (
                <DataTableShell bodyPadding={0}>
                  <QueryStateDisplay
                    isLoading={dailyLoading}
                    isError={dailyError}
                    error={dailyErr}
                    isEmpty={!dailyLoading && !dailyError && dailyItems.length === 0}
                    emptyText="暂无每日快照"
                    onRetry={() => void refetchDaily()}
                    loadingTitle="加载每日快照…"
                  >
                    <Table<BackupRecordItem>
                      columns={dailyColumns}
                      dataSource={dailyItems}
                      rowKey="id"
                      pagination={false}
                      size="middle"
                    />
                  </QueryStateDisplay>
                </DataTableShell>
              ),
            },
            {
              key: 'milestone',
              label: '里程碑备份',
              children: (
                <DataTableShell bodyPadding={0}>
                  <QueryStateDisplay
                    isLoading={milestoneLoading}
                    isError={milestoneError}
                    error={milestoneErr}
                    isEmpty={!milestoneLoading && !milestoneError && milestoneItems.length === 0}
                    emptyText="暂无里程碑备份"
                    onRetry={() => void refetchMilestone()}
                    loadingTitle="加载里程碑备份…"
                  >
                    <Table<BackupRecordItem>
                      columns={milestoneColumns}
                      dataSource={milestoneItems}
                      rowKey="id"
                      pagination={{ pageSize: 20, showSizeChanger: true }}
                      size="middle"
                    />
                  </QueryStateDisplay>
                </DataTableShell>
              ),
            },
          ]}
        />
      </div>

      {/* ---- 创建里程碑备份 Modal ---- */}
      <Modal
        title="创建里程碑备份"
        open={createModalOpen}
        onOk={handleCreateSubmit}
        onCancel={() => {
          setCreateModalOpen(false);
          createForm.resetFields();
        }}
        okText="创建备份"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
      >
        <Form form={createForm} layout="vertical" preserve={false}>
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: '请输入里程碑名称' },
              { max: 100, message: '名称不超过 100 字符' },
            ]}
          >
            <Input placeholder="如：v0.3 发布前备份" maxLength={100} showCount />
          </Form.Item>
          <Form.Item
            name="description"
            label="描述"
            rules={[
              { max: 500, message: '描述不超过 500 字符' },
            ]}
          >
            <Input.TextArea
              placeholder="如：版本发布前的完整数据库备份"
              maxLength={500}
              showCount
              rows={3}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* ---- 回滚/恢复确认 Modal ---- */}
      <Modal
        title="⚠️ 危险操作确认"
        open={restoreTarget !== null}
        onOk={handleRestoreConfirm}
        onCancel={() => {
          setRestoreTarget(null);
          setRestoreConfirmText('');
        }}
        okText="执行回滚"
        cancelText="取消"
        okButtonProps={{
          danger: true,
          disabled: restoreConfirmText !== '确认回滚',
          loading: restoreMutation.isPending,
        }}
        width={480}
      >
        {restoreTarget && (
          <div>
            <p style={{ fontSize: 14, lineHeight: 1.8 }}>
              您即将将数据库回滚到{' '}
              <Tag color={TYPE_TAG_COLOR[restoreTarget.backup_type]}>
                {TYPE_LABEL[restoreTarget.backup_type]}
              </Tag>{' '}
              <Text strong>{restoreTarget.name ?? formatDate(restoreTarget.backup_date)}</Text>
              的状态。
            </p>
            <p style={{ color: '#cf1322', fontSize: 14, lineHeight: 1.8 }}>
              此操作将覆盖当前所有数据，不可撤销！
            </p>
            <p style={{ fontSize: 14, lineHeight: 1.8 }}>
              系统会在回滚前自动备份当前状态（pre_restore 类型，保留 7 天）。
            </p>
            <p style={{ marginTop: 16 }}>
              请输入 <Text strong mark>"确认回滚"</Text> 以继续：
            </p>
            <Input
              value={restoreConfirmText}
              onChange={(e) => setRestoreConfirmText(e.target.value)}
              placeholder="确认回滚"
            />
          </div>
        )}
      </Modal>

      {/* ---- 删除里程碑备份确认 Modal ---- */}
      <Modal
        title="删除里程碑备份"
        open={deleteTarget !== null}
        onOk={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: deleteMutation.isPending }}
        width={420}
      >
        {deleteTarget && (
          <p style={{ fontSize: 14, lineHeight: 1.8 }}>
            确定要删除里程碑备份{' '}
            <Text strong>{deleteTarget.name}</Text>
            ？此操作将永久删除备份文件，不可恢复。
          </p>
        )}
      </Modal>
    </div>
  );
}

export default DatabaseBackupPage;
