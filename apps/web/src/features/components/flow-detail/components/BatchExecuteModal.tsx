/**
 * BatchExecuteModal — 批量执行 Modal。
 *
 * 从 FlowDetail.tsx 提取。通过 props 传递所有状态和回调。
 */

import { Alert, Button, Form, Input, Select, Space, Spin, Modal, Tag, Typography } from 'antd';
import type { BatchItemResult } from '../types';
import type { ComponentSummary } from '@/api/equipment-flows';

const { Text } = Typography;

export interface BatchExecuteModalProps {
  open: boolean;
  onCancel: () => void;
  batchRunning: boolean;
  batchProgress: { current: number; total: number; status: string } | null;
  batchResults: BatchItemResult[] | null;
  batchFiles: File[];
  batchSelectedComp: string | undefined;
  batchOperator: string;
  batchPrompt: string;
  filteredCompOptions: { value: string; label: string; version: string; summary: ComponentSummary }[];
  compMap: Map<string, ComponentSummary>;
  equipMap: Map<string, string>;
  toolTypeDisplayName: Map<string, string>;
  componentOptions: { value: string; label: string; version: string; summary: ComponentSummary }[];
  setBatchFiles: (files: File[]) => void;
  setBatchSelectedComp: (val: string | undefined) => void;
  setBatchOperator: (val: string) => void;
  setBatchPrompt: (val: string) => void;
  setBatchModalOpen: (open: boolean) => void;
  setBatchProgress: (val: { current: number; total: number; status: string } | null) => void;
  setBatchResults: (val: BatchItemResult[] | null) => void;
  handleBatchExecute: () => Promise<void>;
}

export function BatchExecuteModal(props: BatchExecuteModalProps): JSX.Element {
  const {
    open,
    onCancel,
    batchRunning,
    batchProgress,
    batchResults,
    batchFiles,
    batchSelectedComp,
    batchOperator,
    batchPrompt,
    filteredCompOptions,
    compMap,
    equipMap,
    toolTypeDisplayName,
    componentOptions,
    setBatchFiles,
    setBatchSelectedComp,
    setBatchOperator,
    setBatchPrompt,
    setBatchModalOpen,
    setBatchProgress,
    setBatchResults,
    handleBatchExecute,
  } = props;

  return (
    <Modal
      title="提取"
      open={open}
      onCancel={onCancel}
      footer={
        batchRunning ? null : batchResults ? (
          <Button type="primary" onClick={() => {
            setBatchModalOpen(false);
            setBatchFiles([]);
            setBatchProgress(null);
            setBatchResults(null);
            setBatchSelectedComp(undefined);
            setBatchOperator('');
          }}>
            关闭
          </Button>
        ) : (
          <Space>
            <Button onClick={() => setBatchModalOpen(false)}>取消</Button>
            <Button
              type="primary"
              disabled={batchFiles.length === 0}
              onClick={() => void handleBatchExecute()}
            >
              开始执行 ({batchFiles.length} 个文件)
            </Button>
          </Space>
        )
      }
      width={600}
    >
      {batchRunning && batchProgress ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin size="large" />
          <div style={{ marginTop: 16 }}>
            <Text strong>
              进度: {batchProgress.current} / {batchProgress.total}
            </Text>
          </div>
          <div style={{ marginTop: 8 }}>
            <Text type="secondary">{batchProgress.status}</Text>
          </div>
        </div>
      ) : batchResults ? (
        <div>
          {(() => {
            const summary = batchResults.reduce(
              (acc, r) => { acc[r.status]++; return acc; },
              { succeeded: 0, failed: 0, cancelled: 0, timed_out: 0 } as Record<BatchItemResult['status'], number>,
            );
            const hasIssues = summary.failed > 0 || summary.cancelled > 0 || summary.timed_out > 0;
            return (
              <Alert
                type={hasIssues ? 'warning' : 'success'}
                message={
                  hasIssues
                    ? `批量执行完成: ${summary.succeeded} 成功, ${summary.failed} 失败, ${summary.cancelled} 取消, ${summary.timed_out} 超时`
                    : `批量执行完成: ${summary.succeeded} 个文件全部成功`
                }
                style={{ marginBottom: 16 }}
              />
            );
          })()}
          {batchResults
            .filter((r) => r.status === 'failed' || r.status === 'timed_out')
            .map((r, idx) => (
              <Alert
                key={idx}
                type="error"
                message={`${r.fileName}: ${r.status === 'timed_out' ? '执行超时' : '执行失败'}`}
                description={r.error || (r.status === 'timed_out' ? '轮询超时，未在规定时间内到达终态' : '未知原因')}
                style={{ marginBottom: 8 }}
              />
            ))}
        </div>
      ) : (
        <>
          {batchSelectedComp && (() => {
            const runComp = batchSelectedComp ? compMap.get(batchSelectedComp) : undefined;
            const compOpt = componentOptions.find((c) => c.value === batchSelectedComp);
            const eqName = runComp?.equipment_id ? equipMap.get(runComp.equipment_id) : null;
            const converterName = runComp?.tool_type ? toolTypeDisplayName.get(runComp.tool_type) : null;
            return (runComp || compOpt) ? (
              <div style={{ marginBottom: 16, padding: '8px 12px', background: 'var(--ocean-surface-structural)', borderRadius: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Space size={6}>
                  <Tag color="purple" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                    {runComp?.display_name ?? compOpt?.label ?? batchSelectedComp}
                  </Tag>
                  {eqName && (
                    <>
                      <Text type="secondary" style={{ fontSize: 12 }}>→</Text>
                      <Tag color="cyan" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                        {eqName}
                      </Tag>
                    </>
                  )}
                </Space>
                {converterName && (
                  <Tag color="orange" style={{ margin: 0, padding: '2px 8px', borderRadius: 4 }}>
                    {converterName}
                  </Tag>
                )}
              </div>
            ) : null;
          })()}
          <Form layout="vertical">
            <Form.Item label="数据来源" required>
              <Select
                placeholder="请选择数据接口"
                showSearch
                optionFilterProp="label"
                options={filteredCompOptions}
                value={batchSelectedComp}
                onChange={(value: string) => setBatchSelectedComp(value)}
              />
            </Form.Item>
            <Form.Item label="执行人">
              <Input
                value={batchOperator}
                onChange={(e) => setBatchOperator(e.target.value)}
                placeholder="执行人"
                maxLength={100}
              />
            </Form.Item>
            <input
              type="file"
              multiple
              id="batch-file-input"
              style={{ display: 'none' }}
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []);
                setBatchFiles(files);
                if (e.target) e.target.value = '';
              }}
            />
            <div
              role="button"
              tabIndex={0}
              onClick={() => document.getElementById('batch-file-input')?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  document.getElementById('batch-file-input')?.click();
                }
              }}
              style={{
                border: '2px dashed var(--ocean-border-strong)',
                borderRadius: 8,
                padding: 32,
                textAlign: 'center',
                cursor: 'pointer',
                marginBottom: 16,
              }}
            >
              <Text type="secondary" style={{ fontSize: 14 }}>
                {batchFiles.length > 0
                  ? `已选择 ${batchFiles.length} 个文件`
                  : '点击选择多个文件'}
              </Text>
              {batchFiles.length > 0 && (
                <div style={{ marginTop: 8, textAlign: 'left', maxHeight: 200, overflow: 'auto' }}>
                  {batchFiles.map((f, i) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--ocean-border-subtle)' }}>
                      <Text style={{ fontSize: 13 }}>{f.name}</Text>
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        ({(f.size / 1024).toFixed(0)} KB)
                      </Text>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {batchSelectedComp && (() => {
              const batchComp = batchSelectedComp ? compMap.get(batchSelectedComp) : undefined;
              if (batchComp?.tool_type !== 'llm_converter') return null;
              return (
                <Form.Item label="大模型提示词">
                  <Input.TextArea
                    value={batchPrompt || batchComp?.prompt || ''}
                    onChange={(e) => setBatchPrompt(e.target.value)}
                    rows={6}
                    placeholder="大模型提示词"
                  />
                </Form.Item>
              );
            })()}
            <Text type="secondary" style={{ fontSize: 12 }}>
              将使用当前任务的数据接口，逐个上传文件并执行。文件合规性由用户自行负责。
            </Text>
          </Form>
        </>
      )}
    </Modal>
  );
}
