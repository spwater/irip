import { useState } from 'react';
import {
  Avatar,
  Button,
  Card,
  Form,
  Input,
  Space,
  Typography,
  Upload,
  message,
} from 'antd';
import { UserOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  apiChangePassword,
  apiGetProfile,
  apiUpdateProfile,
  apiUploadAvatar,
  type Profile,
} from '@/api/account';
import { extractApiError } from '@/api/types';
import { useAuthStore } from '@/features/auth/AuthProvider';

const { Text } = Typography;

/**
 * 个人设置页（irip-ai-collab）。
 *
 * 三个 Card 区域：
 * 1. 头像设置：预览 + 上传（限制 jpg/png/gif < 2MB）
 * 2. 显示名设置：Input + 保存
 * 3. 密码修改：Form（旧密码 + 新密码 + 确认）→ 成功后触发 logout 跳转登录
 */
export function PersonalSettings(): JSX.Element {
  const queryClient = useQueryClient();
  const logout = useAuthStore((s) => s.logout);
  const [displayName, setDisplayName] = useState('');
  const [passwordForm] = Form.useForm();
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);

  // 查询个人信息
  const { data: profile, isLoading } = useQuery({
    queryKey: ['account-profile'],
    queryFn: apiGetProfile,
  });

  // 初始化表单数据
  if (profile && displayName === '' && avatarUrl === null) {
    setDisplayName(profile.display_name);
    setAvatarUrl(profile.avatar_url);
  }

  // 更新显示名 mutation
  const updateProfileMutation = useMutation({
    mutationFn: (params: { display_name?: string; avatar_url?: string }) =>
      apiUpdateProfile(params),
    onSuccess: (data: Profile) => {
      setDisplayName(data.display_name);
      setAvatarUrl(data.avatar_url);
      void queryClient.invalidateQueries({ queryKey: ['account-profile'] });
      message.success('个人信息更新成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // 上传头像 mutation
  const uploadAvatarMutation = useMutation({
    mutationFn: (file: File) => apiUploadAvatar(file),
    onSuccess: (data: { avatar_url: string }) => {
      setAvatarUrl(data.avatar_url);
      void queryClient.invalidateQueries({ queryKey: ['account-profile'] });
      message.success('头像上传成功');
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // 修改密码 mutation
  const changePasswordMutation = useMutation({
    mutationFn: (params: { old_password: string; new_password: string }) =>
      apiChangePassword(params),
    onSuccess: () => {
      message.success('密码修改成功，请重新登录');
      // 触发 logout 跳转登录页
      void logout();
    },
    onError: (err: unknown) => {
      message.error(extractApiError(err));
    },
  });

  // 头像上传前校验
  const beforeUpload = (file: File): boolean => {
    const allowedTypes = ['image/jpeg', 'image/png', 'image/gif'];
    if (!allowedTypes.includes(file.type)) {
      message.error('仅支持 JPG/PNG/GIF 格式');
      return false;
    }
    const maxSize = 2 * 1024 * 1024;
    if (file.size > maxSize) {
      message.error('头像文件不能超过 2MB');
      return false;
    }
    // 手动上传
    uploadAvatarMutation.mutate(file);
    return false; // 阻止 antd 自动上传
  };

  // 保存显示名
  const handleSaveDisplayName = (): void => {
    const trimmed = displayName.trim();
    if (!trimmed) {
      message.warning('显示名不能为空');
      return;
    }
    updateProfileMutation.mutate({ display_name: trimmed });
  };

  // 修改密码
  const handleChangePassword = async (): Promise<void> => {
    try {
      const values = await passwordForm.validateFields();
      changePasswordMutation.mutate({
        old_password: values.old_password,
        new_password: values.new_password,
      });
    } catch {
      // 校验失败
    }
  };

  return (
    <div style={{ maxWidth: 600 }}>
      {/* 头像设置 */}
      <Card title="头像设置" size="small" style={{ marginBottom: 16 }} loading={isLoading}>
        <Space direction="vertical" align="center" style={{ width: '100%' }}>
          <Avatar
            size={80}
            src={avatarUrl}
            icon={<UserOutlined />}
            style={{ backgroundColor: '#1686AE' }}
          />
          <Upload
            showUploadList={false}
            beforeUpload={beforeUpload}
            accept="image/jpeg,image/png,image/gif"
          >
            <Button
              loading={uploadAvatarMutation.isPending}
              size="small"
            >
              上传头像
            </Button>
          </Upload>
          <Text type="secondary" style={{ fontSize: 12 }}>
            支持 JPG/PNG/GIF，不超过 2MB
          </Text>
        </Space>
      </Card>

      {/* 显示名设置 */}
      <Card title="显示名设置" size="small" style={{ marginBottom: 16 }}>
        <Space style={{ width: '100%' }}>
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="输入显示名"
            maxLength={100}
            style={{ flex: 1 }}
          />
          <Button
            type="primary"
            onClick={handleSaveDisplayName}
            loading={updateProfileMutation.isPending}
          >
            保存
          </Button>
        </Space>
      </Card>

      {/* 密码修改 */}
      <Card title="修改密码" size="small">
        <Form form={passwordForm} layout="vertical">
          <Form.Item
            name="old_password"
            label="旧密码"
            rules={[{ required: true, message: '请输入旧密码' }]}
          >
            <Input.Password placeholder="输入当前密码" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少 6 位' },
            ]}
          >
            <Input.Password placeholder="输入新密码（至少 6 位）" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('两次输入的密码不一致'));
                },
              }),
            ]}
          >
            <Input.Password placeholder="再次输入新密码" />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              danger
              onClick={handleChangePassword}
              loading={changePasswordMutation.isPending}
            >
              修改密码
            </Button>
            <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
              修改成功后需重新登录
            </Text>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}

export default PersonalSettings;
