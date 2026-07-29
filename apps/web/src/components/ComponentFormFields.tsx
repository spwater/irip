/**
 * 组件表单字段（表单模式共用，绑定到外层 Form 上下文）。
 *
 * 从 ComponentsPage.tsx 拆出，包含：
 * - 关联实验对象级联选择器
 * - 文件预加载 + 提示词推荐 + 数据抽取预览
 */

import { useEffect, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { UploadProps } from 'antd';
import { apiUploadFile, apiRecommendPrompt, apiExtractPreview } from '@/api/models-ai';
import { extractApiError, type IndustrialObject } from '@/api/types';
import type { ObjectOption } from './component-utils';

const { Text } = Typography;

export function ComponentFormFields({
  objectOptions,
  objectTypeOptions,
  equipmentOptions,
  objectMap,
  originalName,
  ingestionToolOptions,
}: {
  objectOptions: { value: string; label: string; object_type: string }[];
  objectTypeOptions: { value: string; label: string }[];
  equipmentOptions: ObjectOption[];
  objectMap: Map<string, IndustrialObject>;
  originalName?: string;
  ingestionToolOptions: { value: string; label: string }[];
}): JSX.Element {
  const [uploadedFile, setUploadedFile] = useState<{ name: string; artifactId: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [recommending, setRecommending] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewResult, setPreviewResult] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectedType, setSelectedType] = useState<string | undefined>(undefined);
  const formInstance = Form.useFormInstance();

  const watchedExpCode = Form.useWatch('experimental_object_code', formInstance);
  const watchedToolType = Form.useWatch('tool_type', formInstance);

  useEffect(() => {
    if (watchedExpCode) {
      const obj = objectMap.get(watchedExpCode);
      if (obj) {
        const currentName = formInstance.getFieldValue('display_name') as string ?? '';
        if (!currentName || currentName.endsWith('接口')) {
          formInstance.setFieldsValue({ display_name: `${obj.display_name}接口` });
        }
      }
    }
  }, [watchedExpCode, objectMap, formInstance]);

  const uploadProps: UploadProps = {
    accept: '.pdf,.txt,.md,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx',
    maxCount: 1,
    showUploadList: false,
    customRequest: async (options) => {
      const { file, onSuccess, onError } = options;
      setUploading(true);
      try {
        const res = await apiUploadFile(file as File);
        setUploadedFile({ name: res.filename, artifactId: res.artifact_id });
        onSuccess?.(res);
        message.success(`文件 ${res.filename} 预加载成功`);
      } catch (err: unknown) {
        onError?.(err as Error);
        message.error(extractApiError(err));
      } finally {
        setUploading(false);
      }
    },
    onRemove: () => {
      setUploadedFile(null);
    },
  };

  return (
    <>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item name="equipment_id" label="关联设备">
            <Select
              placeholder="请选择关联设备"
              allowClear
              showSearch
              optionFilterProp="label"
              options={equipmentOptions}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="接口编码">
            <Input value={originalName ?? 'iface_ffffffff'} disabled />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={4}>
          <Form.Item label="类型">
            <Select
              placeholder="全部"
              allowClear
              value={selectedType}
              onChange={(val: string | undefined) => {
                setSelectedType(val);
                if (watchedExpCode) {
                  const obj = objectMap.get(watchedExpCode);
                  if (obj && val && obj.object_type !== val) {
                    formInstance.setFieldsValue({ experimental_object_code: undefined });
                  }
                }
              }}
              options={objectTypeOptions}
            />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="experimental_object_code" label="实验对象">
            <Select
              placeholder="请选择实验对象"
              allowClear
              showSearch
              optionFilterProp="label"
              options={objectOptions.filter((o) => !selectedType || o.object_type === selectedType)}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            name="display_name"
            label="组件名称"
            rules={[{ required: true, message: '请输入组件名称' }]}
          >
            <Input placeholder="如：XRF-EZ扫描提取器接口" />
          </Form.Item>
        </Col>
      </Row>
      <Form.Item
        name="description"
        label="描述"
      >
        <Input placeholder="组件描述（可选）" />
      </Form.Item>
      <Form.Item name="tool_type" label="解析工具">
        <Select
          options={ingestionToolOptions}
        />
      </Form.Item>
      <Form.Item label="文件预加载">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Upload {...uploadProps}>
              <Button loading={uploading}>
                {uploading ? '上传中...' : '选择文件预加载'}
              </Button>
            </Upload>
            <Text type="secondary" style={{ fontSize: 12 }}>
              自动检测（PDF/图片/Word/Excel/文本），临时预览用，关闭窗口即失效。
            </Text>
          </Space>
          {uploadedFile && (
            <Space>
              <Tag color="blue">{uploadedFile.name}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                artifact:{uploadedFile.artifactId}
              </Text>
              <Button
                type="link"
                size="small"
                danger
                onClick={() => setUploadedFile(null)}
              >
                移除
              </Button>
            </Space>
          )}
        </Space>
      </Form.Item>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <Text>{watchedToolType === 'xrd_converter' ? 'LLM 提示词（XRD 工具无需填写）' : 'LLM 提示词'}</Text>
        <Space>
          <Button
            type="link"
            size="small"
            disabled={!uploadedFile || watchedToolType === 'xrd_converter'}
            loading={recommending}
            onClick={async () => {
              if (!uploadedFile) return;
              setRecommending(true);
              try {
                const res = await apiRecommendPrompt({
                  artifact_id: uploadedFile.artifactId,
                  filename: uploadedFile.name,
                });
                formInstance.setFieldsValue({ prompt: res.prompt });
                message.success('提示词已生成');
              } catch (err: unknown) {
                message.error(extractApiError(err));
              } finally {
                setRecommending(false);
              }
            }}
          >
            提示词推荐
          </Button>
          <Button
            type="link"
            size="small"
            disabled={!uploadedFile}
            loading={previewing}
            onClick={async () => {
              if (!uploadedFile) return;
              setPreviewing(true);
              setPreviewResult(null);
              setPreviewOpen(true);
              try {
                const currentPrompt = formInstance.getFieldValue('prompt') as string ?? '';
                const currentToolType = formInstance.getFieldValue('tool_type') as string ?? 'llm_converter';
                const res = await apiExtractPreview({
                  artifact_id: uploadedFile.artifactId,
                  filename: uploadedFile.name,
                  prompt: currentPrompt,
                  tool_type: currentToolType,
                });
                setPreviewResult(res.result);
              } catch (err: unknown) {
                setPreviewResult(extractApiError(err));
              } finally {
                setPreviewing(false);
              }
            }}
          >
            数据抽取预览
          </Button>
        </Space>
      </div>
      <Form.Item
        name="prompt"
        labelCol={{ span: 0 }}
        wrapperCol={{ span: 24 }}
        rules={[{ required: false, message: '请输入 LLM 提示词' }]}
      >
        <Input.TextArea rows={6} placeholder={watchedToolType === 'xrd_converter' ? 'XRD 工具不需要提示词' : '请输入 LLM 提示词，支持多行'} />
      </Form.Item>
      <Modal
        title="数据抽取预览"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={800}
      >
        {previewing ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin tip="正在调用大模型抽取数据..." />
          </div>
        ) : previewResult ? (
          (() => {
            try {
              const parsed = JSON.parse(previewResult);
              const meta = parsed.metadata ?? {};
              const pts: { name: string; value: unknown; unit: string | null }[] = parsed.points ?? [];
              const srs: { name: string; columns: string[]; rows: unknown[][] }[] = parsed.series ?? [];
              return (
                <div style={{ maxHeight: 600, overflow: 'auto' }}>
                  <Text strong>元数据（Metadata）</Text>
                  <Descriptions
                    bordered
                    column={1}
                    size="small"
                    style={{ marginTop: 8, marginBottom: 16 }}
                  >
                    {Object.keys(meta).length > 0 ? (
                      Object.entries(meta).map(([k, v]) => (
                        <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
                      ))
                    ) : (
                      <Descriptions.Item label="（空）">无元数据</Descriptions.Item>
                    )}
                  </Descriptions>
                  <Text strong>单点数据（Points，{pts.length} 项）</Text>
                  <Table
                    size="small"
                    style={{ marginTop: 8, marginBottom: 16 }}
                    pagination={false}
                    rowKey={(_, idx) => String(idx)}
                    dataSource={pts}
                    columns={[
                      { title: '名称', dataIndex: 'name', key: 'name' },
                      { title: '值', dataIndex: 'value', key: 'value' },
                      { title: '单位', dataIndex: 'unit', key: 'unit' },
                    ]}
                  />
                  <Text strong>序列数据（Series，{srs.length} 组）</Text>
                  {srs.length > 0 ? (
                    srs.map((s, i) => (
                      <Card key={i} size="small" title={s.name ?? `序列 ${i + 1}`} style={{ marginTop: 8, marginBottom: 8 }}>
                        <Table
                          size="small"
                          pagination={false}
                          rowKey={(_, idx) => String(idx)}
                          dataSource={s.rows.map((r, ri) => {
                            const obj: Record<string, unknown> = { _key: ri };
                            (s.columns ?? []).forEach((c, ci) => { obj[c] = r[ci]; });
                            return obj;
                          })}
                          columns={(s.columns ?? []).map((c) => ({
                            title: c,
                            dataIndex: c,
                            key: c,
                            ellipsis: true,
                          }))}
                        />
                      </Card>
                    ))
                  ) : (
                    <Text type="secondary">无序列数据</Text>
                  )}
                </div>
              );
            } catch {
              return (
                <Input.TextArea
                  value={previewResult}
                  readOnly
                  rows={20}
                  style={{ fontFamily: 'monospace', fontSize: 13 }}
                />
              );
            }
          })()
        ) : null}
      </Modal>
    </>
  );
}
