import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Button, Card, Col, Empty, Radio, Row, Select, Space, Tag, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { apiListExperimentProjects, type ExperimentProjectListItem } from '@/api/experiment-projects';
import { apiListDepartments } from '@/api/departments';
import { CreateProjectModal } from './CreateProjectModal';

const { Text } = Typography;

/**
 * 实验项目列表页 — 卡片网格视图
 *
 * 功能：
 * - 项目卡片网格（Ant Design Card），每张卡片展示：项目名称、编码、任务数量、状态标签、所属部门
 * - 活跃/归档切换（Radio）
 * - 部门筛选（DepartmentSelector 风格下拉）
 * - 「新建项目」按钮 → 打开 CreateProjectModal
 * - 点击卡片 → navigate(?tab=flows&project=${project_id})
 * - 潮线 UI 风格（ocean-* CSS 类）
 */
export function ProjectList(): JSX.Element {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<string>('active');
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [createModalOpen, setCreateModalOpen] = useState(false);

  // 部门列表查询（用于筛选下拉）
  const { data: deptListData } = useQuery({
    queryKey: ['departments-for-project-list'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptOptions = (deptListData?.items ?? []).map((d) => ({
    value: d.id,
    label: d.display_name,
  }));

  // 项目列表查询
  const { data, isLoading } = useQuery({
    queryKey: ['experiment-projects', statusFilter],
    queryFn: () =>
      apiListExperimentProjects({ status: statusFilter }),
  });

  const projects: ExperimentProjectListItem[] = data?.items ?? [];

  // 部门筛选（前端过滤，数据量不大）
  const filteredProjects = deptFilter
    ? projects.filter((p) => p.department_id === deptFilter)
    : projects;

  const handleCardClick = (projectId: string): void => {
    void navigate({
      to: '/lab-ops',
      search: { tab: 'flows', project: projectId },
    });
  };

  return (
    <div className="ocean-page-enter">
      <Space style={{ marginBottom: 16, alignItems: 'center' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateModalOpen(true)}
        >
          新建项目
        </Button>
        <Radio.Group
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as string)}
          optionType="button"
          buttonStyle="solid"
        >
          <Radio.Button value="active">活跃</Radio.Button>
          <Radio.Button value="archived">归档</Radio.Button>
        </Radio.Group>
        <Select
          placeholder="所属单位筛选"
          style={{ width: 200 }}
          value={deptFilter ?? '__all__'}
          onChange={(val: string) => setDeptFilter(val === '__all__' ? undefined : val)}
          options={[{ value: '__all__', label: '全部' }, ...deptOptions]}
        />
      </Space>

      {isLoading ? (
        <Card loading={true} />
      ) : filteredProjects.length === 0 ? (
        <Empty description={statusFilter === 'archived' ? '暂无归档项目' : '暂无活跃项目'} style={{ padding: 60 }} />
      ) : (
        <Row gutter={[16, 16]}>
          {filteredProjects.map((project) => (
            <Col xs={24} sm={12} md={8} lg={6} key={project.id}>
              <Card
                hoverable
                onClick={() => handleCardClick(project.id)}
                style={{
                  opacity: project.status === 'archived' ? 0.6 : 1,
                  borderRadius: 8,
                  border: '1px solid var(--ocean-border-subtle)',
                }}
              >
                <div style={{ marginBottom: 8 }}>
                  <Text strong style={{ fontSize: 16 }}>{project.display_name}</Text>
                </div>
                <div style={{ marginBottom: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    code: {project.code}
                  </Text>
                </div>
                {project.description && (
                  <div
                    style={{
                      marginBottom: 8,
                      maxHeight: 40,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {project.description}
                    </Text>
                  </div>
                )}
                <Space size={4} style={{ marginBottom: 8 }}>
                  <Tag
                    color={project.status === 'active' ? 'green' : 'default'}
                    style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}
                  >
                    {project.status === 'active' ? '活跃' : '归档'}
                  </Tag>
                  <Tag
                    color="blue"
                    style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}
                  >
                    {project.task_count} 个任务
                  </Tag>
                  <Tag
                    color="cyan"
                    style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}
                  >
                    {project.fact_count} 条数据
                  </Tag>
                </Space>
                {project.department_name && (
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {project.department_name}
                      {project.owner_display_name ? ` · ${project.owner_display_name}` : ''}
                    </Text>
                  </div>
                )}
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <CreateProjectModal open={createModalOpen} onClose={() => setCreateModalOpen(false)} />
    </div>
  );
}

export default ProjectList;
