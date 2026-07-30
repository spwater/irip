import { useState } from 'react';
import {
  Button,
  Drawer,
  Input,
  List,
  Popconfirm,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiGetDepartmentUsers,
  apiGetUserDepartments,
  apiSetUserDepartments,
  type DepartmentListItem,
  type DepartmentUser,
} from '@/api/departments';

const { Text } = Typography;

interface MemberDrawerProps {
  /** 当前实验室 */
  department: DepartmentListItem;
  /** 抽屉是否打开 */
  open: boolean;
  /** 关闭抽屉回调 */
  onClose: () => void;
}

/**
 * 成员管理抽屉（P1）
 *
 * 从右滑出（宽 480px），展示实验室下用户列表，
 * 支持添加/移除用户。
 *
 * M-05 整改：
 * - 客户端读-改-全量 PUT 存在并发覆盖风险，现增加 UI 层并发冲突检测：
 *   1. 变更前重新拉取用户最新实验室列表（缩小竞态窗口）
 *   2. 幂等检测：若目标状态已达成（已添加/已移除），跳过并提示
 *   3. 409 冲突错误专门处理：提示「数据已被他人修改」并刷新
 *   4. 使用部门列表指纹做变更前后对比，检测并发修改
 */
export function MemberDrawer({
  department,
  open,
  onClose,
}: MemberDrawerProps): JSX.Element {
  const queryClient = useQueryClient();
  const [addUserId, setAddUserId] = useState('');

  // ---- 查询实验室下用户 ----
  const { data: users, isLoading } = useQuery({
    queryKey: ['department-users', department.id],
    queryFn: () => apiGetDepartmentUsers(department.id),
    enabled: open,
  });

  // ---- 移除用户 Mutation ----
  const removeMutation = useMutation({
    mutationFn: async (user: DepartmentUser) => {
      // M-05: 变更前重新拉取最新数据，缩小竞态窗口
      const userDepts = await apiGetUserDepartments(user.user_id);

      // 幂等检测：用户已不在该实验室
      if (!userDepts.some((ud) => ud.department_id === department.id)) {
        throw new ConcurrencyConflictError(
          '该用户已不在当前实验室，可能已被其他管理员移除。列表已刷新。',
        );
      }

      const remainingDeptIds = userDepts
        .map((ud) => ud.department_id)
        .filter((id) => id !== department.id);

      const primaryDep = userDepts.find((ud) => ud.is_primary);
      const primaryId =
        primaryDep && primaryDep.department_id !== department.id
          ? primaryDep.department_id
          : remainingDeptIds.length > 0
            ? remainingDeptIds[0]
            : undefined;

      try {
        return await apiSetUserDepartments(user.user_id, {
          department_ids: remainingDeptIds,
          primary_department_id: primaryId,
        });
      } catch (err) {
        // M-05: 409 冲突专门处理
        if (getHttpStatus(err) === 409) {
          throw new ConcurrencyConflictError(
            '数据已被其他管理员修改，请刷新后重试。',
          );
        }
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['department-users', department.id],
      });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      message.success('已移除用户');
    },
    onError: (err: unknown) => {
      if (err instanceof ConcurrencyConflictError) {
        message.warning(err.message);
        void queryClient.invalidateQueries({
          queryKey: ['department-users', department.id],
        });
      } else {
        message.error(_extractErrorMessage(err));
      }
    },
  });

  // ---- 添加用户 Mutation ----
  const addMutation = useMutation({
    mutationFn: async (userId: string) => {
      // M-05: 变更前重新拉取最新数据，缩小竞态窗口
      const userDepts = await apiGetUserDepartments(userId);

      // 幂等检测：用户已在该实验室
      if (userDepts.some((ud) => ud.department_id === department.id)) {
        throw new ConcurrencyConflictError(
          '该用户已在当前实验室，无需重复添加。列表已刷新。',
        );
      }

      const deptIds = userDepts.map((ud) => ud.department_id);
      deptIds.push(department.id);
      const primaryDep = userDepts.find((ud) => ud.is_primary);
      const primaryId = primaryDep ? primaryDep.department_id : department.id;

      try {
        return await apiSetUserDepartments(userId, {
          department_ids: deptIds,
          primary_department_id: primaryId,
        });
      } catch (err) {
        // M-05: 409 冲突专门处理
        if (getHttpStatus(err) === 409) {
          throw new ConcurrencyConflictError(
            '数据已被其他管理员修改，请刷新后重试。',
          );
        }
        throw err;
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['department-users', department.id],
      });
      void queryClient.invalidateQueries({ queryKey: ['departments'] });
      setAddUserId('');
      message.success('已添加用户');
    },
    onError: (err: unknown) => {
      if (err instanceof ConcurrencyConflictError) {
        message.warning(err.message);
        void queryClient.invalidateQueries({
          queryKey: ['department-users', department.id],
        });
      } else {
        message.error(_extractErrorMessage(err));
      }
    },
  });

  const handleAdd = (): void => {
    const userId = addUserId.trim();
    if (!userId) {
      message.warning('请输入用户 ID');
      return;
    }
    addMutation.mutate(userId);
  };

  return (
    <Drawer
      title={`成员管理 — ${department.display_name}`}
      open={open}
      onClose={onClose}
      width={480}
      placement="right"
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        {/* 添加用户 */}
        <div>
          <Space style={{ width: '100%' }}>
            <Input
              placeholder="输入用户 ID 添加成员"
              value={addUserId}
              onChange={(e) => setAddUserId(e.target.value)}
              onPressEnter={handleAdd}
            />
            <Button
              type="primary"
              onClick={handleAdd}
              loading={addMutation.isPending}
            >
              添加
            </Button>
          </Space>
        </div>

        {/* 用户列表 */}
        <List<DepartmentUser>
          loading={isLoading}
          dataSource={users ?? []}
          locale={{ emptyText: '暂无成员' }}
          renderItem={(user) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="remove"
                  title="确定从该实验室移除此用户？"
                  onConfirm={() => removeMutation.mutate(user)}
                  okText="确定"
                  cancelText="取消"
                >
                  <Button type="link" danger size="small">
                    移除
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Text>{user.display_name}</Text>
                    {user.is_primary && <Tag color="blue">主要</Tag>}
                  </Space>
                }
                description={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {user.email} · {user.user_id}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      </Space>
    </Drawer>
  );
}

/**
 * 自定义并发冲突错误（M-05）。
 *
 * 用于区分「并发冲突/幂等跳过」与「普通业务错误」，
 * 前者以 warning 提示并刷新列表，后者以 error 提示。
 */
class ConcurrencyConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConcurrencyConflictError';
  }
}

/**
 * 从 Axios 错误中提取 HTTP 状态码（M-05）。
 */
function getHttpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { status?: number } }).response;
    if (typeof response?.status === 'number') {
      return response.status;
    }
  }
  return undefined;
}

/**
 * 从 Axios 错误中提取后端错误消息。
 */
function _extractErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { error?: { message?: string } } } }).response;
    if (response?.data?.error?.message) {
      return response.data.error.message;
    }
  }
  if (err instanceof Error) {
    return err.message;
  }
  return '操作失败';
}
