"""Unit tests for the `check_sample_compliance` tool's rule logic.

Uses an in-memory stand-in for the AsyncSession (the tool only calls
`db.get(HCP, id)`), so these run without Postgres.
"""

import json
from typing import Any

import pytest

from app.agent.tools import STATE_SAMPLE_LIMITS, build_tools
from app.models import HCP


class _FakeSession:
    def __init__(self, *hcps: HCP) -> None:
        self._hcps = {h.id: h for h in hcps}

    async def get(self, model: type, ident: str) -> Any:
        assert model is HCP
        return self._hcps.get(ident)


async def _check(state: str | None, samples: Any, *, raw: bool = False) -> dict[str, Any]:
    hcp = HCP(id="hcp-1", name="Dr. Unit", state=state)
    tool = next(t for t in build_tools(_FakeSession(hcp)) if t.name == "check_sample_compliance")  # type: ignore[arg-type]  # duck-typed fake stands in for AsyncSession (only .get is used)
    payload = samples if raw else json.dumps(samples)
    out = await tool.coroutine(hcp_id="hcp-1", samples_dropped=payload)  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    return json.loads(out)


def test_ruleset_values() -> None:
    assert STATE_SAMPLE_LIMITS == {"default": 4, "california": 2, "vermont": 0, "minnesota": 2}


@pytest.mark.parametrize(
    ("state", "expected_limit"),
    [
        ("California", 2),
        ("  california  ", 2),  # trimmed + case-insensitive
        ("MINNESOTA", 2),
        ("Vermont", 0),
        ("Karnataka", 4),  # unknown -> default
        (None, 4),
        ("", 4),
    ],
)
async def test_state_limit_lookup(state: str | None, expected_limit: int) -> None:
    result = await _check(state, [])
    assert result["state_limit_applied"] == expected_limit
    assert result["compliance_flag"] is False
    assert result["compliance_notes"] == "No compliance issues detected."


async def test_qty_at_limit_is_allowed() -> None:
    result = await _check("California", [{"product": "A", "qty": 2}])
    assert result["compliance_flag"] is False


async def test_qty_over_limit_is_flagged() -> None:
    result = await _check("California", [{"product": "A", "qty": 3}])
    assert result["compliance_flag"] is True
    assert result["compliance_notes"] == "A: 3 units exceeds the 2-unit limit for California"


async def test_vermont_flags_any_sample() -> None:
    result = await _check("Vermont", [{"product": "A", "qty": 1}])
    assert result["compliance_flag"] is True


async def test_multiple_violations_joined() -> None:
    result = await _check(
        "Minnesota",
        [{"product": "A", "qty": 3}, {"product": "B", "qty": 1}, {"product": "C", "qty": 9}],
    )
    assert result["compliance_flag"] is True
    assert result["compliance_notes"] == (
        "A: 3 units exceeds the 2-unit limit for Minnesota; C: 9 units exceeds the 2-unit limit for Minnesota"
    )


async def test_missing_product_and_state_labels() -> None:
    result = await _check(None, [{"qty": 5}])
    assert result["compliance_notes"] == "Unknown product: 5 units exceeds the 4-unit limit for this state"


async def test_missing_qty_treated_as_zero() -> None:
    result = await _check("Vermont", [{"product": "A"}])
    assert result["compliance_flag"] is False


async def test_accepts_already_parsed_list() -> None:
    result = await _check("California", [{"product": "A", "qty": 3}], raw=True)
    assert result["compliance_flag"] is True


async def test_invalid_json_returns_error() -> None:
    result = await _check("California", "not json", raw=True)
    assert result == {"error": "samples_dropped must be a valid JSON list string"}


async def test_unknown_hcp_returns_error() -> None:
    tool = next(t for t in build_tools(_FakeSession()) if t.name == "check_sample_compliance")  # type: ignore[arg-type]  # duck-typed fake stands in for AsyncSession (only .get is used)
    out = json.loads(await tool.coroutine(hcp_id="nope", samples_dropped="[]"))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert out == {"error": "No HCP found with id nope"}


# ------------------------------------------------- suspected bug probes ----
@pytest.mark.xfail(strict=True, raises=TypeError, reason="BUG-1: check_sample_compliance compares `qty > limit` without coercing/validating, so a non-int qty (string or null) raises TypeError. samples_dropped is typed list[dict], so the API accepts it.")
async def test_string_qty_is_compared_numerically() -> None:
    result = await _check("California", [{"product": "A", "qty": "3"}])
    assert result["compliance_flag"] is True


@pytest.mark.xfail(strict=True, raises=TypeError, reason="BUG-1: check_sample_compliance compares `qty > limit` without coercing/validating, so a non-int qty (string or null) raises TypeError. samples_dropped is typed list[dict], so the API accepts it.")
async def test_null_qty_does_not_crash() -> None:
    result = await _check("California", [{"product": "A", "qty": None}])
    assert result["compliance_flag"] is False
