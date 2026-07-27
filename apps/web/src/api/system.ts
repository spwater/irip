/**
 * 文件与系统 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的文件浏览/上传和系统健康相关类型和函数。
 * 通过 re-export 保持与 client.ts 的兼容性。
 */

export {
  type FileItem,
  type BrowseResponse,
  type UploadResponse,
  type HealthCheck,
  type SystemHealth,
  apiBrowseFiles,
  apiUploadFile,
  apiGetArtifactDownloadUrl,
  apiGetSystemHealth,
} from './client';
