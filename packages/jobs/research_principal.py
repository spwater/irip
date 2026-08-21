"""Research task principal: validated identity context for async workers.

Ensures Outbox payloads only carry allowlisted resource IDs (actor_id,
department_id, workspace_id) and never include analysis text, prompts,
or tool results.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

#: Fields allowed in research Outbox payloads.
ALLOWED_FIELDS = frozenset({"actor_id", "department_id", "workspace_id"})


@dataclass(frozen=True)
class ResearchTaskPrincipal:
    """Validated principal for research async task execution.

    Carries only identity IDs — never analysis content.
    """

    actor_id: UUID
    department_id: UUID
    workspace_id: UUID

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ResearchTaskPrincipal:
        """Parse and validate a principal from an Outbox payload.

        Args:
            payload: Outbox event payload dict.

        Returns:
            Validated ResearchTaskPrincipal.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        try:
            return cls(
                actor_id=UUID(str(payload["actor_id"])),
                department_id=UUID(str(payload["department_id"])),
                workspace_id=UUID(str(payload["workspace_id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid research task principal payload: {exc}") from exc

    def as_kwargs(self) -> dict[str, str]:
        """Return as string kwargs for Celery task calls."""
        return {
            "actor_id": str(self.actor_id),
            "department_id": str(self.department_id),
            "workspace_id": str(self.workspace_id),
        }
