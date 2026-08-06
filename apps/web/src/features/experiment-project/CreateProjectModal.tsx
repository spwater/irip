import { Form, Input, Modal, Select, TreeSelect, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { apiCreateExperimentProject } from '@/api/experiment-projects';
import { apiListDepartments } from '@/api/departments';
import { apiListUsers } from '@/api/governance';
import { buildDeptTree } from '@/shared/buildDeptTree';
import { extractApiError } from '@/api/types';

/**
 * 新建实验项目弹窗
 *
 * 表单：所属单位(DepartmentSelector 必填) + 编码(Input) + 名称(Input) + 描述(TextArea) + 可见单位(TreeSelect 多选 选填)
 * 提交调用 apiCreateExperimentProject，成功后刷新列表。
 */
export function CreateProjectModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();

  // 部门树数据（用于可见单位多选）
  const { data: deptData } = useQuery({
    queryKey: ['departments-for-project-modal'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });
  const deptTreeData = useMemo(
    () => buildDeptTree(deptData?.items ?? []),
    [deptData],
  );

  // 用户列表（用于负责人选择）
  const { data: userData } = useQuery({
    queryKey: ['users-for-project-modal'],
    queryFn: () => apiListUsers({ limit: 100 }),
  });
  const userOptions = (userData?.items ?? []).map((u) => ({
    value: u.id,
    label: `${u.display_name}（${u.email}）`,
  }));

  const createMutation = useMutation({
    mutationFn: apiCreateExperimentProject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['experiment-projects'] });
      message.success('项目创建成功');
      form.resetFields();
      onClose();
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const handleOk = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      createMutation.mutate({
        department_id: values.department_id as string,
        code: (values.code as string) || null,
        display_name: values.display_name as string,
        description: (values.description as string) ?? null,
        visible_departments: (values.visible_departments as string[]) ?? [],
        owner_user_id: values.owner_user_id as string,
      });
    } catch {
      // 校验失败
    }
  };

  return (
    <Modal
      title="新建项目"
      open={open}
      onOk={handleOk}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      confirmLoading={createMutation.isPending}
      okText="创建"
      cancelText="取消"
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="department_id"
          label="所属单位"
          rules={[{ required: true, message: '请选择所属单位' }]}
        >
          <TreeSelect
            treeData={deptTreeData}
            treeDefaultExpandAll
            showSearch
            treeNodeFilterProp="title"
            placeholder="请选择所属单位"
            allowClear
            style={{ width: '100%' }}
          />
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
          name="display_name"
          label="项目名称"
          rules={[{ required: true, message: '请输入项目名称' }]}
        >
          <Input placeholder="如：水泥组分研究" maxLength={200} />
        </Form.Item>
        <Form.Item name="description" label="项目描述">
          <Input.TextArea
            placeholder="可选"
            maxLength={2000}
            rows={4}
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
  );
}

export default CreateProjectModal;
