import { useMemo, useState } from 'react';
import {
  Button,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiListUnifiedTools, apiToggleAITool } from '@/api/models-ai';
import type { UnifiedToolDTO } from '@/api/models-ai';
import { extractApiError } from '@/api/types';
import type { AIToolDTO } from './types';
import type { ToolFilter } from './types';
import { ToolEditDrawer } from './ToolEditDrawer';
import { BuiltinToolEditDrawer } from './BuiltinToolEditDrawer';

const { Text } = Typography;

/**
 * 工具插件管理页面
 *
 * 两个分区：
 * - AItool：ai_tool 表中 category=ai_tool 的工具，可启用/禁用/编辑
 * - 内置工具：category=ingestion 等内置插件，只读列表 + 仅编辑描述
 */
export function AIToolsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<'ai_tool' | 'builtin'>('ai_tool');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<AIToolDTO | null>(null);
  const [builtinDrawerOpen, setBuiltinDrawerOpen] = useState(false);
  const [editingBuiltin, setEditingBuiltin] = useState<AIToolDTO | null>(null);
  const [filter, setFilter] = useState<ToolFilter>({
    status: 'all',
    keyword: '',
  });

  const { data: tools, isLoading } = useQuery({
    queryKey: ['ai-tools'],
    queryFn: apiListUnifiedTools,
  });

  const toggleMutation = useMutation({
    mutationFn: (vars: {
      name: string;
      enabled: boolean;
      lock_version: number;
    }) =>
      apiToggleAITool(vars.name, {
        enabled: vars.enabled,
        lock_version: vars.lock_version,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-tools'] });
      void queryClient.invalidateQueries({ queryKey: ['assistant-provider-status'] });
      message.success('工具状态已更新，下次小艾对话即生效');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleToggle = (tool: UnifiedToolDTO, newEnabled: boolean): void => {
    Modal.confirm({
      title: newEnabled ? '启用工具' : '禁用工具',
      content: newEnabled
        ? `确定启用工具「${tool.display_name}」吗？启用后小艾可调用此工具。`
        : `确定禁用工具「${tool.display_name}」吗？禁用后小艾将无法调用此工具，进行中的对话也会立即生效。`,
      okText: '确定',
      cancelText: '取消',
      okButtonProps: { danger: !newEnabled },
      onOk: () => {
        toggleMutation.mutate({
          name: tool.name,
          enabled: newEnabled,
          lock_version: tool.lock_version,
        });
      },
    });
  };

  const toAIToolDTO = (tool: UnifiedToolDTO): AIToolDTO => ({
    name: tool.name,
    display_name: tool.display_name,
    description: tool.description,
    required_permission: tool.required_permission,
    candidate: tool.candidate,
    parameters_schema: tool.parameters_schema,
    enabled: tool.enabled,
    lock_version: tool.lock_version,
    updated_at: tool.updated_at,
    updated_by: tool.updated_by,
  });

  const handleEdit = (tool: UnifiedToolDTO): void => {
    setEditingTool(toAIToolDTO(tool));
    setDrawerOpen(true);
  };

  const handleCreate = (): void => {
    setEditingTool(null);
    setDrawerOpen(true);
  };

  const handleCloseDrawer = (): void => {
    setDrawerOpen(false);
    setEditingTool(null);
  };

  const handleEditBuiltin = (tool: UnifiedToolDTO): void => {
    setEditingBuiltin(toAIToolDTO(tool));
    setBuiltinDrawerOpen(true);
  };

  const handleCloseBuiltinDrawer = (): void => {
    setBuiltinDrawerOpen(false);
    setEditingBuiltin(null);
  };

  // 按分类分组
  const aiTools = useMemo(
    () => (tools ?? []).filter((t) => t.category === 'ai_tool'),
    [tools],
  );
  const builtinTools = useMemo(
    () => (tools ?? []).filter((t) => t.category !== 'ai_tool'),
    [tools],
  );

  // AItool 筛选
  const filteredAiTools = useMemo(() => {
    return aiTools.filter((t) => {
      if (filter.status === 'enabled' && !t.enabled) return false;
      if (filter.status === 'disabled' && t.enabled) return false;
      if (filter.keyword.trim()) {
        const kw = filter.keyword.trim().toLowerCase();
        if (
          !t.name.toLowerCase().includes(kw) &&
          !t.display_name.toLowerCase().includes(kw)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [aiTools, filter]);

  // AItool 列定义（含状态开关）
  const aiColumns: ColumnsType<UnifiedToolDTO> = [
    {
      title: '工具名',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string) => <Text code>{name}</Text>,
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 160,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '状态',
      key: 'enabled',
      width: 90,
      render: (_: unknown, r: UnifiedToolDTO) => (
        <Switch
          checked={r.enabled}
          onChange={(v) => handleToggle(r, v)}
          loading={toggleMutation.isPending}
        />
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (v: string) =>
        v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, r: UnifiedToolDTO) => (
        <Button size="small" onClick={() => handleEdit(r)}>
          编辑
        </Button>
      ),
    },
  ];

  // 内置工具列定义（无状态开关，仅编辑描述+显示名）
  const builtinColumns: ColumnsType<UnifiedToolDTO> = [
    {
      title: '工具名',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string) => <Text code>{name}</Text>,
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 160,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 120,
      render: (cat: string) => <Tag color="cyan">{cat}</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, r: UnifiedToolDTO) => (
        <Button size="small" onClick={() => handleEditBuiltin(r)}>
          编辑
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Segmented
        value={activeTab}
        onChange={(v) => setActiveTab(v as 'ai_tool' | 'builtin')}
        options={[
          { label: 'AItool', value: 'ai_tool' },
          { label: '内置工具', value: 'builtin' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {activeTab === 'ai_tool' && (
        <>
          <Space style={{ marginBottom: 16 }} wrap>
            <Input
              prefix={<SearchOutlined />}
              placeholder="搜索工具名 / 显示名"
              allowClear
              style={{ width: 240 }}
              value={filter.keyword}
              onChange={(e) =>
                setFilter((f) => ({ ...f, keyword: e.target.value }))
              }
            />
            <Select
              style={{ width: 120 }}
              value={filter.status}
              onChange={(v) => setFilter((f) => ({ ...f, status: v }))}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'enabled', label: '已启用' },
                { value: 'disabled', label: '已禁用' },
              ]}
            />
            <Button type="primary" onClick={handleCreate}>
              新建工具
            </Button>
          </Space>
          <Table
            columns={aiColumns}
            dataSource={filteredAiTools}
            rowKey="name"
            loading={isLoading}
            pagination={false}
            size="middle"
          />
        </>
      )}

      {activeTab === 'builtin' && (
        <Table
          columns={builtinColumns}
          dataSource={builtinTools}
          rowKey="name"
          loading={isLoading}
          pagination={false}
          size="middle"
        />
      )}

      <ToolEditDrawer
        open={drawerOpen}
        tool={editingTool}
        onClose={handleCloseDrawer}
      />
      <BuiltinToolEditDrawer
        open={builtinDrawerOpen}
        tool={editingBuiltin}
        onClose={handleCloseBuiltinDrawer}
      />
    </div>
  );
}
