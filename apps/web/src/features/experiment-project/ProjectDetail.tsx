import { useState, useMemo } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Tag, TreeSelect, Typography, message } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiGetExperimentProject,
  apiUpdateExperimentProject,
  apiUpdateExperimentProjectStatus,
  apiDeleteExperimentProject,
} from '@/api/experiment-projects';
import { apiListDepartments } from '@/api/departments';
import { apiListUsers } from '@/api/governance';
import { buildDeptTree } from '@/shared/buildDeptTree';
import { extractApiError } from '@/api/types';
import { FlowDetail } from '@/features/components/FlowDetail';

const { Text, Paragraph } = Typography;

/**
 * 实验项目详情页
 *
 * 布局：
 * - 顶部项目信息区：返回按钮、名称、编码、描述、负责人、状态标签 + 编辑/归档按钮
 * - 编辑按钮 → Modal 表单（名称/描述）
 * - 归档按钮 → Popconfirm → apiUpdateExperimentProjectStatus
 * - 内嵌 FlowDetail 组件，传 projectId props（任务列表筛选绑定 project_id）
 *
 * 风格参考潮线 UI（ocean-* CSS 类）。
 */
export function ProjectDetail({ projectId }: { projectId: string }): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editForm] = Form.useForm();

  // 项目详情查询
  const { data: project, isLoading } = useQuery({
    queryKey: ['experiment-project', projectId],
    queryFn: () => apiGetExperimentProject(projectId),
    enabled: !!projectId,
  });

  // 部门列表（用于显示部门名）
  const { data: deptListData } = useQuery({
    queryKey: ['departments-for-project-detail'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptMap = new Map<string, string>(
    (deptListData?.items ?? []).map((d) => [d.id, d.display_name]),
  );
  const deptTreeData = useMemo(
    () => buildDeptTree(deptListData?.items ?? []),
    [deptListData],
  );

  // 用户列表（用于负责人选择）
  const { data: userData } = useQuery({
    queryKey: ['users-for-project-detail'],
    queryFn: () => apiListUsers({ limit: 100 }),
  });
  const userOptions = (userData?.items ?? []).map((u) => ({
    value: u.id,
    label: `${u.display_name}（${u.email}）`,
  }));

  // 编辑 Mutation
  const updateMutation = useMutation({
    mutationFn: (vars: {
      display_name: string;
      description: string | null;
      visible_departments: string[];
      owner_user_id: string | null;
      lock_version: number;
    }) =>
      apiUpdateExperimentProject(projectId, {
        display_name: vars.display_name,
        description: vars.description,
        visible_departments: vars.visible_departments,
        lock_version: vars.lock_version,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['experiment-project', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['experiment-projects'] });
      setEditModalOpen(false);
      editForm.resetFields();
      message.success('项目信息已更新');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // 归档/恢复 Mutation
  const statusMutation = useMutation({
    mutationFn: (vars: { status: string; lock_version: number }) =>
      apiUpdateExperimentProjectStatus(projectId, vars),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['experiment-project', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['experiment-projects'] });
      message.success(project?.status === 'active' ? '项目已归档' : '项目已恢复');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleBack = (): void => {
    void navigate({ to: '/lab-ops', search: { tab: 'flows' } });
  };

  const handleEdit = async (): Promise<void> => {
    try {
      const values = await editForm.validateFields();
      if (project) {
        updateMutation.mutate({
          display_name: values.display_name as string,
          description: (values.description as string) ?? null,
          visible_departments: (values.visible_departments as string[]) ?? [],
          owner_user_id: (values.owner_user_id as string) ?? null,
          lock_version: project.lock_version,
        });
      }
    } catch {
      // 校验失败
    }
  };

  const openEditModal = (): void => {
    if (project) {
      editForm.setFieldsValue({
        display_name: project.display_name,
        description: project.description ?? undefined,
        visible_departments: project.visible_departments ?? [],
        owner_user_id: project.owner_user_id,
      });
    }
    setEditModalOpen(true);
  };

  if (isLoading || !project) {
    return <Card loading={isLoading} />;
  }

  const isArchived = project.status === 'archived';
  const deptName = deptMap.get(project.department_id) ?? '';

  return (
    <div className="ocean-page-enter">
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={handleBack}>
          返回项目列表
        </Button>
      </Space>

      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <Space align="center" style={{ marginBottom: 8 }}>
              <Text strong style={{ fontSize: 20 }}>{project.display_name}</Text>
              <Text type="secondary" style={{ fontSize: 14 }}>({project.code})</Text>
              <Tag
                color={isArchived ? 'default' : 'green'}
                style={{ margin: 0, padding: '2px 10px', borderRadius: 4 }}
              >
                {isArchived ? '归档' : '活跃'}
              </Tag>
            </Space>
            {project.description && (
              <Paragraph type="secondary" style={{ marginBottom: 8 }}>
                {project.description}
              </Paragraph>
            )}
            <Space size={16}>
              <Text type="secondary" style={{ fontSize: 13 }}>
                任务数: {project.task_count}
              </Text>
              <Text type="secondary" style={{ fontSize: 13, marginLeft: 16 }}>
                数据数: {project.fact_count}
              </Text>
              {deptName && (
                <Text type="secondary" style={{ fontSize: 13 }}>
                  所属单位: {deptName}
                </Text>
              )}
              {project.owner_display_name && (
                <Text type="secondary" style={{ fontSize: 13 }}>
                  负责人: {project.owner_display_name}
                </Text>
              )}
            </Space>
          </div>
          <Space>
            <Button onClick={openEditModal}>编辑</Button>
            {isArchived ? (
              <Popconfirm
                title="确定恢复该项目？"
                description="恢复后可在项目内创建新任务。"
                onConfirm={() =>
                  statusMutation.mutate({
                    status: 'active',
                    lock_version: project.lock_version,
                  })
                }
                okText="恢复"
                cancelText="取消"
              >
                <Button>恢复</Button>
              </Popconfirm>
            ) : (
              <Popconfirm
                title="确定归档该项目？"
                description="归档后项目内任务将变为只读，不可新建任务。"
                onConfirm={() =>
                  statusMutation.mutate({
                    status: 'archived',
                    lock_version: project.lock_version,
                  })
                }
                okText="归档"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button danger>归档</Button>
              </Popconfirm>
            )}
            {isArchived && (
              <Popconfirm
                title="确定删除该项目？"
                description="将同时删除项目下的所有任务，删除后不可恢复。"
                onConfirm={async () => {
                  try {
                    await apiDeleteExperimentProject(projectId);
                    message.success('项目已删除');
                    handleBack();
                  } catch (err) {
                    message.error(extractApiError(err));
                  }
                }}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button danger>删除</Button>
              </Popconfirm>
            )}
          </Space>
        </div>
      </Card>

      {/* 内嵌 FlowDetail 组件，传 projectId 绑定任务列表筛选 */}
      <FlowDetail projectId={projectId} projectStatus={project.status} />

      {/* 编辑项目 Modal */}
      <Modal
        title="编辑项目"
        open={editModalOpen}
        onOk={handleEdit}
        onCancel={() => {
          setEditModalOpen(false);
          editForm.resetFields();
        }}
        confirmLoading={updateMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="display_name"
            label="项目名称"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="请输入项目名称" maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label="项目描述">
            <Input.TextArea placeholder="可选" maxLength={2000} rows={4} />
          </Form.Item>
          <Form.Item
            name="owner_user_id"
            label="负责人"
            rules={[{ required: true, message: '请选择负责人' }]}
          >
            <Select
              placeholder="请选择负责人"
              showSearch
              optionFilterProp="label"
              options={userOptions}
              allowClear
            />
          </Form.Item>
          <Form.Item
            name="visible_departments"
            label="可见单位"
            tooltip="选填。默认按部门层级可见（上级可看下级、下级可看上级）。如需对其他部门可见，请在此添加。"
          >
            <TreeSelect
              treeData={deptTreeData}
              treeCheckable
              treeDefaultExpandAll
              showSearch
              treeNodeFilterProp="title"
              placeholder="不选则按部门层级默认可见"
              allowClear
              style={{ width: '100%' }}
              maxTagCount={5}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

export default ProjectDetail;
