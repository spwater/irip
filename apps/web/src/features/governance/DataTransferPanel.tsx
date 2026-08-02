/**
 * 数据移交工具面板（P1-T1-03）
 *
 * 管理员可选择表名、源部门、目标部门，预览影响行数后确认执行。
 * 调用 POST /api/v1/governance/data-transfer 端点。
 */
import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Typography,
  message,
} from 'antd';
import { SwapOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { apiDataTransfer, type DataTransferResponse } from '@/api/governance';
import { apiListDepartments } from '@/api/departments';
import { DepartmentSelector } from '@/shared/DepartmentSelector';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { extractApiError } from '@/api/types';

const { Text } = Typography;

/** 可移交的表列表 */
const TRANSFERABLE_TABLES = [
  { value: 'fact', label: '实验事实 (fact)' },
  { value: 'parameter', label: '参数 (parameter)' },
  { value: 'model', label: '模型 (model)' },
  { value: 'flow_definition', label: '流程定义 (flow_definition)' },
  { value: 'flow_run', label: '流程运行 (flow_run)' },
  { value: 'equipment', label: '设备仪器 (equipment)' },
];

export function DataTransferPanel(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.roles?.includes('platform_administrator') ?? false;
  const [form] = Form.useForm();
  const [modalOpen, setModalOpen] = useState(false);
  const [previewResult, setPreviewResult] = useState<DataTransferResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // 监听表单字段变化（用于控制按钮禁用状态）
  const watchedTable = Form.useWatch('table', form);
  const watchedFromDept = Form.useWatch('from_dept_id', form);
  const watchedToDept = Form.useWatch('to_dept_id', form);
  const canPreview = !!(watchedTable && watchedFromDept && watchedToDept);

  // 部门列表查询（用于显示部门名称）
  const { data: deptData } = useQuery({
    queryKey: ['departments-for-transfer'],
    queryFn: () => apiListDepartments({ limit: 100 }),
  });

  const deptMap = new Map(
    (deptData?.items ?? []).map((d) => [d.id, d.display_name] as const),
  );

  // 预览（dry_run=true）
  const handlePreview = async (): Promise<void> => {
    try {
      const values = await form.validateFields();
      setPreviewLoading(true);
      setPreviewResult(null);
      const result = await apiDataTransfer({
        table: values.table,
        from_dept_id: values.from_dept_id,
        to_dept_id: values.to_dept_id,
        dry_run: true,
      });
      setPreviewResult(result);
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error(extractApiError(err));
    } finally {
      setPreviewLoading(false);
    }
  };

  // 确认执行（dry_run=false）
  const transferMutation = useMutation({
    mutationFn: (vars: { table: string; from_dept_id: string; to_dept_id: string }) =>
      apiDataTransfer({
        table: vars.table,
        from_dept_id: vars.from_dept_id,
        to_dept_id: vars.to_dept_id,
        dry_run: false,
      }),
    onSuccess: (data) => {
      message.success(`数据移交完成：${data.affected_rows} 行已更新`);
      setModalOpen(false);
      setPreviewResult(null);
      form.resetFields();
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  const handleConfirm = (): void => {
    const values = form.getFieldsValue();
    if (!values.table || !values.from_dept_id || !values.to_dept_id) return;
    transferMutation.mutate({
      table: values.table,
      from_dept_id: values.from_dept_id,
      to_dept_id: values.to_dept_id,
    });
  };

  if (!isAdmin) {
    return (
      <Card title="数据移交工具">
        <Text type="secondary">仅平台管理员可使用此功能。</Text>
      </Card>
    );
  }

  return (
    <Card
      title={
        <Space>
          <SwapOutlined />
          <span>数据移交工具</span>
        </Space>
      }
      extra={
        <Button type="primary" onClick={() => { form.resetFields(); setPreviewResult(null); setModalOpen(true); }}>
          发起数据移交
        </Button>
      }
    >
      <Alert
        type="warning"
        showIcon
        icon={<ExclamationCircleOutlined />}
        message="数据移交将批量修改数据的部门归属，操作不可撤销。"
        description="选择目标表、源部门和目标部门后，先预览影响行数，确认无误后再执行移交。所有操作将记录审计日志。"
        style={{ marginBottom: 16 }}
      />
      <Row gutter={16}>
        <Col span={8}>
          <Statistic
            title="可移交表数"
            value={TRANSFERABLE_TABLES.length}
            suffix="张"
          />
        </Col>
        <Col span={8}>
          <Statistic
            title="部门总数"
            value={deptData?.items.length ?? 0}
            suffix="个"
          />
        </Col>
        <Col span={8}>
          <Statistic
            title="上次预览影响行数"
            value={previewResult?.affected_rows ?? 0}
            suffix="行"
          />
        </Col>
      </Row>

      <Modal
        title="数据移交"
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          setPreviewResult(null);
          form.resetFields();
        }}
        width={600}
        footer={
          <Space>
            <Button onClick={() => { setModalOpen(false); setPreviewResult(null); form.resetFields(); }}>
              取消
            </Button>
            <Button
              onClick={handlePreview}
              loading={previewLoading}
              disabled={!canPreview}
            >
              预览影响
            </Button>
            <Popconfirm
              title="确认执行数据移交？"
              description="此操作不可撤销，所有受影响行的部门归属将被修改。"
              onConfirm={handleConfirm}
              okText="确认移交"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button
                type="primary"
                danger
                loading={transferMutation.isPending}
                disabled={!previewResult || previewResult.affected_rows === 0}
              >
                确认移交
              </Button>
            </Popconfirm>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="table"
            label="目标数据表"
            rules={[{ required: true, message: '请选择目标数据表' }]}
          >
            <Select
              placeholder="选择要移交的数据表"
              options={TRANSFERABLE_TABLES}
              onChange={() => setPreviewResult(null)}
            />
          </Form.Item>

          <Form.Item
            name="from_dept_id"
            label="源部门"
            rules={[{ required: true, message: '请选择源部门' }]}
          >
            <DepartmentSelector
              placeholder="选择源部门（数据当前归属）"
              allowRoot={true}
              onChange={() => setPreviewResult(null)}
            />
          </Form.Item>

          <Form.Item
            name="to_dept_id"
            label="目标部门"
            rules={[{ required: true, message: '请选择目标部门' }]}
          >
            <DepartmentSelector
              placeholder="选择目标部门（数据移交后归属）"
              allowRoot={true}
              onChange={() => setPreviewResult(null)}
            />
          </Form.Item>
        </Form>

        {previewResult && (
          <Alert
            type={previewResult.affected_rows > 0 ? 'info' : 'success'}
            showIcon
            message="影响预览"
            description={
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="数据表">
                  {previewResult.table}
                </Descriptions.Item>
                <Descriptions.Item label="源部门">
                  {deptMap.get(previewResult.from_dept_id) ?? previewResult.from_dept_id.slice(0, 8)}
                </Descriptions.Item>
                <Descriptions.Item label="目标部门">
                  {deptMap.get(previewResult.to_dept_id) ?? previewResult.to_dept_id.slice(0, 8)}
                </Descriptions.Item>
                <Descriptions.Item label="受影响行数">
                  <Text strong style={{ color: previewResult.affected_rows > 0 ? '#fa541c' : '#52c41a' }}>
                    {previewResult.affected_rows} 行
                  </Text>
                </Descriptions.Item>
              </Descriptions>
            }
          />
        )}
      </Modal>
    </Card>
  );
}
