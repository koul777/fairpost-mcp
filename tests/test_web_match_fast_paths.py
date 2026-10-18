"""The web engine's matching shortcuts give what the plain algorithm gives.

web/engine.js skips patterns whose required literals are missing (with a
code-unit pair filter in front of includes() for strings it checks often),
shares the text views between calls and cuts evidence windows by walking code
points. tests/js_match_fast_paths.cjs compares each shortcut with a reference
version written without it: every regex run on freshly built views, plain
String.prototype.includes, and the Array.from window cuts.
"""
from __future__ import annotations

import json

import pytest

from web_engine import ROOT, run_node

pytestmark = pytest.mark.requires_node


def test_match_shortcuts_equal_reference_versions() -> None:
    result = json.loads(run_node([str(ROOT / "tests/js_match_fast_paths.cjs")]))
    assert result["patterns"] > 100
    assert result["compared"] > 1000
    assert result["nonEmpty"] >= 2 * result["texts"]
    assert result["includesChecks"] > 1000
    assert result["windowChecks"] > 500
    assert result["headingChecks"] > 50
