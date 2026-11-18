"""Deal pipeline (03 section 4.5): lead -> qualified -> proposal -> won, any open stage -> lost (reason required)."""

from __future__ import annotations

from app.domain.errors import FieldError, InvalidState, ValidationFailed

STAGES = ("lead", "qualified", "proposal", "won", "lost")
TERMINAL = frozenset({"won", "lost"})
_NEXT = {"lead": "qualified", "qualified": "proposal", "proposal": "won"}
MAX_LOST_REASON = 500


def is_terminal(stage: str) -> bool:
    return stage in TERMINAL


def validate_transition(current: str, target: str, lost_reason: str | None) -> str | None:
    """Returns the cleaned lost reason (or None). Raises on any illegal move."""
    if current not in STAGES or target not in STAGES:
        raise ValidationFailed("unknown deal stage", errors=[FieldError("stage", "unknown stage")])
    if is_terminal(current):
        raise InvalidState(f"a {current} deal cannot change stage")
    if target == "lost":
        reason = (lost_reason or "").strip()
        if not reason:
            raise ValidationFailed(
                "a reason is required when a deal is lost",
                errors=[FieldError("lost_reason", "required")],
            )
        if len(reason) > MAX_LOST_REASON:
            raise ValidationFailed(
                "lost reason too long", errors=[FieldError("lost_reason", f"max {MAX_LOST_REASON}")]
            )
        return reason
    if _NEXT.get(current) != target:
        raise InvalidState(f"cannot move a deal from {current} to {target}")
    return None


def assert_can_invoice(stage: str) -> None:
    if stage != "won":
        raise InvalidState("only won deals can create an invoice")
