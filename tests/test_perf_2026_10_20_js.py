"""The web engine's 2026-10-20 speed changes give what the plain algorithm gives.

web/engine.js restricts the slot accept patterns to the ones the whole posting
can hold, keeps the plans and a literal gate for every pattern list that is
scanned again and again, shares the scan of one pattern on one long text between
lists, finds single-chunk literals with indexOf, rejects heading candidates with
more than 30 letters early, skips the per-unit identity check with one native
scan and remembers the traits of a candidate's own text. tests/js_perf_2026_10_20.cjs
compares each of them with a reference version written without it.
"""
from __future__ import annotations

import json

import pytest

from web_engine import ROOT, run_node

pytestmark = pytest.mark.requires_node


def test_speed_changes_equal_reference_versions() -> None:
    result = json.loads(run_node([str(ROOT / "tests/js_perf_2026_10_20.cjs")]))
    assert result["keptUnits"] > 20000
    assert result["normalizedChecks"] >= 3000
    assert result["headingChecks"] >= 4000 and result["longRejected"] > 100
    assert result["listChecks"] > 10000 and result["gatedNonEmpty"] > 2000
    assert result["viewOnlyFound"] > 0
    assert result["slotChecks"] >= 250
    assert result["candidateChecks"] > 200 and result["candidateRejected"] > 5
