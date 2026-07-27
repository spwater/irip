/**
 * 溯源与参数 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的溯源（证据集/配方/推导运行/溯源图）和参数管理相关类型和函数。
 * 通过 re-export 保持与 client.ts 的兼容性。
 */

export {
  type ProvenanceNode,
  type ProvenanceEdge,
  type ProvenanceGraph,
  type EvidenceSet,
  type Recipe,
  type DerivationRun,
  type DerivationRunOutput,
  type ParameterSummary,
  type ParameterDetail,
  type ParameterVersion,
  type ParameterCandidate,
  apiCreateEvidenceSet,
  apiListEvidenceSets,
  apiFreezeEvidenceSet,
  apiGetEvidenceSet,
  apiListEvidenceSetMembers,
  apiCreateRecipe,
  apiPublishRecipe,
  apiListRecipes,
  apiGetRecipe,
  apiCreateDerivationRun,
  apiReplayDerivation,
  apiGetDerivationRun,
  apiListDerivationRuns,
  apiGetProvenanceGraph,
  apiCreateParameter,
  apiListParameters,
  apiGetParameter,
  apiListParameterVersions,
  apiGetParameterVersion,
  apiCreateCandidate,
  apiListCandidates,
  apiApproveCandidate,
  apiRejectCandidate,
  apiCheckStaleness,
  apiDeprecateParameter,
} from './client';
