/**
 * 事实与溯源 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的事实、溯源相关类型和函数。
 */

export {
  type FactSummary,
  type FactDetail,
  type ProvenanceNode,
  type ProvenanceEdge,
  type ProvenanceGraph,
  type EvidenceSet,
  type Recipe,
  type DerivationRun,
  type DerivationRunOutput,
} from './types';

// Facts API functions
export {
  type FactData,
  apiCreateFact,
  apiListFacts,
  apiSearchFacts,
  apiSearchFactsByData,
  apiGetFact,
  apiGetFactData,
} from './facts-provenance';

// Provenance API functions
export {
  apiCreateEvidenceSet,
  apiListEvidenceSets,
  apiGetEvidenceSet,
  apiCreateRecipe,
  apiListRecipes,
  apiGetRecipe,
  apiGetDerivationRun,
  apiGetProvenanceGraph,
} from './facts-provenance';
