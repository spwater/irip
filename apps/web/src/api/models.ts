/**
 * 模型管理 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的模型管理相关类型和函数。
 * 通过 re-export 保持与 client.ts 的兼容性。
 */

export {
  type ModelSummary,
  type ModelVersionSummary,
  type PredictionResult,
  apiCreateModel,
  apiListModels,
  apiGetModel,
  apiGetModelVersions,
  apiValidateModelVersion,
  apiPublishModelVersion,
  apiRollbackModel,
  apiPredictModel,
  apiDeprecateModel,
} from './client';
