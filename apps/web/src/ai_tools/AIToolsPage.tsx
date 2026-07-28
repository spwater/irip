import { useMemo, useState } from 'react';
import {
  Button,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiListAITools,
  apiToggleAITool,
  extractApiError,
} from '@/api/client';
import type { AIToolDTO } from './types';
import type { ToolFilter } from './types';
import { ToolEditDrawer } from './ToolEditDrawer';

const { Title, Text } = Typography;

/**
 * AI 工具管理页面
 *
 * 功能：工具列表、类型/状态筛选、名称搜索、启用/禁用开关（二次确认）、
 * 编辑/新建按钮（打开抽屉）。
 *
 * 仅 platform_administrator 可见（由 PlatformPage Tab 条件渲染保证），
 * 后端端点另由 system:manage 权限守卫。
 */
export function AIToolsPage(): JSX.Element {
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<AIToolDTO | null>(null);
  const [filter, setFilter] = useState<ToolFilter>({
    type: 'all',
    status: 'all',
    keyword: '',
  });

  const { data: tools, isLoading } = useQuery({
    queryKey: ['ai-tools'],
    queryFn: apiListAITools,
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

  const handleToggle = (tool: AIToolDTO, newEnabled: boolean): void => {
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

  const handleEdit = (tool: AIToolDTO): void => {
    setEditingTool(tool);
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

  const filteredTools = useMemo<AIToolDTO[]>(() => {
    const all = tools ?? [];
    return all.filter((t) => {
      if (filter.type === 'whitelist' && t.candidate) return false;
      if (filter.type === 'candidate' && !t.candidate) return false;
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
  }, [tools, filter]);

  const columns: ColumnsType<AIToolDTO> = [
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
      title: '类型',
      key: 'type',
      width: 100,
      render: (_: unknown, r: AIToolDTO) =>
        r.candidate ? (
          <Tag color="orange">候选</Tag>
        ) : (
          <Tag color="blue">只读</Tag>
        ),
    },
    {
      title: '状态',
      key: 'enabled',
      width: 90,
      render: (_: unknown, r: AIToolDTO) => (
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
      render: (_: unknown, r: AIToolDTO) => (
        <Button size="small" onClick={() => handleEdit(r)}>
          编辑
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Title level={5}>AI 工具管理</Title>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
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
          value={filter.type}
          onChange={(v) => setFilter((f) => ({ ...f, type: v }))}
          options={[
            { value: 'all', label: '全部类型' },
            { value: 'whitelist', label: '只读' },
            { value: 'candidate', label: '候选' },
          ]}
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
        columns={columns}
        dataSource={filteredTools}
        rowKey="name"
        loading={isLoading}
        pagination={false}
        size="middle"
      />
      <ToolEditDrawer
        open={drawerOpen}
        tool={editingTool}
        onClose={handleCloseDrawer}
      />
    </div>
  );
}
