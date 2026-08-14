"""Versioned prompt constants for recommendation, candidate extraction, and synthesis.

提示词已迁移到 config/prompts.yaml，本模块通过 prompt_store 加载后
导出同名常量，保持向后兼容（已有 import 无需修改）。

当提示词内容变更时，修改 config/prompts.yaml 即可，无需改代码。
版本号变更需同步检查 DB 中已存储的 prompt_template_version 字段。
"""

from packages.ai.prompt_store import get_prompt

# ============================================================
# Recommendation prompts
# ============================================================

RECOMMENDATION_PROMPT_VERSION = get_prompt(
    "research_recommendation.version", "research-recommendation-v2"
)
RECOMMENDATION_OUTPUT_SCHEMA_VERSION = get_prompt(
    "research_recommendation.output_schema_version", "recommendation-output-v1"
)

RECOMMENDATION_SYSTEM_PROMPT = get_prompt(
    "research_recommendation.system_prompt"
)

RECOMMENDATION_USER_TEMPLATE = get_prompt(
    "research_recommendation.user_template"
)


# ============================================================
# Synthesis prompts
# ============================================================

SYNTHESIS_PROMPT_VERSION = get_prompt(
    "research_synthesis.version", "research-synthesis-v1"
)
SYNTHESIS_OUTPUT_SCHEMA_VERSION = get_prompt(
    "research_synthesis.output_schema_version", "synthesis-result-v1"
)

SYNTHESIS_SYSTEM_PROMPT = get_prompt(
    "research_synthesis.system_prompt"
)


# ============================================================
# Candidate extraction prompts
# ============================================================

CANDIDATE_EXTRACTION_PROMPT_VERSION = get_prompt(
    "candidate_extraction.version", "research-candidate-extraction-v1"
)
CANDIDATE_EXTRACTION_SCHEMA_VERSION = get_prompt(
    "candidate_extraction.output_schema_version", "candidate-extraction-output-v1"
)

CANDIDATE_EXTRACTION_SYSTEM_PROMPT = get_prompt(
    "candidate_extraction.system_prompt"
)
