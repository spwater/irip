import { useMemo, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import {
  Button,
  Input,
  Modal,
  Popconfirm,
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
import { apiArchiveComponent, apiRestoreComponent } from '@/api/equipment-flows';
import { extractApiError } from '@/api/types';
import type { AIToolDTO } from './types';
import type { ToolFilter } from './types';
import { ToolEditDrawer } from './ToolEditDrawer';

const { Title, Text } = Typography;

/**
 * AI 工具与插件管理页面（统一视图）
 *
 * 汇总展示两套工具体系：
 * - AI 工具白名单（ai_tool 表，小艾对话调用）
 * - 组件插件（component 表，流程引擎调用）
 *
 * 功能：
 * - 统一列表，"来源"列区分 AI 工具（蓝色）和组件插件（绿色）；
 * - 来源/类型/状态筛选 + 名称搜索；
 * - AI 工具：启用/禁用开关（二次确认）+ 编辑按钮；
 * - 组件插件：只读展示（开关仅显示状态，无编辑按钮）。
 *
 * 仅 platform_administrator 可见（由 PlatformPage Tab 条件渲染保证），
 * 后端端点另由 system:manage 权限守卫。
 */
export function AIToolsPage(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<AIToolDTO | null>(null);
  const [filter, setFilter] = useState<ToolFilter>({
    source: 'all',
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

  // ---- 组件归档 Mutation ----
  const archiveCompMutation = useMutation({
    mutationFn: (componentId: string) => apiArchiveComponent(componentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-tools'] });
      message.success('组件已归档');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 组件恢复 Mutation ----
  const restoreCompMutation = useMutation({
    mutationFn: (componentId: string) => apiRestoreComponent(componentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-tools'] });
      message.success('组件已恢复');
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

  /**
   * 将 UnifiedToolDTO（AI 工具）转换为 AIToolDTO，
   * 供 ToolEditDrawer 使用。
   */
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
      // 来源筛选
      if (filter.source === 'ai_tool' && t.source !== 'ai_tool') return false;
      if (filter.source === 'component' && t.source !== 'component') return false;
      // 类型筛选（仅对 AI 工具有效）
      if (t.source === 'ai_tool') {
        if (filter.type === 'whitelist' && t.candidate) return false;
        if (filter.type === 'candidate' && !t.candidate) return false;
      }
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
      title: '来源',
      key: 'source',
      width: 110,
      render: (_: unknown, r: UnifiedToolDTO) =>
        r.source === 'ai_tool' ? (
          <Tag color="blue">AI 工具</Tag>
        ) : (
          <Tag color="green">组件插件</Tag>
        ),
    },
    {
      title: '类型',
      key: 'type',
      width: 100,
      render: (_: unknown, r: UnifiedToolDTO) => {
        if (r.source === 'ai_tool') {
          return r.candidate ? (
            <Tag color="orange">候选</Tag>
          ) : (
            <Tag color="blue">只读</Tag>
          );
        }
        return <Tag color="cyan">{r.kind}</Tag>;
      },
    },
    {
      title: '状态',
      key: 'enabled',
      width: 90,
      render: (_: unknown, r: UnifiedToolDTO) =>
        r.source === 'ai_tool' ? (
          <Switch
            checked={r.enabled}
            onChange={(v) => handleToggle(r, v)}
            loading={toggleMutation.isPending}
          />
        ) : (
          <Switch checked={r.enabled} disabled />
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
      width: 140,
      render: (_: unknown, r: UnifiedToolDTO) => {
        if (r.source === 'ai_tool') {
          return (
            <Button size="small" onClick={() => handleEdit(r)}>
              编辑
            </Button>
          );
        }
        // 组件插件：归档/恢复
        if (r.status === 'deprecated') {
          return (
            <Space size="small">
              <Button
                type="link"
                size="small"
                onClick={() => void navigate({ to: '/platform', search: { tab: 'components', edit_id: r.version_id } })}
              >
                编辑
              </Button>
              <Popconfirm
                title="确定恢复该组件？"
                onConfirm={() => restoreCompMutation.mutate(r.component_id)}
                okText="恢复"
                cancelText="取消"
              >
                <Button
                  type="link"
                  size="small"
                  loading={restoreCompMutation.isPending}
                >
                  恢复
                </Button>
              </Popconfirm>
            </Space>
          );
        }
        return (
          <Space size="small">
            <Button
              type="link"
              size="small"
              onClick={() => void navigate({ to: '/platform', search: { tab: 'components', edit_id: r.version_id } })}
            >
              编辑
            </Button>
            <Popconfirm
              title="确定归档该组件？"
              onConfirm={() => archiveCompMutation.mutate(r.component_id)}
              okText="归档"
              cancelText="取消"
            >
              <Button
                type="link"
                size="small"
                danger
                loading={archiveCompMutation.isPending}
              >
                归档
              </Button>
            </Popconfirm>
          </Space>
        );
      },
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
          style={{ width: 140 }}
          value={filter.source}
          onChange={(v) => setFilter((f) => ({ ...f, source: v }))}
          options={[
            { value: 'all', label: '全部来源' },
            { value: 'ai_tool', label: 'AI 工具' },
            { value: 'component', label: '组件插件' },
          ]}
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
