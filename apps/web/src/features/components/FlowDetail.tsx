/**
 * FlowDetail — 流程详情页面（编排层）。
 *
 * 从 1755 行单函数组件精简为编排层：
 * 调用 hooks 获取数据 + 组合子组件，目标 < 200 行。
 * 所有业务逻辑已提取到 hooks/ 和 components/ 子目录。
 */

import { useState, useRef } from 'react';
import { Form, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { apiUploadFile } from '@/api/models-ai';
import { extractApiError } from '@/api/types';
import { useAuthStore } from '@/features/auth/AuthProvider';
import { buildManifestYaml, FORM_FIELD_NAMES, FRESH_FORM_VALUES } from '@/shared/component-utils';
import type { FlowSummary } from '@/api/equipment-flows';
import { canManage as canManageFn } from './flow-detail/utils/canManage';
import { useFlowQueries } from './flow-detail/hooks/useFlowQueries';
import { useFlowMutations } from './flow-detail/hooks/useFlowMutations';
import { useBatchExecute } from './flow-detail/hooks/useBatchExecute';
import { FlowListTable } from './flow-detail/components/FlowListTable';
import { FlowRunTable } from './flow-detail/components/FlowRunTable';
import { CreateFlowModal } from './flow-detail/components/CreateFlowModal';
import { EditFlowModal } from './flow-detail/components/EditFlowModal';
import { NewObjectModal } from './flow-detail/components/NewObjectModal';
import { NewComponentModal } from './flow-detail/components/NewComponentModal';
import { BatchExecuteModal } from './flow-detail/components/BatchExecuteModal';

export function FlowDetail({
  projectId,
  projectStatus,
}: {
  projectId?: string;
  projectStatus?: string;
} = {}): JSX.Element {
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((s) => s.user);
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editFlowId, setEditFlowId] = useState<string | null>(null);
  const [editForm] = Form.useForm();
  const [dataRunId, setDataRunId] = useState<string | null>(null);
  const [factModalOpen, setFactModalOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const artifactMapRef = useRef<Record<string, string>>({});
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [flowPageSize, setFlowPageSize] = useState(10);
  const [runPageSize, setRunPageSize] = useState(10);
  const [createForm] = Form.useForm();
  const [runForm] = Form.useForm();
  const [selectedType, setSelectedType] = useState<string | undefined>(undefined);
  const [newObjectModalOpen, setNewObjectModalOpen] = useState(false);
  const [newObjectForm] = Form.useForm();
  const [compCreateModalOpen, setCompCreateModalOpen] = useState(false);
  const [compForm] = Form.useForm();
  const [compAdvancedMode, setCompAdvancedMode] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [deptFilter, setDeptFilter] = useState<string | undefined>(undefined);
  const [equipFilter, setEquipFilter] = useState<string | undefined>(undefined);

  // ---- hooks ----
  const queries = useFlowQueries({
    projectId, selectedFlowId, showArchived, deptFilter, equipFilter, createForm,
  });
  const mutations = useFlowMutations({
    projectId, selectedFlowId, activeRunId,
    createForm, editForm, newObjectForm, compForm,
    setCreateModalOpen, setEditModalOpen, setEditFlowId,
    setSelectedFlowId, setActiveRunId, setNewObjectModalOpen,
    setCompCreateModalOpen, setCompAdvancedMode,
  });
  const batch = useBatchExecute({
    selectedFlowId, flow: queries.flow, compMap: queries.compMap, componentOptions: queries.componentOptions,
  });

  const canManage = (flow: FlowSummary | undefined | null): boolean => canManageFn(flow, currentUser);

  // ---- handlers ----
  const handleCreate = async (): Promise<void> => {
    try {
      const values = await createForm.validateFields();
      const expCode = (values.experimental_object_code as string) ?? '';
      mutations.createMutation.mutate({
        display_name: values.display_name,
        department_id: (values.department_id as string) ?? null,
        project_id: projectId ?? null,
        operator: (values.operator as string) ?? '',
        experimental_object_code: expCode || null,
      });
    } catch { /* 校验失败 */ }
  };

  const handleEditOk = async (): Promise<void> => {
    try {
      const values = await editForm.validateFields(['display_name', 'department_id', 'operator', 'experimental_object_code']);
      if (editFlowId) {
        mutations.updateFlowMutation.mutate({
          flowId: editFlowId,
          displayName: values.display_name as string,
          departmentId: (values.department_id as string) ?? null,
          operator: (values.operator as string) ?? null,
          projectId: projectId ?? null,
          experimentalObjectCode: (values.experimental_object_code as string) ?? null,
        });
      }
    } catch { /* 校验失败 */ }
  };

  const handleNewObjectOk = async (): Promise<void> => {
    try {
      const values = await newObjectForm.validateFields();
      mutations.createObjectMutation.mutate({
        display_name: values.display_name as string,
        object_type: (values.object_type as string) ?? 'unknown',
        description: (values.description as string) ?? undefined,
        department_id: (values.department_id as string) ?? undefined,
        visible_departments: (values.visible_departments as string[]) ?? undefined,
        component_id: (values.component_id as string) ?? undefined,
      });
    } catch { /* validation error */ }
  };

  const handleNewCompOk = async (): Promise<void> => {
    try {
      if (compAdvancedMode) {
        const values = await compForm.validateFields(['manifest_yaml', 'department_id', 'visible_departments']);
        mutations.publishCompMutation.mutate({
          manifest_yaml: values.manifest_yaml as string,
          department_id: (values.department_id as string) ?? null,
          visible_departments: (values.visible_departments as string[] | undefined) ?? null,
        });
      } else {
        const values = await compForm.validateFields([...FORM_FIELD_NAMES, 'department_id', 'visible_departments']);
        const yaml = buildManifestYaml({
          display_name: values.display_name as string,
          description: (values.description as string) ?? '',
          prompt: (values.prompt as string) ?? '',
          tool_type: (values.tool_type as string) ?? 'llm_converter',
        });
        mutations.publishCompMutation.mutate({
          manifest_yaml: yaml,
          department_id: (values.department_id as string) ?? null,
          visible_departments: (values.visible_departments as string[] | undefined) ?? null,
        });
      }
    } catch { /* validation error */ }
  };

  const openNewObjectFromCreate = (): void => {
    newObjectForm.resetFields();
    if (selectedType) newObjectForm.setFieldsValue({ object_type: selectedType });
    setNewObjectModalOpen(true);
  };

  const openNewObjectFromEdit = (): void => {
    newObjectForm.resetFields();
    setNewObjectModalOpen(true);
  };

  const openNewComponent = (): void => {
    const curDept = newObjectForm.getFieldValue('department_id') as string | undefined;
    const curName = newObjectForm.getFieldValue('display_name') as string | undefined;
    compForm.resetFields();
    setCompAdvancedMode(false);
    compForm.setFieldsValue({
      ...FRESH_FORM_VALUES, tool_type: 'llm_converter',
      department_id: curDept, display_name: curName ? `${curName}接口` : undefined,
    });
    setCompCreateModalOpen(true);
  };

  const openBatch = (): void => {
    batch.setBatchFiles([]);
    batch.setBatchProgress(null);
    batch.setBatchSelectedComp(queries.objCompName ?? queries.runNode?.component_name ?? undefined);
    batch.setBatchOperator(queries.flow?.operator ?? '');
    batch.setBatchPrompt('');
    batch.setBatchResults(null);
    batch.setBatchModalOpen(true);
  };

  const onMakePublic = async (id: string): Promise<void> => {
    try {
      await import('@/api/client').then(({ http }) =>
        http.patch(`/flows/${id}`, { visibility_scope: 'tree' })
      );
      message.success('流程已公开');
      void queryClient.invalidateQueries({ queryKey: ['flows'] });
    } catch (err) {
      message.error(extractApiError(err));
    }
  };

  return (
    <div>
      <FlowListTable
        flows={queries.flows}
        loading={queries.listLoading}
        flowPageSize={flowPageSize}
        setFlowPageSize={setFlowPageSize}
        onSelectFlow={setSelectedFlowId}
        showArchived={showArchived}
        setShowArchived={setShowArchived}
        projectStatus={projectStatus}
        onCreateFlow={() => setCreateModalOpen(true)}
        deptFilter={deptFilter}
        setDeptFilter={setDeptFilter}
        equipFilter={equipFilter}
        setEquipFilter={setEquipFilter}
        deptOptions={queries.deptOptions}
        equipOptions={queries.equipOptions}
        objMap={queries.objMap}
        deptMap={queries.deptMap}
        canManage={canManage}
        onEdit={(record) => {
          setEditFlowId(record.id);
          editForm.setFieldsValue({
            display_name: record.display_name, code: record.code,
            department_id: record.department_id ?? undefined,
            operator: record.operator ?? undefined,
            experimental_object_code: record.experimental_object_code ?? undefined,
          });
          setEditModalOpen(true);
        }}
        onArchive={(id) => mutations.archiveMutation.mutate(id)}
        onRestore={(id) => mutations.restoreMutation.mutate(id)}
        onDelete={(id) => mutations.deleteFlowMutation.mutate(id)}
        onMakePublic={onMakePublic}
        archivePending={mutations.archiveMutation.isPending}
        restorePending={mutations.restoreMutation.isPending}
        deletePending={mutations.deleteFlowMutation.isPending}
      />

      <FlowRunTable
        selectedFlowId={selectedFlowId}
        flow={queries.flow}
        runs={queries.runs}
        runsLoading={queries.runsLoading}
        runPageSize={runPageSize}
        setRunPageSize={setRunPageSize}
        activeRunId={activeRunId}
        compMap={queries.compMap}
        equipMap={queries.equipMap}
        deptMap={queries.deptMap}
        canManage={canManage}
        onResume={(id) => mutations.resumeMutation.mutate(id)}
        onCancel={(id) => mutations.cancelMutation.mutate(id)}
        onDeleteRun={(id) => mutations.deleteRunMutation.mutate(id)}
        deleteRunPending={mutations.deleteRunMutation.isPending}
        onOpenBatch={openBatch}
        projectId={projectId}
        factModalOpen={factModalOpen}
        setFactModalOpen={setFactModalOpen}
        dataRunId={dataRunId}
        setDataRunId={setDataRunId}
      />

      <CreateFlowModal
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => { setCreateModalOpen(false); createForm.resetFields(); setSelectedType(undefined); }}
        confirmLoading={mutations.createMutation.isPending}
        createForm={createForm}
        selectedType={selectedType}
        setSelectedType={setSelectedType}
        objectTypeOptions={queries.objectTypeOptions}
        objectOptions={queries.objectOptions}
        objMap={queries.objMap}
        onNewObject={openNewObjectFromCreate}
      />

      <EditFlowModal
        open={editModalOpen}
        onOk={handleEditOk}
        onCancel={() => { setEditModalOpen(false); editForm.resetFields(); }}
        confirmLoading={mutations.updateFlowMutation.isPending}
        editForm={editForm}
        objectOptions={queries.objectOptions}
        onNewObject={openNewObjectFromEdit}
      />

      <NewObjectModal
        open={newObjectModalOpen}
        onCancel={() => { setNewObjectModalOpen(false); newObjectForm.resetFields(); }}
        onOk={handleNewObjectOk}
        confirmLoading={mutations.createObjectMutation.isPending}
        newObjectForm={newObjectForm}
        currentUserDepartmentId={currentUser?.departmentId}
        objectTypeOptions={queries.objectTypeOptions}
        deptOptions={queries.deptOptions}
        deptTreeData={queries.deptTreeData}
        compOptionsForObj={queries.compOptionsForObj}
        onNewComponent={openNewComponent}
      />

      <NewComponentModal
        open={compCreateModalOpen}
        onOk={handleNewCompOk}
        onCancel={() => { setCompCreateModalOpen(false); compForm.resetFields(); setCompAdvancedMode(false); }}
        confirmLoading={mutations.publishCompMutation.isPending}
        compForm={compForm}
        compAdvancedMode={compAdvancedMode}
        setCompAdvancedMode={setCompAdvancedMode}
        ingestionToolOptions={queries.ingestionToolOptions}
        deptOptions={queries.deptOptions}
        deptTreeData={queries.deptTreeData}
      />

      <BatchExecuteModal
        open={batch.batchModalOpen}
        onCancel={() => {
          if (!batch.batchRunning) {
            batch.setBatchModalOpen(false);
            batch.setBatchFiles([]);
            batch.setBatchProgress(null);
            batch.setBatchSelectedComp(undefined);
            batch.setBatchOperator('');
            batch.setBatchResults(null);
          }
        }}
        batchRunning={batch.batchRunning}
        batchProgress={batch.batchProgress}
        batchResults={batch.batchResults}
        batchFiles={batch.batchFiles}
        batchSelectedComp={batch.batchSelectedComp}
        batchOperator={batch.batchOperator}
        batchPrompt={batch.batchPrompt}
        filteredCompOptions={queries.filteredCompOptions}
        compMap={queries.compMap}
        equipMap={queries.equipMap}
        toolTypeDisplayName={queries.toolTypeDisplayName}
        componentOptions={queries.componentOptions}
        setBatchFiles={batch.setBatchFiles}
        setBatchSelectedComp={batch.setBatchSelectedComp}
        setBatchOperator={batch.setBatchOperator}
        setBatchPrompt={batch.setBatchPrompt}
        setBatchModalOpen={batch.setBatchModalOpen}
        setBatchProgress={batch.setBatchProgress}
        setBatchResults={batch.setBatchResults}
        handleBatchExecute={batch.handleBatchExecute}
        handleBatchCancel={batch.handleBatchCancel}
      />

      {/* 隐藏的文件上传 input（供执行弹窗的 path 字段使用） */}
      <input
        ref={fileInputRef}
        type="file"
        style={{ display: 'none' }}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          const formKey = fileInputRef.current?.dataset.formkey;
          if (!formKey) return;
          try {
            const res = await apiUploadFile(file);
            runForm.setFieldValue(formKey, file.name);
            artifactMapRef.current[formKey] = `artifact:${res.artifact_id}`;
            message.success(`文件已上传: ${file.name}`);
          } catch (err) {
            message.error(`上传失败: ${err instanceof Error ? err.message : String(err)}`);
          } finally {
            if (fileInputRef.current) {
              fileInputRef.current.value = '';
              delete fileInputRef.current.dataset.formkey;
            }
          }
        }}
      />
    </div>
  );
}

export default FlowDetail;
