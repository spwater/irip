/**
 * TypeManagerModal — 实验对象类型管理弹窗。
 *
 * 从 ExperimentalObjectPage.tsx 提取。自包含状态管理：
 * - 新建类型（名称 + 描述）
 * - 编辑类型（改名 + 改描述）
 * - 删除类型（通过 ObjectTypesList 回调）
 * 通过 props 仅传递 open/onCancel。
 */

import { useState } from 'react';
import { Button, Input, Modal, Space, Typography, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  apiCreateObjectType,
  apiDeleteObjectType,
  apiUpdateObjectType,
  type ObjectTypeDictItem,
} from '@/api/standards-objects';
import { extractApiError } from '@/api/types';
import { ObjectTypesList } from './ObjectTypesList';

const { Text } = Typography;

export interface TypeManagerModalProps {
  open: boolean;
  onCancel: () => void;
}

export function TypeManagerModal(props: TypeManagerModalProps): JSX.Element {
  const { open, onCancel } = props;
  const queryClient = useQueryClient();
  const [newTypeName, setNewTypeName] = useState('');
  const [newTypeDesc, setNewTypeDesc] = useState('');
  const [editingType, setEditingType] = useState<ObjectTypeDictItem | null>(null);
  const [editTypeName, setEditTypeName] = useState('');
  const [editTypeDesc, setEditTypeDesc] = useState('');

  const handleCancel = (): void => {
    onCancel();
    setNewTypeName('');
    setNewTypeDesc('');
    setEditingType(null);
  };

  const handleCreateType = async (): Promise<void> => {
    if (!newTypeName.trim()) return;
    try {
      await apiCreateObjectType({
        display_name: newTypeName.trim(),
        description: newTypeDesc || undefined,
      });
      void queryClient.invalidateQueries({ queryKey: ['object-types'] });
      setNewTypeName('');
      setNewTypeDesc('');
      message.success('类型创建成功');
    } catch (err) {
      message.error(extractApiError(err));
    }
  };

  const handleDeleteType = async (item: ObjectTypeDictItem): Promise<void> => {
    try {
      await apiDeleteObjectType(item.id);
      void queryClient.invalidateQueries({ queryKey: ['object-types'] });
      message.success('类型已删除');
    } catch (err) {
      message.error(extractApiError(err));
    }
  };

  const handleUpdateType = async (): Promise<void> => {
    if (!editingType) return;
    try {
      await apiUpdateObjectType(editingType.id, {
        display_name: editTypeName,
        description: editTypeDesc || undefined,
      });
      void queryClient.invalidateQueries({ queryKey: ['object-types'] });
      setEditingType(null);
      message.success('类型已更新');
    } catch (err) {
      message.error(extractApiError(err));
    }
  };

  return (
    <Modal
      title="类型管理"
      open={open}
      onCancel={handleCancel}
      footer={null}
      width={650}
    >
      {/* 新建类型 */}
      <div style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="新类型名称"
            value={newTypeName}
            onChange={(e) => setNewTypeName(e.target.value)}
            maxLength={100}
          />
          <Button
            type="primary"
            onClick={handleCreateType}
          >
            新建
          </Button>
        </Space.Compact>
        <Input
          placeholder="描述（可选）"
          value={newTypeDesc}
          onChange={(e) => setNewTypeDesc(e.target.value)}
          maxLength={500}
          style={{ marginTop: 8 }}
        />
      </div>

      {/* 类型列表 */}
      <ObjectTypesList
        onEdit={(item) => {
          setEditingType(item);
          setEditTypeName(item.display_name);
          setEditTypeDesc(item.description ?? '');
        }}
        onDelete={handleDeleteType}
      />

      {/* 编辑类型 */}
      {editingType && (
        <div style={{ marginTop: 16, padding: 12, background: 'var(--ocean-surface-structural)', borderRadius: 8 }}>
          <Text strong>编辑类型: {editingType.code}</Text>
          <Input
            placeholder="类型名称"
            value={editTypeName}
            onChange={(e) => setEditTypeName(e.target.value)}
            maxLength={100}
            style={{ marginTop: 8 }}
          />
          <Input
            placeholder="描述"
            value={editTypeDesc}
            onChange={(e) => setEditTypeDesc(e.target.value)}
            maxLength={500}
            style={{ marginTop: 8 }}
          />
          <Space style={{ marginTop: 8 }}>
            <Button
              type="primary"
              size="small"
              onClick={handleUpdateType}
            >
              保存
            </Button>
            <Button size="small" onClick={() => setEditingType(null)}>
              取消
            </Button>
          </Space>
        </div>
      )}
    </Modal>
  );
}
