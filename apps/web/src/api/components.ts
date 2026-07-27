/**
 * 组件管理 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的组件注册表相关类型和函数。
 */

export {
  type ComponentSummary,
  type ComponentDetail,
  type ComponentVersionItem,
} from './client';

export {
  apiPublishComponent,
  apiListComponents,
  apiGetComponent,
} from './client';
