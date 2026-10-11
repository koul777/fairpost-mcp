"""tools/normalize_candidates.py: merge spelling variants of model-proposed expressions.

The tool turns the JSONL that an LLM produced from training postings into one
human-review list. It must merge variants, count distinct postings, never mark
anything as decided, and refuse a candidate that carries a statute citation:
the legal basis is chosen by people, not proposed by a model. Synthetic data only.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unicodedata

import pytest

from tools import normalize_candidates as tool


def write_jsonl(path: Path, rows: list[object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def run(monkeypatch: pytest.MonkeyPatch, *args: object) -> int:
    monkeypatch.setattr(sys, "argv", ["normalize_candidates.py", *map(str, args)])
    return tool.main()


# --- normalized_expression -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("젊은  사람", "젊은"),  # whitespace collapses, the trailing 사람 is an ending
        ("  “젊은”   인재 ", "젊은"),  # quotes and padding are dropped first
        ("(여성) 우대", "여성 우대"),  # brackets become spaces
        ("Young   Applicants", "young applicants"),  # case folds
        ("활기찬 분", "활기찬"),
        ("건강한", "건강"),  # an ending glued to the stem is cut too
    ],
)
def test_expressions_that_differ_only_in_spelling_normalize_alike(raw: str, expected: str) -> None:
    assert tool.normalized_expression(raw) == expected


def test_decomposed_hangul_is_composed_before_comparison() -> None:
    composed = "젊은 사람"
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed
    assert tool.normalized_expression(decomposed) == tool.normalized_expression(composed)


def test_an_expression_that_is_only_an_ending_is_kept() -> None:
    assert tool.normalized_expression("사람") == "사람"
    assert tool.normalized_expression(" (분) ") == "분"


# --- iter_candidates -----------------------------------------------------------


def test_candidates_are_read_from_the_wrapped_list(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "in.jsonl",
        [{"candidates": [{"expression": "a"}, {"expression": "b"}]}, {"candidates": []}],
    )
    assert [c["expression"] for c in tool.iter_candidates(path)] == ["a", "b"]


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "in.jsonl"
    path.write_text('\n{"candidates": [{"expression": "a"}]}\n\n', encoding="utf-8")
    assert len(list(tool.iter_candidates(path))) == 1


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"candidates": [{"expression": "a", "article": "제4조"}]}, "법령 조항"),
        ({"candidates": [{"expression": "a", "statute_id": "equal-employment-act"}]}, "법령 조항"),
        ({"candidates": [{"category": "age"}]}, "expression이 필요"),
        ({"candidates": [{"expression": ""}]}, "expression이 필요"),
        ({"candidates": ["not an object"]}, "expression이 필요"),
        ({"candidates": {"expression": "a"}}, "candidates 목록이 필요"),
        ({"expression": "a"}, "candidates 목록이 필요"),
    ],
)
def test_a_malformed_candidate_is_rejected_with_its_line(
    tmp_path: Path, row: object, message: str
) -> None:
    path = write_jsonl(tmp_path / "in.jsonl", [{"candidates": [{"expression": "ok"}]}, row])
    with pytest.raises(ValueError, match=message) as error:
        list(tool.iter_candidates(path))
    assert f"{path}:2:" in str(error.value)


# --- main ----------------------------------------------------------------------


def two_inputs(tmp_path: Path) -> list[Path]:
    first = write_jsonl(
        tmp_path / "a.jsonl",
        [
            {
                "candidates": [
                    {"expression": "젊은 사람", "posting_id": "p1", "category": "age"},
                    {"expression": "“젊은” 인재", "posting_id": "p2", "category": "age"},
                    {"expression": "건강한", "posting_id": "p1", "category": "health"},
                ]
            }
        ],
    )
    second = write_jsonl(
        tmp_path / "b.jsonl",
        [
            {
                "candidates": [
                    {"expression": "젊은", "posting_id": "p2", "category": "other"},
                    {"expression": "젊은", "posting_id": "p3"},
                    {"expression": "여성 우대"},
                ]
            }
        ],
    )
    return [first, second]


def test_variants_merge_and_postings_are_counted_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out" / "nested" / "normalized.json"
    assert run(monkeypatch, *two_inputs(tmp_path), "--output", output) == 0

    result = json.loads(output.read_text(encoding="utf-8"))
    by_expression = {item["expression"]: item for item in result}
    young = by_expression["젊은"]
    assert young["variants"] == ["“젊은” 인재", "젊은", "젊은 사람"]
    assert young["posting_ids"] == ["p1", "p2", "p3"]
    assert young["corpus_hits"] == 3  # p2 appears twice but counts once
    assert young["categories"] == ["age", "other"]
    # Candidates without a posting id still appear, with no hits.
    assert by_expression["여성 우대"]["corpus_hits"] == 0
    assert by_expression["여성 우대"]["posting_ids"] == []
    assert by_expression["건강"]["corpus_hits"] == 1
    assert capsys.readouterr().out.strip() == "6개 후보를 3개로 병합"


def test_nothing_is_decided_and_the_most_supported_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "normalized.json"
    run(monkeypatch, *two_inputs(tmp_path), "--output", output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert {item["decision"] for item in result} == {"pending_human_review"}
    assert [item["corpus_hits"] for item in result] == sorted(
        (item["corpus_hits"] for item in result), reverse=True
    )
    assert [item["expression"] for item in result] == ["젊은", "건강", "여성 우대"]
    assert set(result[0]) == {
        "expression", "variants", "corpus_hits", "posting_ids", "categories", "decision",
    }


def test_output_is_deterministic_and_independent_of_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = two_inputs(tmp_path)
    run(monkeypatch, first, second, "--output", tmp_path / "x.json")
    run(monkeypatch, second, first, "--output", tmp_path / "y.json")
    assert (tmp_path / "x.json").read_bytes() == (tmp_path / "y.json").read_bytes()


@pytest.mark.parametrize("kind", ["missing", "not-json", "citation"])
def test_bad_input_stops_the_run_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    output = tmp_path / "normalized.json"
    if kind == "missing":
        source = tmp_path / "does-not-exist.jsonl"
    elif kind == "not-json":
        source = tmp_path / "in.jsonl"
        source.write_text("{not json}\n", encoding="utf-8")
    else:
        source = write_jsonl(
            tmp_path / "in.jsonl", [{"candidates": [{"expression": "a", "article": "제4조"}]}]
        )
    with pytest.raises(SystemExit) as stop:
        run(monkeypatch, source, "--output", output)
    assert stop.value.code not in (0, None)  # a message, which Python reports with status 1
    assert not output.exists()
