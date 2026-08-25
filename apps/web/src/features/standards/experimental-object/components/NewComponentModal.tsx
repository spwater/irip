/**
 * NewComponentModal — 新建数据接口 Modal。
 *
 * 从 ExperimentalObjectPage.tsx 提取。复用 ComponentFormFields 组件，
 * 支持高级模式（直接编辑 YAML）和表单模式（自动生成 YAML）。
 * 通过 props 传递 form 实例、数据和回调。
 */

import { useEffect } from 'react';
import type { FormInstance } from 'antd';
import { Form, Input, Modal, Select, Space, Switch, TreeSelect, Typography } from 'antd';
import { buildManifestYaml } from '@/shared/component-utils';
import { ComponentFormFields } from '@/features/components/ComponentFormFields';
import { useAuthStore } from '@/features/auth/AuthProvider';

const { Text } = Typography;

export interface NewComponentModalProps {
  open: boolean;
  onOk: () => void;
  onCancel: () => void;
  confirmLoading: boolean;
  compForm: FormInstance;
  compAdvancedMode: boolean;
  setCompAdvancedMode: (mode: boolean) => void;
  ingestionToolOptions: { value: string; label: string }[];
  deptOptions: { value: string; label: string }[];
  deptTreeData: unknown;
}

export function NewComponentModal(props: NewComponentModalProps): JSX.Element {
  const {
    open,
    onOk,
    onCancel,
    confirmLoading,
    compForm,
    compAdvancedMode,
    setCompAdvancedMode,
    ingestionToolOptions,
    deptOptions,
    deptTreeData,
  } = props;

  const user = useAuthStore((s) => s.user);
  const isAdmin: boolean = user?.roles?.includes('platform_administrator') ?? false;

  // 非管理员用户预填主部门（disabled 不可改，但需要看到自己的部门）
  useEffect(() => {
    if (open && !isAdmin && user?.departmentId) {
      compForm.setFieldsValue({ department_id: user.departmentId });
    }
  }, [open, isAdmin, user?.departmentId, compForm]);

  return (
    <Modal
      title="新建数据接口"
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      confirmLoading={confirmLoading}
      okText="发布"
      cancelText="取消"
      width={680}
      destroyOnHidden
    >
      <Form form={compForm} layout="vertical">
        <div style={{ marginBottom: 16 }}>
          <Space align="center">
            <Text>高级模式</Text>
            <Switch
              checked={compAdvancedMode}
              onChange={(checked) => {
                if (checked) {
                  const vals = compForm.getFieldsValue() as Record<string, string>;
                  const yaml = buildManifestYaml({
                    display_name: vals.display_name ?? '',
                    description: vals.description ?? '',
                    prompt: vals.prompt ?? '',
                    tool_type: vals.tool_type ?? 'llm_converter',
                  });
                  compForm.setFieldsValue({ manifest_yaml: yaml });
                }
                setCompAdvancedMode(checked);
              }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {compAdvancedMode ? '直接编辑 YAML 全文' : '填写表单字段，自动生成 YAML'}
            </Text>
          </Space>
        </div>
        {compAdvancedMode ? (
          <Form.Item
            name="manifest_yaml"
            label="接口清单 (YAML)"
            rules={[{ required: true, message: '请输入 YAML' }, { min: 10, message: '清单内容过短' }]}
          >
            <Input.TextArea
              placeholder={'name: iface_ffffffff  # 自动生成\nkind: ingestion\ndisplay_name: "接口名"\n...'}
              rows={16}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
          </Form.Item>
        ) : (
          <ComponentFormFields ingestionToolOptions={ingestionToolOptions} />
        )}
        <Form.Item name="department_id" label="所属单位">
          <Select
            placeholder="选择所属单位（可选）"
            allowClear
            showSearch
            optionFilterProp="label"
            options={deptOptions}
            disabled={!isAdmin}
          />
        </Form.Item>
        <Form.Item
          name="visible_departments"
          label="可见单位"
          tooltip="选填。默认按部门层级可见。如需对其他部门可见，请在此添加。"
        >
          <TreeSelect
            treeData={deptTreeData as never}
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
