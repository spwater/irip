/**
 * useBatchExecute — 批量执行逻辑 + 8 个 state。
 *
 * 从 FlowDetail.tsx 提取。管理批量执行的全部状态和 handleBatchExecute 逻辑。
 */

import { useState, type Dispatch, type SetStateAction } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { message } from 'antd';
import {
  apiGetComponent,
  apiCreateFlowRun,
  apiGetFlowRun,
  apiPublishFlow,
  type ComponentSummary,
  type FlowNodeSchema,
  type FlowSummary,
} from '@/api/equipment-flows';
import { apiUploadFile } from '@/api/models-ai';
import { parseManifest } from '../../shared';
import {
  BATCH_POLL_MAX_ATTEMPTS,
  BATCH_POLL_INTERVAL,
  FLOW_RUN_TERMINAL_STATUSES,
  type BatchItemResult,
} from '../types';

export interface UseBatchExecuteParams {
  selectedFlowId: string | null;
  flow: FlowSummary | undefined;
  compMap: Map<string, ComponentSummary>;
  componentOptions: { value: string; label: string; version: string; summary: ComponentSummary }[];
}

export interface UseBatchExecuteResult {
  batchModalOpen: boolean;
  setBatchModalOpen: Dispatch<SetStateAction<boolean>>;
  batchFiles: File[];
  setBatchFiles: Dispatch<SetStateAction<File[]>>;
  batchRunning: boolean;
  batchProgress: { current: number; total: number; status: string } | null;
  setBatchProgress: Dispatch<SetStateAction<{ current: number; total: number; status: string } | null>>;
  batchResults: BatchItemResult[] | null;
  setBatchResults: Dispatch<SetStateAction<BatchItemResult[] | null>>;
  batchSelectedComp: string | undefined;
  setBatchSelectedComp: Dispatch<SetStateAction<string | undefined>>;
  batchOperator: string;
  setBatchOperator: Dispatch<SetStateAction<string>>;
  batchPrompt: string;
  setBatchPrompt: Dispatch<SetStateAction<string>>;
  handleBatchExecute: () => Promise<void>;
}

export function useBatchExecute(params: UseBatchExecuteParams): UseBatchExecuteResult {
  const { selectedFlowId, flow, compMap, componentOptions } = params;

  const queryClient = useQueryClient();

  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{
    current: number;
    total: number;
    status: string;
  } | null>(null);
  /** H-16: 批量执行逐项结果，仅 succeeded 计成功 */
  const [batchResults, setBatchResults] = useState<BatchItemResult[] | null>(null);
  const [batchSelectedComp, setBatchSelectedComp] = useState<string | undefined>(undefined);
  const [batchOperator, setBatchOperator] = useState<string>('');
  const [batchPrompt, setBatchPrompt] = useState<string>('');

  const handleBatchExecute = async (): Promise<void> => {
    if (!selectedFlowId || batchFiles.length === 0) return;
    if (!batchSelectedComp) {
      message.warning('请先选择数据接口');
      return;
    }
    setBatchRunning(true);
    setBatchProgress({ current: 0, total: batchFiles.length, status: '准备执行...' });

    // 检查是否需要先发布
    const currentNode = (flow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];
    const needPublish = !currentNode || currentNode.component_name !== batchSelectedComp;

    if (needPublish) {
      const comp = componentOptions.find((c) => c.value === batchSelectedComp);
      if (!comp) {
        message.error('找不到选中的数据接口');
        setBatchRunning(false);
        return;
      }
      let params: Record<string, unknown> = {};
      try {
        const detail = await apiGetComponent(comp.summary.id);
        const parsed = parseManifest(detail.manifest_yaml);
        for (const p of parsed.params) {
          params[p.name] = p.default ?? '';
        }
      } catch {
        // 获取详情失败时用空 params
      }
      const nodes: FlowNodeSchema[] = [
        {
          node_id: 'n1',
          component_name: batchSelectedComp,
          component_version: comp.version,
          params: {
            ...params,
            experimental_object_code: flow?.experimental_object_code ?? null,
          },
        },
      ];
      try {
        await apiPublishFlow(selectedFlowId, { nodes });
        await queryClient.refetchQueries({ queryKey: ['flow', selectedFlowId] });
      } catch (err) {
        message.error(`发布失败: ${err instanceof Error ? err.message : String(err)}`);
        setBatchRunning(false);
        return;
      }
    }

    // 从 published 版本获取 node 信息（从 queryClient 获取最新数据，确保发布后拿到最新 node）
    const updatedFlow = queryClient.getQueryData<FlowSummary>(['flow', selectedFlowId]);
    const node = (updatedFlow?.latest_version?.nodes as FlowNodeSchema[] | undefined)?.[0];
    const comp = node?.component_name ? compMap.get(node.component_name) : undefined;

    // H-16: 逐项维护 succeeded/failed/cancelled/timed_out
    const results: BatchItemResult[] = [];

    for (let i = 0; i < batchFiles.length; i++) {
      const file = batchFiles[i];
      setBatchProgress({
        current: i,
        total: batchFiles.length,
        status: `正在上传: ${file.name}`,
      });
      try {
        // 1. 上传文件
        const uploadRes = await apiUploadFile(file);
        // 2. 构建 inputs（prompt 用组件当前活跃版本的值，不用 flow 快照）
        const inputs: Record<string, unknown> = {};
        if (node) {
          for (const key of Object.keys(node.params ?? {})) {
            if (key === 'path') {
              inputs[key] = `artifact:${uploadRes.artifact_id}`;
            } else if (key === 'experimental_object_code') {
              inputs[key] =
                (node.params as Record<string, unknown>)?.experimental_object_code ?? '';
            } else if (key === 'prompt' && comp?.prompt) {
              inputs[key] = batchPrompt || comp.prompt;
            } else {
              const defaultVal = (node.params as Record<string, unknown>)?.[key];
              inputs[key] = defaultVal ?? '';
            }
          }
        }
        // 执行人存入元信息（为空时取任务执行人）
        inputs['_operator'] = batchOperator || flow?.operator || '';
        inputs['_filename'] = file.name;
        // 3. 创建运行
        setBatchProgress({
          current: i,
          total: batchFiles.length,
          status: `正在执行: ${file.name}`,
        });
        const run = await apiCreateFlowRun(selectedFlowId, { inputs });
        // 4. 等待执行完成（轮询）— H-16: 轮询耗尽记超时
        let runStatus: string | null = null;
        for (let attempts = 0; attempts < BATCH_POLL_MAX_ATTEMPTS; attempts++) {
          await new Promise((r) => setTimeout(r, BATCH_POLL_INTERVAL));
          const updated = await apiGetFlowRun(run.id);
          runStatus = updated.status;
          if (FLOW_RUN_TERMINAL_STATUSES.includes(updated.status)) {
            break;
          }
        }
        // H-16: 仅 succeeded/failed/cancelled 为终态；轮询耗尽记 timed_out
        if (runStatus && FLOW_RUN_TERMINAL_STATUSES.includes(runStatus)) {
          results.push({
            fileName: file.name,
            status: runStatus as BatchItemResult['status'],
            runId: run.id,
          });
        } else {
          // 轮询耗尽，未到达终态
          results.push({
            fileName: file.name,
            status: 'timed_out',
            runId: run.id,
          });
        }
      } catch (err) {
        // H-16: 记录失败原因
        const errMsg = err instanceof Error ? err.message : String(err);
        results.push({ fileName: file.name, status: 'failed', error: errMsg });
        message.error(`文件 ${file.name} 执行失败: ${errMsg}`);
      }
    }

    // H-16: 准确汇总 — 仅 succeeded 计成功，混合结果用 warning 而非 success
    const summary = results.reduce(
      (acc, r) => {
        acc[r.status]++;
        return acc;
      },
      { succeeded: 0, failed: 0, cancelled: 0, timed_out: 0 } as Record<
        BatchItemResult['status'],
        number
      >,
    );

    setBatchResults(results);
    setBatchProgress({
      current: batchFiles.length,
      total: batchFiles.length,
      status: '批量执行完成',
    });
    void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
    setBatchRunning(false);
    setBatchFiles([]);

    // H-16: 展示准确汇总 — 有失败/取消/超时时用 warning 而非 success
    const hasIssues = summary.failed > 0 || summary.cancelled > 0 || summary.timed_out > 0;
    if (hasIssues) {
      const parts: string[] = [];
      if (summary.succeeded > 0) parts.push(`${summary.succeeded} 成功`);
      if (summary.failed > 0) parts.push(`${summary.failed} 失败`);
      if (summary.cancelled > 0) parts.push(`${summary.cancelled} 取消`);
      if (summary.timed_out > 0) parts.push(`${summary.timed_out} 超时`);
      message.warning(`批量执行完成: ${parts.join(', ')}`);
    } else {
      message.success(`批量执行完成: ${summary.succeeded} 个文件`);
    }
  };

  return {
    batchModalOpen,
    setBatchModalOpen,
    batchFiles,
    setBatchFiles,
    batchRunning,
    batchProgress,
    setBatchProgress,
    batchResults,
    setBatchResults,
    batchSelectedComp,
    setBatchSelectedComp,
    batchOperator,
    setBatchOperator,
    batchPrompt,
    setBatchPrompt,
    handleBatchExecute,
  };
}
