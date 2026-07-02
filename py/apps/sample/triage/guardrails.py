"""Post-processing guardrails for triage output."""

import re

from models import Category
from models import Team
from models import TriageDecision
from models import TriageRequest
from models import TriageResponse
from text_utils import truncate

# Fallback team for each real-signal category, applied only when the model
# returns "None" for a category that must route somewhere.
_DEFAULT_TEAM_BY_CATEGORY: dict[Category, Team] = {
    Category.ACCESS: Team.IDENTITY,
    Category.HULL: Team.SYSTEMS,
    Category.COMMS: Team.COMMS,
    Category.SOFTWARE: Team.SOFTWARE,
    Category.THREAT: Team.THREAT,
    Category.DATA: Team.TELEMETRY,
    Category.BRIEFING: Team.SYSTEMS,
}

# Narrow, high-precision phrases that must always escalate ("no exceptions").
# Kept deliberately tight so we don't hurt escalation precision.
_ALWAYS_ESCALATE_PHRASES = (
    "hull breach",
    "hull rupture",
    "decompress",
    "atmospheric compromise",
    "life support failure",
    "life-support failure",
    "containment breach",
    "restricted zone",
    "restricted-zone",
)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    separator_tolerant = re.escape(phrase).replace(r"\ ", r"[-\s]+").replace(r"\-", r"[-\s]+")
    return re.compile(rf"(?<!\w){separator_tolerant}(?!\w)", re.IGNORECASE)


_ALWAYS_ESCALATE_PATTERNS = tuple(_phrase_pattern(phrase) for phrase in _ALWAYS_ESCALATE_PHRASES)


def clamp_text(text: str, max_chars: int) -> str:
    """Truncate overly long free text so a huge payload can't blow up a call."""
    return truncate(text, max_chars, suffix=" …[truncated]")


def _must_always_escalate(req: TriageRequest) -> bool:
    haystack = f"{req.subject}\n{req.description}"
    return any(pattern.search(haystack) is not None for pattern in _ALWAYS_ESCALATE_PATTERNS)


def apply_guardrails(decision: TriageDecision, req: TriageRequest) -> TriageResponse:
    """Enforce contract invariants and inject the request's ticket_id.

    The model output (``decision``) never carries ``ticket_id`` -- the server owns
    it -- so the final response is assembled here from the guarded decision plus
    ``req.ticket_id``.
    """
    team = decision.assigned_team
    if decision.category == Category.NOT_SIGNAL:
        team = Team.NONE
    elif team == Team.NONE:
        team = _DEFAULT_TEAM_BY_CATEGORY.get(decision.category, Team.NONE)

    needs_escalation = decision.needs_escalation or _must_always_escalate(req)
    missing_information = list(dict.fromkeys(decision.missing_information))

    return TriageResponse(
        ticket_id=req.ticket_id,
        category=decision.category,
        priority=decision.priority,
        assigned_team=team,
        needs_escalation=needs_escalation,
        missing_information=missing_information,
        next_best_action=decision.next_best_action,
        remediation_steps=decision.remediation_steps,
    )
