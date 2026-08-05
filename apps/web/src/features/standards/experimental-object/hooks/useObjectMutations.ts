/**
 * useObjectMutations — ExperimentalObjectPage 所有 useMutation 声明。
 *
 * 从 ExperimentalObjectPage.tsx 提取。统一管理 5 个 mutation 的
 * onSuccess/onError + queryClient.invalidateQueries。
 */

import type { FormInstance } from 'antd';
import { message } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  apiCreateObject,
  apiDeleteObject,
  apiUpdateObject,
  apiUpdateObjectStatus,
} from '@/api/standards-objects';
import { apiPublishComponent } from '@/api/equipment-flows';
import { extractApiError } from '@/api/types';
import type { IndustrialObject } from '@/api/types';

export interface UseObjectMutationsParams {
  form: FormInstance;
  compForm: FormInstance;
  setModalOpen: (open: boolean) => void;
  setEditingItem: (item: IndustrialObject | null) => void;
  setCompCreateModalOpen: (open: boolean) => void;
}

export type UseObjectMutationsResult = {
  createMutation: ReturnType<typeof useMutation>;
  updateMutation: ReturnType<typeof useMutation>;
  publishCompMutation: ReturnType<typeof useMutation>;
  statusMutation: ReturnType<typeof useMutation>;
  deleteMutation: ReturnType<typeof useMutation>;
};

export function useObjectMutations(
  params: UseObjectMutationsParams,
) {
  const { form, compForm, setModalOpen, setEditingItem, setCompCreateModalOpen } =
    params;

  const queryClient = useQueryClient();

  // ---- 创建 Mutation ----
  const createMutation = useMutation({
    mutationFn: apiCreateObject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
      setModalOpen(false);
      form.resetFields();
      message.success('实验对象创建成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 编辑 Mutation ----
  const updateMutation = useMutation({
    mutationFn: (args: {
      id: string;
      body: {
        display_name: string;
        description?: string | null;
        object_type?: string;
        department_id?: string | null;
        visible_departments?: string[] | null;
        component_id?: string | null;
      };
    }) => apiUpdateObject(args.id, args.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
      setModalOpen(false);
      setEditingItem(null);
      form.resetFields();
      message.success('实验对象更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 新建数据接口 Mutation ----
  const publishCompMutation = useMutation({
    mutationFn: apiPublishComponent,
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['components'] });
      setCompCreateModalOpen(false);
      compForm.resetFields();
      message.success('数据接口创建成功');
      // 自动选中新建的接口
      form.setFieldsValue({ component_id: data.id });
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 状态切换 Mutation ----
  const statusMutation = useMutation({
    mutationFn: (args: {
      id: string;
      body: { status: 'active' | 'inactive' };
    }) => apiUpdateObjectStatus(args.id, args.body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
      message.success('状态更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // ---- 删除 Mutation ----
  const deleteMutation = useMutation({
    mutationFn: apiDeleteObject,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['exp-objects'] });
      void queryClient.invalidateQueries({ queryKey: ['objects'] });
      setModalOpen(false);
      setEditingItem(null);
      form.resetFields();
      message.success('实验对象已删除');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  return {
    createMutation,
    updateMutation,
    publishCompMutation,
    statusMutation,
    deleteMutation,
  };
}
