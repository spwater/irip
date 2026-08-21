"""Shared timeline service dependency providers (single source of truth).

The research timeline router was split across three modules
(``research_timeline.py``, ``research_timeline_turns.py`` and
``research_timeline_bar.py``).  FastAPI's ``dependency_overrides`` map by
function object identity, so every module that injects a service must
reference the *same* function object.  This module is the canonical home for
those placeholder functions and their ``Annotated`` dependency aliases.

All three router modules and ``apps/api/composition/research.py`` import from
here so that a single ``dependency_overrides`` entry covers every route.
"""

from typing import Annotated

from fastapi import Depends

from packages.research.timeline.analysis_service import AnalysisService
from packages.research.timeline.conclusion_bar_service import ConclusionBarService
from packages.research.timeline.conclusion_service import ConclusionService
from packages.research.timeline.recommendation_service import RecommendationService
from packages.research.timeline.timeline_query_service import TimelineQueryService
from packages.research.timeline.turn_service import TurnService

# ---- DI placeholders (overridden by composition/research.py) ----


def get_timeline_query_service() -> TimelineQueryService:
    raise NotImplementedError("overridden by composition")


def get_turn_service() -> TurnService:
    raise NotImplementedError("overridden by composition")


def get_conclusion_service() -> ConclusionService:
    raise NotImplementedError("overridden by composition")


def get_conclusion_bar_service() -> ConclusionBarService:
    raise NotImplementedError("overridden by composition")


def get_recommendation_service() -> RecommendationService:
    raise NotImplementedError("overridden by composition")


def get_analysis_service() -> AnalysisService:
    raise NotImplementedError("overridden by composition")


# ---- Dependency aliases ----


TimelineQueryDep = Annotated[TimelineQueryService, Depends(get_timeline_query_service)]
TurnServiceDep = Annotated[TurnService, Depends(get_turn_service)]
ConclusionServiceDep = Annotated[ConclusionService, Depends(get_conclusion_service)]
ConclusionBarServiceDep = Annotated[ConclusionBarService, Depends(get_conclusion_bar_service)]
RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
