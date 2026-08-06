/**
 * Workspace 三栏布局容器
 *
 * 左栏：EvidencePanel（数据搜索 + 列表 + 冻结）
 * 中栏：ResearchCanvas（研究问题 + 子问题）
 * 右栏：AiAssistantPanel（占位）
 */
import { useEffect, useState } from 'react';
import { Button, Row, Col, Spin, Modal, Input, message, Popconfirm, Tag } from 'antd';
import { ArrowLeftOutlined, ForkOutlined, DeleteOutlined, InboxOutlined } from '@ant-design/icons';
import { apiGetWorkspace, apiForkWorkspace, apiDeleteWorkspace, apiArchiveWorkspace, type WorkspaceDetail as WorkspaceDetailType } from '@/api/research';
import { EvidencePanel } from './EvidencePanel';
import { ResearchCanvas } from './ResearchCanvas';
import { AiAssistantPanel } from './AiAssistantPanel';

interface WorkspaceDetailProps {
  workspaceId: string;
  onBack: () => void;
}

export function WorkspaceDetail({ workspaceId, onBack }: WorkspaceDetailProps): JSX.Element {
  const [detail, setDetail] = useState<WorkspaceDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [forkModalOpen, setForkModalOpen] = useState(false);
  const [forkName, setForkName] = useState('');
  const [forking, setForking] = useState(false);

  const fetchDetail = async () => {
    setLoading(true);
    try {
      const data = await apiGetWorkspace(workspaceId);
      setDetail(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败';
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const handleFork = async () => {
    if (!forkName.trim()) {
      message.warning('请输入新工作空间名称');
      return;
    }
    setForking(true);
    try {
      await apiForkWorkspace(workspaceId, { new_name: forkName.trim() });
      message.success('分叉成功');
      setForkModalOpen(false);
      setForkName('');
      onBack();
    } catch {
      message.error('分叉失败');
    } finally {
      setForking(false);
    }
  };

  const handleArchive = async () => {
    try {
      await apiArchiveWorkspace(workspaceId);
      message.success('已归档');
      onBack();
    } catch (err: any) {
      const msg = err?.response?.data?.error?.message || '归档失败';
      message.error(msg);
    }
  };

  const handleDelete = async () => {
    try {
      await apiDeleteWorkspace(workspaceId);
      message.success('已删除');
      onBack();
    } catch (err: any) {
      const msg = err?.response?.data?.error?.message || '删除失败（可能存在发布成果引用，请先归档）';
      message.error(msg);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!detail) {
    return <div style={{ padding: 24 }}>工作空间不存在</div>;
  }

  return (
    <div style={{ padding: 16 }}>
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center' }}>
        <Button icon={<ArrowLeftOutlined />} type="text" onClick={onBack}>
          返回列表
        </Button>
        <span style={{ fontSize: 18, fontWeight: 600, marginLeft: 8 }}>{detail.name}</span>
        <Button
          icon={<ForkOutlined />}
          size="small"
          style={{ marginLeft: 12 }}
          onClick={() => {
            setForkName(`${detail.name} - 分叉`);
            setForkModalOpen(true);
          }}
        >
          分叉
        </Button>
        <span style={{ marginLeft: 12 }}>
          <Tag color={detail.status === 'draft' ? 'blue' : 'default'}>
            {detail.status === 'draft' ? '活跃' : '已归档'}
          </Tag>
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {detail.status === 'draft' && (
            <Popconfirm
              title="归档后可在列表归档筛选下查看，不可恢复为活跃"
              onConfirm={handleArchive}
              okText="确认归档"
              cancelText="取消"
            >
              <Button size="small" icon={<InboxOutlined />}>
                归档
              </Button>
            </Popconfirm>
          )}
          <Popconfirm
            title="删除后不可恢复，仅无发布成果引用的工作空间可删除"
            onConfirm={handleDelete}
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </div>
      </div>
      <Row gutter={16}>
        <Col xs={24} lg={6}>
          <EvidencePanel
            workspaceId={workspaceId}
            evidenceCount={detail.evidence_count}
            onEvidenceChanged={fetchDetail}
          />
        </Col>
        <Col xs={24} lg={12}>
          <ResearchCanvas workspaceId={workspaceId} detail={detail} onQuestionUpdated={fetchDetail} />
        </Col>
        <Col xs={24} lg={6}>
          <AiAssistantPanel />
        </Col>
      </Row>

      {/* 分叉弹窗 */}
      <Modal
        title="分叉工作空间"
        open={forkModalOpen}
        onOk={handleFork}
        onCancel={() => setForkModalOpen(false)}
        confirmLoading={forking}
        okText="确认分叉"
        cancelText="取消"
      >
        <div style={{ marginBottom: 8, color: 'var(--ocean-text-muted)', fontSize: 13 }}>
          分叉将继承当前工作空间的主研究问题（最新版本）和数据引用列表（副本），后续独立运行。
        </div>
        <Input
          placeholder="新工作空间名称"
          value={forkName}
          onChange={(e) => setForkName(e.target.value)}
          onPressEnter={handleFork}
        />
      </Modal>
    </div>
  );
}
