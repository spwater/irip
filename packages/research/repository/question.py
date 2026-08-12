"""研究问题版本子仓库 — 已废弃。

Timeline refactoring (2026-08-12): research_question_version 表已删除，
问题文本现在只存在于 ResearchTurn.question_text_snapshot 中。
此文件保留为空 stub 以避免导入链断裂，所有方法已移除。
"""

# This module is intentionally empty. The QuestionRepository class has been
# removed in the timeline refactoring. All question-related operations are now
# handled by TurnService in packages/research/timeline/turn_service.py.
