from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import FairpostEngine


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "private_fairness_cases.json"


def _cases() -> list[dict[str, object]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_anonymized_private_case_family(case: dict[str, object]) -> None:
    result = FairpostEngine().check(str(case["text"]))
    finding_ids = {item.id for item in result.findings}
    question_ids = {item.id for item in result.questions}

    assert set(case["finding_contains"]) <= finding_ids
    assert set(case["question_contains"]) <= question_ids
    assert finding_ids.isdisjoint(case["finding_excludes"])
    assert question_ids.isdisjoint(case["question_excludes"])


def test_fixture_is_anonymized_and_has_unique_stable_ids() -> None:
    cases = _cases()
    ids = [str(case["id"]) for case in cases]
    serialized = FIXTURE_PATH.read_text(encoding="utf-8")

    assert len(ids) == len(set(ids))
    assert all("@" not in str(case["text"]) for case in cases)
    assert all("http" not in str(case["text"]).casefold() for case in cases)
    assert "010-" not in serialized
    assert "02-" not in serialized
