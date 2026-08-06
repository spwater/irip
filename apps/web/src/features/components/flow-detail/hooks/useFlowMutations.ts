/**
 * useFlowMutations — FlowDetail 所有 useMutation 声明。
 *
 * 从 FlowDetail.tsx 提取。统一管理 11 个 mutation 的 onSuccess/onError
 * + queryClient.invalidateQueries。
 */

import { Form, message } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  apiArchiveFlow,
  apiCancelFlowRun,
  apiCreateFlow,
  apiDeleteFlow,
  apiDeleteFlowRun,
  apiPublishComponent,
  apiRestoreFlow,
  apiResumeFlowRun,
  apiUpdateFlow,
} from '@/api/equipment-flows';
import { apiCreateObject } from '@/api/standards-objects';
import { extractApiError } from '@/api/types';

export interface UseFlowMutationsParams {
  projectId?: string;
  selectedFlowId: string | null;
  activeRunId: string | null;
  createForm: ReturnType<typeof Form.useForm>[0];
  editForm: ReturnType<typeof Form.useForm>[0];
  newObjectForm: ReturnType<typeof Form.useForm>[0];
  compForm: ReturnType<typeof Form.useForm>[0];
  setCreateModalOpen: (open: boolean) => void;
  setEditModalOpen: (open: boolean) => void;
  setEditFlowId: (id: string | null) => void;
  setSelectedFlowId: (id: string | null) => void;
  setActiveRunId: (id: string | null) => void;
  setNewObjectModalOpen: (open: boolean) => void;
  setCompCreateModalOpen: (open: boolean) => void;
  setCompAdvancedMode: (mode: boolean) => void;
}

export type UseFlowMutationsResult = {
  createMutation: ReturnType<typeof useMutation>;
  createObjectMutation: ReturnType<typeof useMutation>;
  publishCompMutation: ReturnType<typeof useMutation>;
  archiveMutation: ReturnType<typeof useMutation>;
  restoreMutation: ReturnType<typeof useMutation>;
  deleteFlowMutation: ReturnType<typeof useMutation>;
  updateFlowMutation: ReturnType<typeof useMutation>;
  resumeMutation: ReturnType<typeof useMutation>;
  cancelMutation: ReturnType<typeof useMutation>;
  deleteRunMutation: ReturnType<typeof useMutation>;
};

export function useFlowMutations(params: UseFlowMutationsParams) {
  const {
    projectId,
    selectedFlowId,
    activeRunId,
    createForm,
    editForm,
    newObjectForm,
    compForm,
    setCreateModalOpen,
    setEditModalOpen,
    setSelectedFlowId,
    setActiveRunId,
    setNewObjectModalOpen,
    setCompCreateModalOpen,
    setCompAdvancedMode,
  } = params;

  const queryClient = useQueryClient();

  // ---- 创建流程 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateFlow,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      setCreateModalOpen(false);
      createForm.resetFields();
      message.success('流程创建成功');
      setSelectedFlowId(data.id);
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 新建实验对象 Mutation ----
  const createObjectMutation = useMutation({
    mutationFn: apiCreateObject,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['objects-for-flow-list'] });
      setNewObjectModalOpen(false);
      newObjectForm.resetFields();
      message.success('实验对象创建成功');
      // 自动选中新建的实验对象
      createForm.setFieldsValue({ experimental_object_code: data.code });
      editForm.setFieldsValue({ experimental_object_code: data.code });
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 新建数据接口 Mutation ----
  const publishCompMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      setCompCreateModalOpen(false);
      compForm.resetFields();
      setCompAdvancedMode(false);
      message.success('数据接口创建成功');
      newObjectForm.setFieldsValue({ component_id: data.component_id ?? data.id });
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 归档 Mutation ----
  const archiveMutation = useMutation({
    mutationFn: apiArchiveFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      void queryClient.refetchQueries({ queryKey: ['flows', projectId] });
      setSelectedFlowId(null);
      message.success('流程已归档');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 恢复 Mutation ----
  const restoreMutation = useMutation({
    mutationFn: apiRestoreFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      message.success('流程已恢复');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 删除流程 Mutation ----
  const deleteFlowMutation = useMutation({
    mutationFn: apiDeleteFlow,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      // 若删除的是当前选中流程，清除选中状态
      setSelectedFlowId(null);
      setActiveRunId(null);
      message.success('流程已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 更新流程 Mutation ----
  const updateFlowMutation = useMutation({
    mutationFn: (vars: {
      flowId: string;
      displayName: string;
      departmentId?: string | null;
      operator?: string | null;
      projectId?: string | null;
      experimentalObjectCode?: string | null;
    }) =>
      apiUpdateFlow(
        vars.flowId,
        vars.displayName,
        vars.departmentId,
        vars.operator,
        vars.projectId,
        vars.experimentalObjectCode,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flows', projectId] });
      if (selectedFlowId) {
        void queryClient.invalidateQueries({ queryKey: ['flow', selectedFlowId] });
      }
      setEditModalOpen(false);
      editForm.resetFields();
      message.success('任务名称已更新');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 继续 / 取消 Mutation ----
  const resumeMutation = useMutation({
    mutationFn: apiResumeFlowRun,
    onSuccess: () => {
      void queryClient.refetchQueries({ queryKey: ['flow-runs', selectedFlowId] });
      message.success('已开始执行');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  const cancelMutation = useMutation({
    mutationFn: apiCancelFlowRun,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-run', activeRunId] });
      message.success('流程执行已取消');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  // ---- 删除运行 Mutation ----
  const deleteRunMutation = useMutation({
    mutationFn: apiDeleteFlowRun,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['flow-runs', selectedFlowId] });
      if (activeRunId) {
        setActiveRunId(null);
      }
      message.success('运行记录已删除');
    },
    onError: (err: unknown) => message.error(extractApiError(err)),
  });

  return {
    createMutation,
    createObjectMutation,
    publishCompMutation,
    archiveMutation,
    restoreMutation,
    deleteFlowMutation,
    updateFlowMutation,
    resumeMutation,
    cancelMutation,
    deleteRunMutation,
  };
}
