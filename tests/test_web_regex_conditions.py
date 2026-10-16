"""Soundness and full-result parity for the raw-regex necessary-condition filter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import FairpostEngine, load_ruleset
from test_web_rule_sweep import samples
from web_engine import ROOT, run_node, web_check_batch

pytestmark = pytest.mark.requires_node


def _cases() -> dict[str, str]:
    return {
        "no-required-word": "모집공고\n" + " " * 99_990,
        "missing-middle-group": "채용 결과 안내\n통지 후 불합격 처리합니다.",
        "missing-final-group": "채용 결과 안내\n별도 통지 없이 처리합니다.",
        "missing-first-group": "모집공고\n별도 절차 없이 불합격 처리합니다.",
        "all-words-wrong-order": "모집공고\n불합격 없이 통지합니다.",
        "positive": "채용 결과 안내\n서류 미비 시 별도 통지 없이 불합격 처리합니다.",
        "positive-second-branch": "모집공고\n안내 하지 않으면 미합격 처리합니다.",
        "positive-nested-branch": "모집공고\n연락 없습니다 탈락 처리합니다.",
        "zero-width-required-words": "채용 결과 안내\n통\u200b지 없\u200c이 불\u200d합격 처리합니다.",
        "nfkc-fullwidth": "자격요건\n２０대 지원자 우대\n채용 결과 안내：\n별도　통\u200b지　없이　불합격 처리합니다.",
        "nfkc-circled-digit": "채용인원\n①명\n지원자격\n만 ⑱세 이상\n통지 없이 불합격 처리합니다.",
        "long-space-missing-word": "모집공고\n" + " " * 30_000 + "통지 불합격",
        "long-space-positive": "모집공고\n" + " " * 30_000 + "통지 없이 불합격 처리합니다.",
        "long-space-normalized-positive": "모집공고\n" + " " * 30_000 + "통\ufeff지 없이 불합격 처리합니다.",
        "protected-normalized-wording": "자격요건\n성\u200b별과 무관하게 지원할 수 있습니다.\n채용 결과 안내\n합격 여부는 모든 지원자에게 통지합니다.",
    }


def test_regex_prefilter_preserves_full_python_results(tmp_path: Path) -> None:
    cases = _cases()
    texts = list(cases.values())
    # The second pass exercises cached plans after accepted and rejected scans.
    web = web_check_batch(texts + texts, tmp_path)
    engine = FairpostEngine()
    for index, (label, text) in enumerate(cases.items()):
        expected = engine.check(text).to_dict()
        assert web[index] == expected, label
        assert web[index + len(texts)] == expected, f"{label} (cached plans)"
        if label == "positive":
            assert "Q-INFO-014" in {question["id"] for question in expected["questions"]}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def test_regex_conditions_are_necessary_on_rule_derived_samples(tmp_path: Path) -> None:
    ruleset = load_ruleset()
    patterns = sorted({
        text for text in _strings([ruleset.rules, ruleset.slots])
        if text.startswith("re:")
    })
    texts = sorted({text for pattern in patterns for text in samples(pattern)})
    assert patterns and texts
    # Keep the cross-product samples short. Long-space cases are checked above
    # and by the runner's execution counter without running unfiltered regexes.
    request = tmp_path / "regex-conditions.json"
    request.write_text(json.dumps({"patterns": patterns, "texts": texts}, ensure_ascii=False), encoding="utf-8")
    result = json.loads(run_node([str(ROOT / "tests/js_regex_conditions.cjs"), str(request)]))
    assert result["patterns"] >= len(patterns)
    assert result["comparisons"] == result["patterns"] * result["texts"]
    assert result["matchedPatterns"] > 0
    assert result["filteredPatterns"] > 0
    assert result["rejectedExecutions"] == 0
