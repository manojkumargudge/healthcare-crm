"""Sample-drop validation and state compliance rules, shared by the HTTP API
and the LangGraph agent tools so both paths enforce the same rules."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator

logger = logging.getLogger("hcp_crm.samples")

# ---------------------------------------------------------------------------
# Simplified compliance ruleset (state -> max samples per product per visit).
# In a real system this would be pulled from a regulatory/compliance service.
# ---------------------------------------------------------------------------
STATE_SAMPLE_LIMITS = {
    "default": 4,
    "california": 2,
    "vermont": 0,  # Vermont restricts pharma sample/gift programs heavily
    "minnesota": 2,
}


class SampleDrop(BaseModel):
    """One dropped sample. `qty` accepts ints and integer-valued numbers or
    numeric strings ("3" -> 3); rejects null, negatives, fractions and bools."""

    product: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    qty: int = Field(ge=0)

    @field_validator("qty", mode="before")
    @classmethod
    def _reject_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("qty must be an integer, not a boolean")
        return v


def coerce_samples(raw: Any, *, source: str) -> list[dict[str, Any]]:
    """Validate untrusted sample items (e.g. LLM output) against SampleDrop.

    Invalid items are skipped with a logged warning rather than failing the
    whole operation. Returns plain dicts ready for the JSON column.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("%s: samples_dropped is not a list (%r); ignoring it", source, raw)
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(SampleDrop.model_validate(item).model_dump())
        except ValidationError as exc:
            logger.warning("%s: skipping invalid sample %r (%s)", source, item, exc.errors(include_url=False))
    return out


def evaluate_sample_compliance(state: str | None, samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the state sample limits. Pure function: no DB, no I/O.

    `samples` should already be validated (see SampleDrop / coerce_samples);
    any item whose qty still isn't a valid non-negative int is skipped with a
    warning so a bad item can never crash the check.
    """
    state_key = (state or "default").strip().lower()
    limit = STATE_SAMPLE_LIMITS.get(state_key, STATE_SAMPLE_LIMITS["default"])

    violations = []
    for s in samples:
        raw_qty = s.get("qty", 0)
        try:
            qty = SampleDrop.model_validate({"product": "x", "qty": raw_qty}).qty
        except ValidationError:
            logger.warning("compliance: skipping sample with invalid qty %r", s)
            continue
        if qty > limit:
            violations.append(
                f"{s.get('product', 'Unknown product')}: {qty} units exceeds the "
                f"{limit}-unit limit for {state or 'this state'}"
            )

    return {
        "compliance_flag": bool(violations),
        "compliance_notes": "; ".join(violations) if violations else "No compliance issues detected.",
        "state_limit_applied": limit,
    }
