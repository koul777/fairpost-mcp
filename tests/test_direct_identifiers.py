from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from core import ReviewEvent, ReviewPacketError
from core.direct_identifiers import (
    DIRECT_IDENTIFIER_PATTERNS,
    contains_direct_identifier,
    direct_identifier_kinds,
    direct_identifier_web_bundle,
    mask_direct_identifiers,
)
from mcp_server.assisted_review import _redact_external_text


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "direct_identifier_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
POSITIVE = [case for case in CASES if case["kinds"]]
NEGATIVE = [case for case in CASES if not case["kinds"]]


def _text(case: dict) -> str:
    return "".join(case["parts"])


def _event(note: str) -> ReviewEvent:
    return ReviewEvent(
        event_id="evt-identifier",
        stage="analysis",
        role="auditor",
        action="note",
        occurred_at="2026-10-03T09:00:00+09:00",
        note=note,
    )


def test_fixture_covers_required_shapes() -> None:
    ids = {case["id"] for case in CASES}
    assert {
        "email-ascii",
        "mobile-hyphen",
        "landline-seoul",
        "landline-gyeonggi",
        "rrn-hyphen",
        "rrn-letter-prefixed",
        "date-iso",
        "salary-man-won",
        "ncs-code-8-digits",
        "ncs-code-10-digits",
        "posting-id-hyphen",
        "time-range",
        "year-range",
    } <= ids
    assert len(ids) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_python_detection_matches_fixture(case: dict) -> None:
    text = _text(case)
    assert list(direct_identifier_kinds(text)) == case["kinds"]
    assert contains_direct_identifier(text) is bool(case["kinds"])
    expected_mask = case.get("masked", text)
    assert mask_direct_identifiers(text) == expected_mask


@pytest.mark.parametrize("case", POSITIVE, ids=[case["id"] for case in POSITIVE])
def test_review_packet_rejects_and_assisted_review_masks_same_values(
    case: dict,
) -> None:
    text = _text(case)
    with pytest.raises(ReviewPacketError, match="direct personal identifiers"):
        replace(_event("정상 메모"), note=f"메모 {text}")
    masked = _redact_external_text(text)
    assert masked == case["masked"]
    assert not contains_direct_identifier(masked)


@pytest.mark.parametrize("case", NEGATIVE, ids=[case["id"] for case in NEGATIVE])
def test_review_packet_and_masking_leave_negatives_alone(case: dict) -> None:
    text = _text(case)
    assert _event(text).note == text
    assert _redact_external_text(text) == text


def test_web_bundle_carries_the_single_source() -> None:
    bundle_text = (ROOT / "web" / "data.js").read_text(encoding="utf-8")
    prefix = "window.FAIRPOST_DATA="
    assert bundle_text.startswith(prefix)
    payload = json.loads(bundle_text[len(prefix) :].rstrip().rstrip(";"))
    assert payload["direct_identifiers"] == direct_identifier_web_bundle()
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "FAIRPOST_DATA.direct_identifiers" in app
    # No hand-written identifier regex may come back into the browser code.
    assert "01[016789]" not in app
    assert "@[^" not in app


def test_patterns_avoid_engine_specific_regex_syntax() -> None:
    for pattern in DIRECT_IDENTIFIER_PATTERNS:
        source = pattern.source
        for token in ("\\d", "\\w", "\\s", "\\b", "(?i", "(?P"):
            assert token not in source, (pattern.kind, token)


def test_fixture_does_not_trip_release_privacy_scan() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_distribution", ROOT / "tools" / "verify_distribution.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in (
        "tests/fixtures/direct_identifier_cases.json",
        "tests/test_direct_identifiers.py",
        "tests/web_identifier_runner.cjs",
    ):
        payload = (ROOT / relative).read_bytes()
        assert module._privacy_kinds(payload) == [], relative


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
def test_browser_detection_and_masking_match_python() -> None:
    completed = subprocess.run(
        ["node", "tests/web_identifier_runner.cjs", str(FIXTURE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = json.loads(completed.stdout)
    assert output["available"] is True
    for case in CASES:
        text = _text(case)
        assert output["results"][case["id"]] == {
            "kinds": list(direct_identifier_kinds(text)),
            "masked": mask_direct_identifiers(text),
        }, case["id"]


def test_long_unspaced_text_is_linear_enough() -> None:
    import time

    text = "가" * 4000 + "a" * 4000
    started = time.perf_counter()
    for _ in range(5):
        assert not contains_direct_identifier(text)
        assert mask_direct_identifiers(text) == text
    assert time.perf_counter() - started < 2.0
    assert re.fullmatch(r"\[이메일 마스킹\]", mask_direct_identifiers("x" * 64 + "@example.com"))
