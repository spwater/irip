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
import { apiListUnifiedTools, apiToggleAITool } from '@/api/models-ai';
import type { UnifiedToolDTO } from '@/api/models-ai';
import { extractApiError } from '@/api/types';
import type { AIToolDTO } from './types';
import type { ToolFilter } from './types';
import { ToolEditDrawer } from './ToolEditDrawer';

const { Title, Text } = Typography;

/**
 * AI 工具与插件管理页面
 *
 * 展示 ai_tool 表中的全部工具：
 * - AI 工具白名单（小艾对话调用的只读工具）
 * - 候选工具（需审批的写操作建议）
 * - 插件工具（XRD 解析器等专门编写的工具，可编辑描述）
 *
 * 功能：
 * - 类型/状态筛选 + 名称搜索；
 * - 启用/禁用开关（二次确认）+ 编辑按钮。
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

  const filteredTools = useMemo<UnifiedToolDTO[]>(() => {
    const all = tools ?? [];
    return all.filter((t) => {
      // 类型筛选
      if (filter.type === 'whitelist' && t.candidate) return false;
      if (filter.type === 'candidate' && !t.candidate) return false;
      // 状态筛选
      if (filter.status === 'enabled' && !t.enabled) return false;
      if (filter.status === 'disabled' && t.enabled) return false;
      // 关键词搜索
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

  const columns: ColumnsType<UnifiedToolDTO> = [
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
      render: (_: unknown, r: UnifiedToolDTO) => {
        return r.candidate ? (
          <Tag color="orange">候选</Tag>
        ) : (
          <Tag color="blue">只读</Tag>
        );
      },
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

  return (
    <div>
      <Title level={5}>AI 工具与插件管理</Title>
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
