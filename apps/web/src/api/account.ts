/**
 * Account API — 个人账户管理 API 类型定义 + 请求函数。
 *
 * 端点（/api/v1/account）：
 *   GET   /profile    — 查询个人信息
 *   PATCH /profile    — 修改显示名/头像 URL
 *   POST  /password   — 修改密码
 *   POST  /avatar     — 上传头像
 */
import { http } from './client';

// ============================================================
// 类型定义
// ============================================================

/** 个人信息 */
export type Profile = {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  roles: string[];
  department_id: string | null;
};

// ============================================================
// API 响应类型
// ============================================================

type ProfileApiResponse = {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  roles: string[];
  department_id: string | null;
};

type AvatarUploadApiResponse = { avatar_url: string };

// ============================================================
// API 函数
// ============================================================

/** 查询个人信息 */
export async function apiGetProfile(): Promise<Profile> {
  const res = await http.get<ProfileApiResponse>('/account/profile');
  return {
    id: res.data.id,
    email: res.data.email,
    display_name: res.data.display_name,
    avatar_url: res.data.avatar_url,
    roles: res.data.roles ?? [],
    department_id: res.data.department_id,
  };
}

/** 修改个人信息（显示名 / 头像 URL） */
export async function apiUpdateProfile(params: {
  display_name?: string;
  avatar_url?: string;
}): Promise<Profile> {
  const res = await http.patch<ProfileApiResponse>('/account/profile', params);
  return {
    id: res.data.id,
    email: res.data.email,
    display_name: res.data.display_name,
    avatar_url: res.data.avatar_url,
    roles: res.data.roles ?? [],
    department_id: res.data.department_id,
  };
}

/** 修改密码（成功后需重新登录） */
export async function apiChangePassword(params: {
  old_password: string;
  new_password: string;
}): Promise<void> {
  await http.post('/account/password', params);
}

/** 上传头像到 MinIO，返回头像 URL */
export async function apiUploadAvatar(file: File): Promise<{ avatar_url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await http.post<AvatarUploadApiResponse>('/account/avatar', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return { avatar_url: res.data.avatar_url };
}
