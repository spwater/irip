/**
 * 事实与溯源 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的事实、观察值、溯源相关类型和函数。
 */

export {
  type FactSummary,
  type FactDetail,
  type FactRevision,
  type FactData,
  type RawObservation,
  type NormalizedObservation,
  type ObservationsResponse,
  type ProvenanceNode,
  type ProvenanceEdge,
  type ProvenanceGraph,
  type EvidenceSet,
  type Recipe,
  type DerivationRun,
  type DerivationRunOutput,
} from './client';

// Facts API functions
export {
  apiCreateFact,
  apiListFacts,
  apiSearchFacts,
  apiSearchFactsByData,
  apiGetFact,
  apiGetFactRevision,
  apiGetFactObservations,
  apiGetFactData,
} from './client';

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
} from './client';
