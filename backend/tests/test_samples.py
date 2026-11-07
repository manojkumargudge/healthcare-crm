"""Unit tests for app.samples (no DB)."""

import pytest
from pydantic import ValidationError

from app.samples import SampleDrop, coerce_samples, evaluate_sample_compliance


@pytest.mark.parametrize(
    ("qty", "expected"),
    [(0, 0), (3, 3), ("3", 3), (" 4 ", 4), (3.0, 3), ("3.0", 3)],
)
def test_sampledrop_accepts_integer_values(qty: object, expected: int) -> None:
    assert SampleDrop.model_validate({"product": "A", "qty": qty}).qty == expected


@pytest.mark.parametrize("qty", [None, -1, "-1", 2.5, "abc", "", True, False, [1], {}])
def test_sampledrop_rejects_bad_qty(qty: object) -> None:
    with pytest.raises(ValidationError):
        SampleDrop.model_validate({"product": "A", "qty": qty})


@pytest.mark.parametrize("product", ["", "   ", None, 5])
def test_sampledrop_rejects_bad_product(product: object) -> None:
    with pytest.raises(ValidationError):
        SampleDrop.model_validate({"product": product, "qty": 1})


def test_sampledrop_drops_unknown_keys_and_strips_product() -> None:
    assert SampleDrop.model_validate({"product": " A ", "qty": 1, "lot": "x"}).model_dump() == {"product": "A", "qty": 1}


def test_coerce_samples_skips_invalid_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="hcp_crm.samples")
    out = coerce_samples([{"product": "A", "qty": "2"}, {"product": "B"}, 7], source="unit")
    assert out == [{"product": "A", "qty": 2}]
    assert [r.message.split(" (")[0] for r in caplog.records] == [
        "unit: skipping invalid sample {'product': 'B'}",
        "unit: skipping invalid sample 7",
    ]


@pytest.mark.parametrize("raw", [None, "x", {"product": "A", "qty": 1}])
def test_coerce_samples_non_list(raw: object) -> None:
    assert coerce_samples(raw, source="unit") == []


def test_evaluate_matches_tool_contract() -> None:
    assert evaluate_sample_compliance("California", [{"product": "A", "qty": 3}]) == {
        "compliance_flag": True,
        "compliance_notes": "A: 3 units exceeds the 2-unit limit for California",
        "state_limit_applied": 2,
    }
