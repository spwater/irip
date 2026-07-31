/**
 * 标准域 API 模块（F-23: 按领域拆分，标准层空表清理后精简版）
 *
 * 标准变量、模板、包相关 API 已随 migration 0057 删除，
 * 仅保留工业对象（Objects）与对象类型（Object Types）相关函数。
 */

export {
  type CursorPage,
  type IndustrialObject,
  type StandardStatus,
  type QualityLevel,
  type PreviewTable,
} from './types';

// Objects API
export {
  apiCreateObject,
  apiListObjects,
  apiGetObject,
  apiUpdateObject,
  apiUpdateObjectStatus,
  apiDeleteObject,
} from './standards-objects';

// Object Types API
export {
  apiListObjectTypes,
  apiCreateObjectType,
  apiUpdateObjectType,
  apiDeleteObjectType,
} from './standards-objects';

// Ingestions preview API
export {
  apiPreviewIngestion,
  apiPreviewSource,
} from './standards-objects';
