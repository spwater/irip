/**
 * 研究分析首页（Workspace 列表）
 *
 * 展示当前用户的 Workspace 列表，支持活跃/归档筛选、搜索、排序。
 * 新用户看到空状态引导 + "新建 Workspace" 主操作按钮。
 */
import { useCallback, useEffect, useState } from 'react';
import { Button, Empty, Input, Row, Col, Segmented, Spin, message } from 'antd';
import { PlusOutlined, SearchOutlined } from '@ant-design/icons';
import {
  apiListWorkspaces,
  type Workspace,
} from '@/api/research';
import { WorkspaceCard } from './WorkspaceCard';
import { CreateWorkspaceModal } from './CreateWorkspaceModal';
import { WorkspaceDetail } from './WorkspaceDetail';

type StatusFilter = 'all' | 'draft' | 'archived';

export function ResearchPage(): JSX.Element {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [searchText, setSearchText] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);

  const fetchWorkspaces = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: { status?: string; page_size?: number } = { page_size: 100 };
      if (statusFilter !== 'all') {
        params.status = statusFilter;
      }
      const res = await apiListWorkspaces(params);
      let items = res.items;
      if (searchText.trim()) {
        const q = searchText.trim().toLowerCase();
        items = items.filter((w) => w.name.toLowerCase().includes(q));
      }
      setWorkspaces(items);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败';
      setError(msg);
      setWorkspaces([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, searchText]);

  useEffect(() => {
    void fetchWorkspaces();
  }, [fetchWorkspaces]);

  const handleCreated = useCallback(() => {
    setModalOpen(false);
    void fetchWorkspaces();
  }, [fetchWorkspaces]);

  if (selectedWorkspaceId) {
    return (
      <WorkspaceDetail
        workspaceId={selectedWorkspaceId}
        onBack={() => {
          setSelectedWorkspaceId(null);
          void fetchWorkspaces();
        }}
      />
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <Segmented
            value={statusFilter}
            onChange={(val) => setStatusFilter(val as StatusFilter)}
            options={[
              { label: '全部', value: 'all' },
              { label: '活跃', value: 'draft' },
              { label: '归档', value: 'archived' },
            ]}
          />
          <Input
            placeholder="搜索工作空间名称"
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            style={{ width: 240 }}
            allowClear
          />
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          新建 Workspace
        </Button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : error ? (
        <Empty description={`加载失败：${error}`} />
      ) : workspaces.length === 0 ? (
        <Empty description="暂无研究工作空间，点击「新建 Workspace」开始">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            新建 Workspace
          </Button>
        </Empty>
      ) : (
        <Row gutter={[16, 16]}>
          {workspaces.map((ws) => (
            <Col key={ws.workspace_id} xs={24} sm={12} md={8} lg={6}>
              <WorkspaceCard
                workspace={ws}
                onClick={() => setSelectedWorkspaceId(ws.workspace_id)}
              />
            </Col>
          ))}
        </Row>
      )}

      <CreateWorkspaceModal open={modalOpen} onClose={() => setModalOpen(false)} onCreated={handleCreated} />
    </div>
  );
}
