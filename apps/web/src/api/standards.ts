/**
 * 标准域 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的标准变量、工业对象、模板、方法、包相关类型和函数。
 */

export {
  type CursorPage,
  type VariableSummary,
  type VariableDetail,
  type VariableVersion,
  type IndustrialObject,
  type ObjectRelation,
  type DescendantsResponse,
  type TemplateSummary,
  type MethodSummary,
  type PackageSummary,
  type StandardStatus,
  type QualityLevel,
  type PreviewTable,
} from './client';

// Variables API
export {
  apiCreateVariable,
  apiListVariables,
  apiGetVariable,
  apiListVariableVersions,
  apiSubmitVariable,
  apiPublishVariable,
  apiRejectVariable,
  apiDeprecateVariable,
  apiResubmitVariable,
  apiAddVariableAlias,
  apiConvertUnits,
} from './client';

// Objects API
export {
  apiCreateObject,
  apiListObjects,
  apiGetObject,
  apiUpdateObject,
  apiUpdateObjectStatus,
  apiDeleteObject,
  apiAddObjectRelation,
  apiRemoveObjectRelation,
  apiListObjectRelations,
  apiGetObjectRelations,
  apiGetObjectDescendants,
} from './client';

// Templates API
export {
  apiCreateTemplate,
  apiListTemplates,
  apiGetTemplate,
  apiSubmitTemplate,
  apiPublishTemplate,
  apiRejectTemplate,
  apiDeprecateTemplate,
  apiAddObservationRequirement,
} from './client';

// Methods API
export {
  apiCreateMethod,
  apiListMethods,
  apiGetMethod,
  apiSubmitMethod,
  apiPublishMethod,
} from './client';

// Packages API
export {
  apiCreatePackage,
  apiListPackages,
  apiGetPackage,
  apiAddPackageRef,
  apiSubmitPackage,
  apiPublishPackage,
  apiRejectPackage,
} from './client';
