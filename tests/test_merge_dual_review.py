"""tools/merge_dual_review.py: 이중 검토 병합, 일치율, 조정 파일 왕복.

모든 입력은 tmp_path 안에서 만든 합성 큐 행이다. 실제 `.private-review/`나
`.corpus*` 폴더는 열지 않는다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from tools import build_private_review_queue as review_queue
from tools import merge_dual_review as dual
from tools import summarize_private_review as review_summary


ROOT = Path(__file__).resolve().parents[1]
RULES = review_summary._rule_metadata()
LABEL_CODES = {
    "T": "true_positive",
    "F": "false_positive",
    "U": "uncertain",
    "-": "unreviewed",
}
CONTEXT_PREFIX = "합성 문맥"


@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """테스트 이름과 무관한 폴더.

    pytest는 tmp_path 이름에 테스트 함수 이름을 넣는다. 경로 규칙을 시험하는
    함수 이름에 금지 분할 단어가 들어가면 모든 경로가 거부되므로 이름을 고정한다.
    """
    return tmp_path_factory.mktemp("dual")


def queue_row(number: int, rule_id: str, label: str) -> dict[str, Any]:
    layer, dimension = RULES[rule_id]
    return {
        "review_id": f"{number:064x}",
        "rule_id": rule_id,
        "layer": layer,
        "dimension": dimension,
        "context": f"{CONTEXT_PREFIX} {number}번",
        "matched_text": "일치 문자열",
        "section": None,
        "label": label,
        "allowed_labels": ["true_positive", "false_positive", "uncertain"],
    }


def pair_rows(
    rule_id: str, labels_a: str, labels_b: str, start: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """T/F/U/-로 쓴 라벨 문자열 두 개에서 A, B 행 목록을 만든다."""
    assert len(labels_a) == len(labels_b)
    rows_a, rows_b = [], []
    for offset, (code_a, code_b) in enumerate(zip(labels_a, labels_b)):
        number = start + offset
        rows_a.append(queue_row(number, rule_id, LABEL_CODES[code_a]))
        rows_b.append(queue_row(number, rule_id, LABEL_CODES[code_b]))
    return rows_a, rows_b


def write_jsonl(path: Path, rows: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_report(output_dir: Path) -> dict[str, Any]:
    text = (output_dir / dual.REPORT_NAME).read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    return json.loads(text)


class Case:
    """한 번의 비교에 쓰는 파일 경로 묶음."""

    def __init__(
        self,
        root: Path,
        rows_a: list[dict[str, Any]],
        rows_b: list[dict[str, Any]],
        *,
        name: str = "case",
    ) -> None:
        self.root = root / name
        self.a = write_jsonl(self.root / "reviewer-a" / "primary.jsonl", rows_a)
        self.b = write_jsonl(self.root / "reviewer-b" / "secondary.jsonl", rows_b)
        self.out = self.root / "out"

    def argv(self, *extra: str) -> list[str]:
        return [
            "--input-a",
            str(self.a),
            "--input-b",
            str(self.b),
            "--output-dir",
            str(self.out),
            *extra,
        ]

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return dual.merge_dual_review([self.a], [self.b], output_dir=self.out, **kwargs)


def two_rule_case(root: Path, *, name: str = "case") -> Case:
    """SEX-001 10행(불일치 1), AGE-002 10행(전부 일치)."""
    a1, b1 = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFT", 1)
    a2, b2 = pair_rows("AGE-002", "TTTTTFFFFF", "TTTTTFFFFF", 11)
    return Case(root, a1 + a2, b1 + b2, name=name)


# --- 일치율과 카파 -------------------------------------------------------------


def test_happy_path_reports_per_rule_and_overall_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = two_rule_case(tmp_path)

    exit_code = dual.main(case.argv())

    assert exit_code == 0
    report = read_report(case.out)
    assert report["schema_version"] == "dual-review-merge-report-v1"
    assert report["status"] == "ok"
    assert report["merge_status"] == "awaiting_adjudication"
    assert report["release_claim_eligible"] is False
    assert report["rows"] == {
        "union": 20,
        "compared": 20,
        "agreed": 19,
        "disagreed": 1,
        "incomplete": 0,
        "single_review": 0,
    }
    overall = report["overall"]
    assert overall["agreement"] == 0.95
    assert overall["kappa"] == 0.9
    assert overall["uncertain_share"] == {"a": 0.0, "b": 0.0}

    by_rule = {rule["rule_id"]: rule for rule in report["rules"]}
    assert [rule["rule_id"] for rule in report["rules"]] == ["AGE-002", "SEX-001"]
    sex = by_rule["SEX-001"]
    assert sex["rows"] == 10
    assert sex["agreed"] == 9
    assert sex["agreement"] == 0.9
    assert sex["expected_agreement"] == 0.5
    assert sex["kappa"] == 0.8
    assert sex["kappa_reason_code"] is None
    assert sex["status"] == "ok"
    assert sex["label_counts"]["a"] == {
        "true_positive": 5,
        "false_positive": 5,
        "uncertain": 0,
    }
    assert sex["label_counts"]["b"]["true_positive"] == 6
    assert by_rule["AGE-002"]["agreement"] == 1.0
    assert by_rule["AGE-002"]["kappa"] == 1.0

    assert report["disagreements"] == {
        "count": 1,
        "review_ids": [f"{10:064x}"],
        "by_rule": {"SEX-001": 1},
    }
    assert report["coverage_gap_rules"] == []
    assert report["alerts"] == {
        "agreement_below_threshold": [],
        "kappa_below_threshold": [],
        "uncertain_share_above_threshold": [],
    }
    assert report["thresholds"] == {
        "min_agreement_per_rule": 0.8,
        "min_kappa_overall": 0.6,
        "max_uncertain_share": 0.1,
        "min_rows_per_rule": 10,
    }
    assert report["queue_fingerprint"]["a"] == report["queue_fingerprint"]["b"]
    assert report["privacy_boundary"]["contains_posting_text"] is False

    # 조정 파일에는 불일치 행만 있고 조정자 필드는 비어 있다.
    adjudication = read_jsonl(case.out / dual.ADJUDICATION_NAME)
    assert len(adjudication) == 1
    row = adjudication[0]
    assert row["review_id"] == f"{10:064x}"
    assert row["label_a"] == "false_positive"
    assert row["label_b"] == "true_positive"
    assert [row[field] for field in dual.ADJUDICATOR_FIELDS] == ["", "", ""]
    assert set(row) == dual.ADJUDICATION_FIELDS
    # 조정 전에는 최종 라벨 파일이 없다.
    assert not (case.out / dual.MERGED_NAME).exists()
    assert not (case.out / dual.SOURCES_NAME).exists()

    # 표준 출력과 보고서에는 문맥과 매치 문자열이 없다.
    captured = capsys.readouterr()
    for text in (captured.out, json.dumps(report, ensure_ascii=False)):
        assert CONTEXT_PREFIX not in text
        assert "일치 문자열" not in text
    summary = json.loads(captured.out)
    assert summary["status"] == "ok"
    assert summary["compared_rows"] == 20
    assert summary["disagreement_count"] == 1
    assert summary["merge_status"] == "awaiting_adjudication"
    assert "조정 파일" in summary["message"]


def test_pair_statistics_known_values() -> None:
    labels = ["true_positive", "false_positive", "uncertain"]
    # 고전 예: 50행, 일치 40행, 양쪽 20/30 비율 -> 카파 0.6
    pairs = (
        [("true_positive", "true_positive")] * 20
        + [("true_positive", "false_positive")] * 5
        + [("false_positive", "true_positive")] * 5
        + [("false_positive", "false_positive")] * 20
    )
    stats = dual.pair_statistics(pairs)
    assert stats["rows"] == 50
    assert stats["agreement"] == 0.8
    assert stats["expected_agreement"] == 0.5
    assert stats["kappa"] == 0.6

    # 세 범주와 음수 카파
    stats = dual.pair_statistics(
        [
            (labels[0], labels[1]),
            (labels[1], labels[2]),
            (labels[2], labels[0]),
        ]
    )
    assert stats["agreement"] == 0.0
    assert stats["expected_agreement"] == 0.333333
    assert stats["kappa"] == -0.5

    # uncertain도 하나의 범주다: 둘 다 uncertain이면 일치
    stats = dual.pair_statistics(
        [("uncertain", "uncertain"), ("true_positive", "true_positive"),
         ("false_positive", "true_positive"), ("false_positive", "false_positive")]
    )
    assert stats["agreed"] == 3
    assert stats["uncertain_share"] == {"a": 0.25, "b": 0.25}


def test_perfect_agreement_with_one_label_gives_null_kappa_not_nan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    case = Case(tmp_path, rows_a, rows_b)

    exit_code = dual.main(case.argv())

    assert exit_code == 0
    report = read_report(case.out)
    for scope in (report["overall"], report["rules"][0]):
        assert scope["agreement"] == 1.0
        assert scope["expected_agreement"] == 1.0
        assert scope["kappa"] is None
        assert scope["kappa_reason_code"] == "expected_agreement_is_one"
        assert "카파" in scope["kappa_reason"]
    assert report["status"] == "ok"
    assert report["alerts"]["kappa_below_threshold"] == []
    assert report["disagreements"]["count"] == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["kappa"] is None
    assert summary["kappa_reason_code"] == "expected_agreement_is_one"

    # 불일치가 없으면 조정 파일은 비어 있고 최종 라벨 파일이 바로 나온다.
    assert report["merge_status"] == "complete"
    assert (case.out / dual.ADJUDICATION_NAME).read_text(encoding="utf-8") == ""
    merged = read_jsonl(case.out / dual.MERGED_NAME)
    assert [row["label"] for row in merged] == ["true_positive"] * 10
    sources = json.loads((case.out / dual.SOURCES_NAME).read_text(encoding="utf-8"))
    assert sources["source_counts"]["agreed"] == 10


def test_perfect_agreement_with_mixed_labels_gives_kappa_one(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFF", 1)
    report = Case(tmp_path, rows_a, rows_b).run()

    assert report["overall"]["agreement"] == 1.0
    assert report["overall"]["kappa"] == 1.0
    assert report["overall"]["kappa_reason_code"] is None
    assert report["status"] == "ok"


def test_all_uncertain_on_both_sides_has_null_kappa_but_breaches_uncertain_share(
    tmp_path: Path,
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "U" * 10, "U" * 10, 1)
    case = Case(tmp_path, rows_a, rows_b)

    assert dual.main(case.argv()) == 2

    report = read_report(case.out)
    assert report["overall"]["kappa"] is None
    assert report["status"] == "alert"
    assert {item["reviewer"] for item in report["alerts"]["uncertain_share_above_threshold"]} == {"a", "b"}


# --- 기준 미달과 coverage_gap ----------------------------------------------------


def test_low_agreement_alerts_with_exit_code_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTFFFFFTT", 1)
    case = Case(tmp_path, rows_a, rows_b)

    exit_code = dual.main(case.argv())

    assert exit_code == 2
    report = read_report(case.out)
    assert report["status"] == "alert"
    assert report["overall"]["agreement"] == 0.6
    assert report["overall"]["kappa"] == 0.2
    agreement_alert = report["alerts"]["agreement_below_threshold"]
    assert [(item["rule_id"], item["agreement"]) for item in agreement_alert] == [
        ("SEX-001", 0.6)
    ]
    assert agreement_alert[0]["minimum"] == 0.8
    assert "일치율" in agreement_alert[0]["message"]
    kappa_alert = report["alerts"]["kappa_below_threshold"]
    assert kappa_alert[0]["kappa"] == 0.2 and kappa_alert[0]["scope"] == "overall"
    assert report["rules"][0]["status"] == "alert"
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "alert"
    assert summary["alert_kinds"] == ["agreement_below_threshold", "kappa_below_threshold"]
    # 경보여도 보고서와 조정 파일은 쓴다.
    assert len(read_jsonl(case.out / dual.ADJUDICATION_NAME)) == 4


def test_thresholds_can_be_overridden_from_the_command_line(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTFFFFFTT", 1)
    case = Case(tmp_path, rows_a, rows_b)

    assert dual.main(case.argv("--min-agreement", "0.5", "--min-kappa", "0.1")) == 0

    report = read_report(case.out)
    assert report["status"] == "ok"
    assert report["thresholds"]["min_agreement_per_rule"] == 0.5
    assert report["thresholds"]["min_kappa_overall"] == 0.1
    # 기준을 높이면 다시 경보가 된다.
    assert dual.main(case.argv("--min-agreement", "0.7")) == 2


def test_agreement_exactly_at_the_threshold_is_not_an_alert(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFTT", 1)
    case = Case(tmp_path, rows_a, rows_b)

    report = case.run(min_kappa=0.0)

    assert report["overall"]["agreement"] == 0.8
    assert report["alerts"]["agreement_below_threshold"] == []
    assert report["status"] == "ok"


def test_uncertain_share_above_the_limit_is_an_alert(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFUU", "TTTTTFFFUU", 1)
    case = Case(tmp_path, rows_a, rows_b)

    assert dual.main(case.argv()) == 2

    report = read_report(case.out)
    alerts = report["alerts"]["uncertain_share_above_threshold"]
    assert len(alerts) == 4  # 규칙 단위 2개, 전체 2개
    assert {(item["scope"], item["reviewer"]) for item in alerts} == {
        ("rule", "a"),
        ("rule", "b"),
        ("overall", "a"),
        ("overall", "b"),
    }
    assert all(item["share"] == 0.2 and item["maximum"] == 0.1 for item in alerts)
    assert report["alerts"]["agreement_below_threshold"] == []
    # 상한을 올리면 통과한다.
    assert dual.main(case.argv("--max-uncertain-share", "0.2")) == 0


def test_uncertain_share_equal_to_the_limit_is_not_an_alert(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFU", "TTTTTFFFFU", 1)

    report = Case(tmp_path, rows_a, rows_b).run()

    assert report["overall"]["uncertain_share"] == {"a": 0.1, "b": 0.1}
    assert report["status"] == "ok"


def test_rules_with_too_few_rows_are_coverage_gap_not_failures(tmp_path: Path) -> None:
    a1, b1 = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFF", 1)
    a2, b2 = pair_rows("Q-DIST-014", "TTFF", "TFFT", 11)  # 4행, 일치율 0.5
    case = Case(tmp_path, a1 + a2, b1 + b2)

    exit_code = dual.main(case.argv())

    assert exit_code == 0
    report = read_report(case.out)
    assert report["status"] == "ok"
    assert report["coverage_gap_rules"] == ["Q-DIST-014"]
    gap = next(rule for rule in report["rules"] if rule["rule_id"] == "Q-DIST-014")
    assert gap["status"] == "coverage_gap"
    assert gap["rows"] == 4
    assert gap["agreement"] == 0.5  # 숫자는 보고하되 기준에서는 뺀다.
    assert gap["kappa"] == 0.0
    assert report["alerts"]["agreement_below_threshold"] == []
    assert report["overall"]["rows"] == 14
    assert report["overall"]["kappa"] == 0.714286

    # 최소 행 수를 낮추면 같은 규칙이 기준 평가를 받는다.
    assert dual.main(case.argv("--min-rows-per-rule", "4")) == 2
    assert read_report(case.out)["coverage_gap_rules"] == []


def test_rule_without_compared_rows_is_listed_as_coverage_gap(tmp_path: Path) -> None:
    a1, b1 = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFF", 1)
    a2, b2 = pair_rows("AGE-002", "TT", "TT", 11)
    # AGE-002 는 B 파일에 없고(부분집합 허용) 비교 행이 0이다.
    case = Case(tmp_path, a1 + a2, b1)

    report = case.run(allow_subset=True)

    age = next(rule for rule in report["rules"] if rule["rule_id"] == "AGE-002")
    assert age["rows"] == 0
    assert age["agreement"] is None
    assert age["kappa"] is None and age["kappa_reason_code"] == "no_rows"
    assert age["status"] == "coverage_gap"
    assert "AGE-002" in report["coverage_gap_rules"]


# --- 입력 오류 ---------------------------------------------------------------


def test_mismatched_row_sets_fail_in_korean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    rows_b[-1] = queue_row(99, "SEX-001", "true_positive")
    case = Case(tmp_path, rows_a, rows_b)

    exit_code = dual.main(case.argv())

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("오류:")
    assert "행 집합" in captured.err
    assert "A에만 1행, B에만 1행" in captured.err
    assert f"{99:064x}" not in captured.err
    assert not case.out.exists()


def test_subset_is_rejected_by_default_and_compared_with_allow_subset(
    tmp_path: Path,
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFF", 1)
    rows_b = rows_b[:6]
    case = Case(tmp_path, rows_a, rows_b)

    with pytest.raises(dual.DualReviewError, match="행 집합"):
        case.run()

    report = case.run(allow_subset=True)

    assert report["rows"]["compared"] == 6
    assert report["rows"]["single_review"] == 4
    assert report["rows"]["union"] == 10
    assert report["rows"]["disagreed"] == 0
    assert report["queue_fingerprint"]["a"] != report["queue_fingerprint"]["b"]
    merged = read_jsonl(case.out / dual.MERGED_NAME)
    assert len(merged) == 10
    sources = json.loads((case.out / dual.SOURCES_NAME).read_text(encoding="utf-8"))
    assert sources["source_counts"] == {
        "agreed": 6,
        "adjudicated": 0,
        "single_review": 4,
        "incomplete": 0,
    }
    # 겹치지 않는 행이 양쪽에 모두 있으면 부분집합이 아니다.
    rows_b[0] = queue_row(77, "SEX-001", "true_positive")
    other = Case(tmp_path, rows_a, rows_b, name="other")
    with pytest.raises(dual.DualReviewError, match="포함되지 않습니다"):
        other.run(allow_subset=True)


def test_same_ids_with_different_queue_content_fail_the_fingerprint(
    tmp_path: Path,
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    rows_b[3]["context"] = "다른 합성 문맥"
    case = Case(tmp_path, rows_a, rows_b)

    with pytest.raises(dual.DualReviewError, match="큐 지문"):
        case.run()


def test_invalid_label_value_fails_without_echoing_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    rows_b[4]["label"] = "BOGUS_LABEL_VALUE"
    case = Case(tmp_path, rows_a, rows_b)

    assert dual.main(case.argv()) == 1

    err = capsys.readouterr().err
    assert "검토 파일 B 5행" in err
    assert "label" in err
    assert "BOGUS_LABEL_VALUE" not in err
    assert CONTEXT_PREFIX not in err


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(label=None),
        lambda row: row.update(label="TRUE_POSITIVE"),
        lambda row: row.update(allowed_labels=["true_positive"]),
        lambda row: row.pop("context"),
        lambda row: row.update(rule_id="NO-SUCH-RULE"),
        lambda row: row.update(review_id="not-a-hash"),
    ],
)
def test_malformed_rows_fail_in_korean(tmp_path: Path, mutation: Any) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    mutation(rows_a[0])
    case = Case(tmp_path, rows_a, rows_b)

    with pytest.raises(dual.DualReviewError, match="검토 파일 A 1행"):
        case.run()


def test_duplicate_review_ids_and_json_fields_fail(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    rows_a.append(dict(rows_a[0]))
    case = Case(tmp_path, rows_a, rows_b)
    with pytest.raises(dual.DualReviewError, match="검토 파일 A 11행: review_id가 중복"):
        case.run()

    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    other = Case(tmp_path, rows_a, rows_b, name="fields")
    lines = other.b.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0][:-1] + ', "label": "true_positive"}'
    other.b.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(dual.DualReviewError, match="검토 파일 B 1행: JSON 필드가 중복"):
        other.run()


def test_malformed_json_empty_file_and_missing_file_fail(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    case = Case(tmp_path, rows_a, rows_b)
    case.b.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(dual.DualReviewError, match="JSON 형식"):
        case.run()
    case.b.write_text("", encoding="utf-8")
    with pytest.raises(dual.DualReviewError, match="행이 없습니다"):
        case.run()
    case.b.unlink()
    with pytest.raises(dual.DualReviewError, match="읽을 수 없습니다"):
        case.run()


def test_unlabeled_rows_fail_unless_partial_files_are_allowed(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTT---FF", 1)
    case = Case(tmp_path, rows_a, rows_b)

    with pytest.raises(dual.DualReviewError, match="미검토 행이 있습니다 \\(A 0행, B 3행\\)"):
        case.run()

    report = case.run(allow_partial=True)

    assert report["rows"]["compared"] == 7
    assert report["rows"]["incomplete"] == 3
    assert report["files"]["b"]["unreviewed"] == 3
    assert report["overall"]["rows"] == 7
    assert report["options"]["allow_partial"] is True
    # 미검토 행은 불일치로 세지 않고 조정 대상에도 넣지 않는다.
    assert report["disagreements"]["count"] == 0
    merged = {row["review_id"]: row["label"] for row in read_jsonl(case.out / dual.MERGED_NAME)}
    assert merged[f"{6:064x}"] == "unreviewed"
    sources = json.loads((case.out / dual.SOURCES_NAME).read_text(encoding="utf-8"))
    assert sources["source_counts"]["incomplete"] == 3


def test_all_rows_incomplete_has_nothing_to_compare(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "-" * 10, 1)

    with pytest.raises(dual.DualReviewError, match="비교 행이 없습니다"):
        Case(tmp_path, rows_a, rows_b).run(allow_partial=True)


@pytest.mark.parametrize(
    "arguments",
    [
        ("--min-agreement", "1.5"),
        ("--min-agreement", "nan"),
        ("--min-kappa", "-2"),
        ("--max-uncertain-share", "-0.1"),
        ("--min-rows-per-rule", "-1"),
    ],
)
def test_invalid_thresholds_exit_one(
    tmp_path: Path, arguments: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    case = two_rule_case(tmp_path)

    assert dual.main(case.argv(*arguments)) == 1

    assert capsys.readouterr().err.startswith("오류:")
    assert not case.out.exists()


def test_usage_errors_exit_one_not_two(capsys: pytest.CaptureFixture[str]) -> None:
    # argparse 기본 종료 코드 2는 이 도구에서 경보를 뜻한다.
    with pytest.raises(SystemExit) as caught:
        dual.main(["--input-a", "a.jsonl"])
    assert caught.value.code == 1
    assert "오류:" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:
        dual.main(["--input-a", "a.jsonl", "--input-b", "b.jsonl", "--min-kappa", "x"])
    assert caught.value.code == 1


# --- 조정 파일 왕복 -----------------------------------------------------------------


def fill_adjudication(
    path: Path,
    *,
    label: str = "true_positive",
    adjudicator: str = "adjudicator-1",
    reason: str = "criteria_clarified",
) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    for row in rows:
        row["adjudicated_label"] = label
        row["adjudicator_id"] = adjudicator
        row["reason_code"] = reason
    write_jsonl(path, rows)
    return rows


def disagreeing_case(tmp_path: Path, *, name: str = "case") -> Case:
    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTFFFFFFT", 1)
    a2, b2 = pair_rows("Q-DIST-014", "TTTTTTTTTT", "TTTTTTTTTT", 11)
    return Case(tmp_path, rows_a + a2, rows_b + b2, name=name)


def test_adjudication_round_trip_produces_merged_labels_with_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = disagreeing_case(tmp_path)
    assert dual.main(case.argv("--min-kappa", "0", "--min-agreement", "0")) == 0
    template_path = case.out / dual.ADJUDICATION_NAME
    template = read_jsonl(template_path)
    assert [row["review_id"] for row in template] == [f"{n:064x}" for n in (4, 5, 10)]
    # 4번: A=T B=F, 5번: A=T B=F, 10번: A=F B=T
    rows = read_jsonl(template_path)
    rows[0].update(adjudicated_label="false_positive", adjudicator_id="adjudicator-1", reason_code="exception_clause")
    rows[1].update(adjudicated_label="true_positive", adjudicator_id="adjudicator-1", reason_code="criteria_clarified")
    rows[2].update(adjudicated_label="uncertain", adjudicator_id="adjudicator-1", reason_code="context_too_short")
    write_jsonl(template_path, rows)
    capsys.readouterr()

    exit_code = dual.main(
        case.argv(
            "--adjudication",
            str(template_path),
            "--reviewer-id",
            "reviewer-A",
            "--reviewer-id",
            "reviewer-B",
            "--min-kappa",
            "0",
            "--min-agreement",
            "0",
        )
    )

    assert exit_code == 0
    report = read_report(case.out)
    assert report["merge_status"] == "complete"
    assert report["adjudication"] == {
        "required_rows": 3,
        "provided": True,
        "resolved_rows": 3,
        "adjudicator_ids": ["adjudicator-1"],
        "final_label_counts": {
            "true_positive": 1,
            "false_positive": 1,
            "uncertain": 1,
        },
    }
    merged_path = case.out / dual.MERGED_NAME
    merged = read_jsonl(merged_path)
    assert len(merged) == 20
    labels = {row["review_id"]: row["label"] for row in merged}
    assert labels[f"{1:064x}"] == "true_positive"  # 합의
    assert labels[f"{4:064x}"] == "false_positive"  # 조정
    assert labels[f"{5:064x}"] == "true_positive"
    assert labels[f"{10:064x}"] == "uncertain"
    # 합의 행의 라벨은 두 검토자가 같았던 값이다.
    assert labels[f"{8:064x}"] == "false_positive"

    sources = json.loads((case.out / dual.SOURCES_NAME).read_text(encoding="utf-8"))
    assert sources["schema_version"] == "dual-review-merge-sources-v1"
    assert sources["row_count"] == 20
    assert sources["source_counts"] == {
        "agreed": 17,
        "adjudicated": 3,
        "single_review": 0,
        "incomplete": 0,
    }
    by_id = {row["review_id"]: row for row in sources["rows"]}
    assert by_id[f"{4:064x}"] == {
        "review_id": f"{4:064x}",
        "rule_id": "SEX-001",
        "source": "adjudicated",
        "label_a": "true_positive",
        "label_b": "false_positive",
        "final_label": "false_positive",
        "adjudicator_id": "adjudicator-1",
        "reason_code": "exception_clause",
    }
    assert by_id[f"{1:064x}"]["source"] == "agreed"
    assert by_id[f"{1:064x}"]["adjudicator_id"] is None
    assert CONTEXT_PREFIX not in json.dumps(sources, ensure_ascii=False)
    assert report["merged_labels_sha256"] == sources["merged_labels_sha256"]

    # 최종 라벨 파일은 큐 형식을 그대로 지켜서 집계 도구가 바로 읽는다.
    assert {frozenset(row) for row in merged} == {frozenset(review_summary.INPUT_FIELDS)}
    original = read_jsonl(case.a)
    assert review_queue.immutable_rows_sha256(merged) == review_queue.immutable_rows_sha256(original)
    summary = review_summary.summarize_private_review(merged_path, tmp_path / "summary.json")
    assert summary["aggregate"]["unreviewed"] == 0
    assert summary["aggregate"]["uncertain"] == 1
    assert capsys.readouterr().out.count("\n") == 1


def test_adjudicated_run_writes_the_same_bytes_every_time(tmp_path: Path) -> None:
    outputs = []
    for name in ("first", "second"):
        case = disagreeing_case(tmp_path, name=name)
        case.run(min_kappa=0, min_agreement=0)
        fill_adjudication(case.out / dual.ADJUDICATION_NAME)
        case.run(
            adjudication_path=case.out / dual.ADJUDICATION_NAME,
            min_kappa=0,
            min_agreement=0,
        )
        outputs.append(
            {
                path.name: path.read_bytes()
                for path in sorted(case.out.iterdir())
            }
        )
    assert set(outputs[0]) == {
        dual.REPORT_NAME,
        dual.ADJUDICATION_NAME,
        dual.MERGED_NAME,
        dual.SOURCES_NAME,
    }
    assert outputs[0] == outputs[1]


def test_output_does_not_depend_on_row_order(tmp_path: Path) -> None:
    case = disagreeing_case(tmp_path, name="ordered")
    shuffled = disagreeing_case(tmp_path, name="shuffled")
    for path in (shuffled.a, shuffled.b):
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    case.run(min_kappa=0, min_agreement=0)
    shuffled.run(min_kappa=0, min_agreement=0)

    for name in (dual.REPORT_NAME, dual.ADJUDICATION_NAME):
        assert (case.out / name).read_bytes() == (shuffled.out / name).read_bytes()


def test_adjudication_must_be_complete_and_valid(tmp_path: Path) -> None:
    case = disagreeing_case(tmp_path)
    case.run(min_kappa=0, min_agreement=0)
    path = case.out / dual.ADJUDICATION_NAME
    pristine = path.read_text(encoding="utf-8")

    def run_final(**kwargs: Any) -> dict[str, Any]:
        return case.run(
            adjudication_path=path, min_kappa=0, min_agreement=0, **kwargs
        )

    # 비어 있음
    with pytest.raises(dual.DualReviewError, match="아직 조정되지 않은 행이 3개"):
        run_final()

    # 일부만 채움
    rows = read_jsonl(path)
    rows[0]["adjudicated_label"] = "true_positive"
    write_jsonl(path, rows)
    with pytest.raises(dual.DualReviewError, match="일부만 채운 행이 1개"):
        run_final()

    # 잘못된 값들
    for field, value in (
        ("adjudicated_label", "unreviewed"),
        ("adjudicated_label", "maybe"),
        ("adjudicator_id", "has space"),
        ("reason_code", "사유 설명 문장"),
    ):
        path.write_text(pristine, encoding="utf-8")
        fill_adjudication(path)
        rows = read_jsonl(path)
        rows[1][field] = value
        write_jsonl(path, rows)
        with pytest.raises(dual.DualReviewError, match="올바르지 않은 행이 1개"):
            run_final()

    # 조정자가 검토자와 같음
    path.write_text(pristine, encoding="utf-8")
    fill_adjudication(path, adjudicator="reviewer-A")
    with pytest.raises(dual.DualReviewError, match="올바르지 않은 행이 3개"):
        run_final(reviewer_ids=["reviewer-A"])
    run_final()  # 검토자 ID를 알려 주지 않으면 구분할 수 없으므로 통과한다.

    # 행이 빠짐, 행이 늘어남, 중복
    path.write_text(pristine, encoding="utf-8")
    fill_adjudication(path)
    rows = read_jsonl(path)
    write_jsonl(path, rows[:-1])
    with pytest.raises(dual.DualReviewError, match="없는 불일치 행이 1개"):
        run_final()
    extra = dict(rows[0], review_id=f"{15:064x}")
    write_jsonl(path, rows + [extra])
    with pytest.raises(dual.DualReviewError, match="불일치가 아닌 행이 1개"):
        run_final()
    write_jsonl(path, rows + [rows[0]])
    with pytest.raises(dual.DualReviewError, match="review_id가 중복"):
        run_final()

    # 스키마가 다름
    broken = [dict(row) for row in rows]
    broken[0]["note"] = "extra"
    write_jsonl(path, broken)
    with pytest.raises(dual.DualReviewError, match="스키마"):
        run_final()

    # 모든 조정이 끝난 파일은 통과
    write_jsonl(path, rows)
    assert run_final()["merge_status"] == "complete"


def test_stale_adjudication_is_rejected_after_a_label_changes(tmp_path: Path) -> None:
    case = disagreeing_case(tmp_path)
    case.run(min_kappa=0, min_agreement=0)
    path = case.out / dual.ADJUDICATION_NAME
    fill_adjudication(path)
    # 검토자 B가 4번 라벨을 고쳐서 A와 같아졌다.
    rows_b = read_jsonl(case.b)
    rows_b[3]["label"] = "true_positive"
    write_jsonl(case.b, rows_b)

    with pytest.raises(dual.DualReviewError, match="불일치가 아닌 행이 1개"):
        case.run(adjudication_path=path, min_kappa=0, min_agreement=0)

    # 라벨이 다른 값으로 또 바뀌면 내용 불일치로 걸린다.
    rows_b[3]["label"] = "uncertain"
    rows_b[4]["label"] = "uncertain"
    write_jsonl(case.b, rows_b)
    with pytest.raises(dual.DualReviewError, match="라벨 또는 내용과 다릅니다"):
        case.run(adjudication_path=path, min_kappa=0, min_agreement=0)


def test_rerun_without_adjudication_never_overwrites_recorded_decisions(
    tmp_path: Path,
) -> None:
    case = disagreeing_case(tmp_path)
    case.run(min_kappa=0, min_agreement=0)
    path = case.out / dual.ADJUDICATION_NAME

    # 비어 있는 템플릿은 다시 만들 수 있다.
    case.run(min_kappa=0, min_agreement=0)

    fill_adjudication(path)
    recorded = path.read_bytes()
    with pytest.raises(dual.DualReviewError, match="덮어쓰지 않습니다"):
        case.run(min_kappa=0, min_agreement=0)
    assert path.read_bytes() == recorded
    assert not (case.out / dual.MERGED_NAME).exists()

    # 같은 폴더에서 --adjudication으로 실행해도 조정 파일은 그대로다.
    case.run(adjudication_path=path, min_kappa=0, min_agreement=0)
    assert path.read_bytes() == recorded
    assert (case.out / dual.MERGED_NAME).exists()


def test_stale_final_files_are_removed_when_adjudication_is_needed_again(
    tmp_path: Path,
) -> None:
    case = disagreeing_case(tmp_path)
    case.run(min_kappa=0, min_agreement=0)
    path = case.out / dual.ADJUDICATION_NAME
    fill_adjudication(path)
    case.run(adjudication_path=path, min_kappa=0, min_agreement=0)
    assert (case.out / dual.MERGED_NAME).exists()

    # 조정 기록을 치우고 같은 폴더에서 조정 없이 다시 실행하면 이전 최종 파일이 남지 않는다.
    path.unlink()
    case.run(min_kappa=0, min_agreement=0)

    assert not (case.out / dual.MERGED_NAME).exists()
    assert not (case.out / dual.SOURCES_NAME).exists()
    assert read_report(case.out)["merge_status"] == "awaiting_adjudication"


def test_multiple_files_per_side_are_combined(tmp_path: Path) -> None:
    a1, b1 = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFF", 1)
    a2, b2 = pair_rows("AGE-002", "TTTTTFFFFF", "TTTTTFFFFT", 11)
    root = tmp_path / "multi"
    first_a = write_jsonl(root / "a-1.jsonl", a1)
    second_a = write_jsonl(root / "a-2.jsonl", a2)
    first_b = write_jsonl(root / "b-1.jsonl", b1)
    second_b = write_jsonl(root / "b-2.jsonl", b2)
    out = root / "out"

    report = dual.merge_dual_review([first_a, second_a], [first_b, second_b], output_dir=out)

    assert report["rows"]["compared"] == 20
    assert report["disagreements"]["by_rule"] == {"AGE-002": 1}

    overlapping = write_jsonl(root / "a-3.jsonl", a1[:2])
    with pytest.raises(dual.DualReviewError, match="검토 파일 A\\(3번째\\) 1행: review_id가 중복"):
        dual.merge_dual_review(
            [first_a, second_a, overlapping], [first_b, second_b], output_dir=out
        )


def test_utf8_bom_in_a_reviewer_file_is_accepted(tmp_path: Path) -> None:
    case = two_rule_case(tmp_path)
    text = case.b.read_text(encoding="utf-8")
    case.b.write_text(text, encoding="utf-8-sig")

    assert case.run()["rows"]["compared"] == 20


def test_kickoff_layout_secondary_subset_merges_into_a_full_label_file(
    tmp_path: Path,
) -> None:
    """1차 두 파일(전체 큐)과 2차 두 파일(일부)을 한 번에 병합하는 착수 계획 배치."""
    a1, _ = pair_rows("SEX-001", "TTTTTFFFFF", "TTTTTFFFFF", 1)
    a2, _ = pair_rows("Q-DIST-010", "FFFFFTTTTT", "FFFFFTTTTT", 11)
    # 2차: SEX-001 앞 4행(1건 불일치), Q-DIST-010 앞 3행(전부 일치)
    secondary_1 = [
        queue_row(1, "SEX-001", "true_positive"),
        queue_row(2, "SEX-001", "true_positive"),
        queue_row(3, "SEX-001", "false_positive"),
        queue_row(4, "SEX-001", "true_positive"),
    ]
    secondary_2 = [queue_row(n, "Q-DIST-010", "false_positive") for n in (11, 12, 13)]
    root = tmp_path / "kickoff"
    primary_files = [write_jsonl(root / "p-a.jsonl", a1), write_jsonl(root / "p-b.jsonl", a2)]
    secondary_files = [
        write_jsonl(root / "s-b.jsonl", secondary_1),
        write_jsonl(root / "s-a.jsonl", secondary_2),
    ]
    out = root / "out"

    report = dual.merge_dual_review(
        primary_files, secondary_files, output_dir=out, allow_subset=True
    )

    assert report["rows"] == {
        "union": 20,
        "compared": 7,
        "agreed": 6,
        "disagreed": 1,
        "incomplete": 0,
        "single_review": 13,
    }
    # 규칙당 비교 행이 10행보다 적어 두 규칙 모두 coverage_gap이다.
    assert report["coverage_gap_rules"] == ["Q-DIST-010", "SEX-001"]
    assert report["disagreements"]["review_ids"] == [f"{3:064x}"]

    path = out / dual.ADJUDICATION_NAME
    fill_adjudication(path, label="false_positive")
    final = dual.merge_dual_review(
        primary_files,
        secondary_files,
        output_dir=out,
        allow_subset=True,
        adjudication_path=path,
    )
    assert final["merge_status"] == "complete"
    merged = read_jsonl(out / dual.MERGED_NAME)
    assert len(merged) == 20
    # 큐 전체와 같은 라벨 외 필드이므로 --manifest 검증 대상이 될 수 있다.
    assert review_queue.immutable_rows_sha256(merged) == review_queue.immutable_rows_sha256(a1 + a2)
    sources = json.loads((out / dual.SOURCES_NAME).read_text(encoding="utf-8"))
    assert sources["source_counts"] == {
        "agreed": 6,
        "adjudicated": 1,
        "single_review": 13,
        "incomplete": 0,
    }


# --- 경로 규칙 -----------------------------------------------------------------------


def test_default_output_directory_is_the_private_review_folder() -> None:
    assert dual.DEFAULT_OUTPUT_DIR == Path(".private-review")
    args = dual._parse_args(["--input-a", "a.jsonl", "--input-b", "b.jsonl"])
    assert args.output_dir == Path(".private-review")
    assert {path.parent for path in dual.output_paths(dual.DEFAULT_OUTPUT_DIR).values()} == {
        Path(".private-review")
    }


@pytest.mark.parametrize(
    "parts",
    [
        ("sealed-holdout",),
        ("Holdout",),
        ("private-holdout", "train"),
        ("tests",),
        ("eval",),
        ("dev",),
    ],
)
def test_forbidden_partition_paths_are_refused_for_inputs_and_outputs(
    tmp_path: Path, parts: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    clean = two_rule_case(tmp_path, name="clean")
    forbidden_root = tmp_path.joinpath(*parts)

    # 입력 A가 금지 경로
    moved_a = write_jsonl(forbidden_root / "a.jsonl", read_jsonl(clean.a))
    args = ["--input-a", str(moved_a), "--input-b", str(clean.b), "--output-dir", str(clean.out)]
    assert dual.main(args) == 1
    assert "--input-a" in capsys.readouterr().err
    assert not clean.out.exists()

    # 입력 B가 금지 경로
    moved_b = write_jsonl(forbidden_root / "b.jsonl", read_jsonl(clean.b))
    args = ["--input-a", str(clean.a), "--input-b", str(moved_b), "--output-dir", str(clean.out)]
    assert dual.main(args) == 1
    assert "--input-b" in capsys.readouterr().err

    # 출력 폴더가 금지 경로
    args = clean.argv()
    args[args.index("--output-dir") + 1] = str(forbidden_root / "out")
    assert dual.main(args) == 1
    err = capsys.readouterr().err
    assert "--output-dir" in err
    assert not (forbidden_root / "out").exists()

    # 조정 파일이 금지 경로
    adjudication = write_jsonl(forbidden_root / "adjudication.jsonl", [])
    assert dual.main(clean.argv("--adjudication", str(adjudication))) == 1
    assert "--adjudication" in capsys.readouterr().err


def test_partition_names_in_a_file_name_are_refused(tmp_path: Path) -> None:
    case = two_rule_case(tmp_path)
    renamed = case.a.with_name("private-holdout-review.jsonl")
    case.a.rename(renamed)

    with pytest.raises(dual.DualReviewError, match="홀드아웃"):
        dual.merge_dual_review([renamed], [case.b], output_dir=case.out)


def test_there_is_no_flag_that_unlocks_a_sealed_partition() -> None:
    # 기존 도구와 같이 봉인 분할 경로는 예외 없이 거부한다.
    with pytest.raises(SystemExit) as caught:
        dual.main(["--input-a", "a", "--input-b", "b", "--allow-holdout"])
    assert caught.value.code == 1


def test_a_and_b_must_be_different_files_and_outputs_must_not_alias_inputs(
    tmp_path: Path,
) -> None:
    case = two_rule_case(tmp_path)

    with pytest.raises(dual.DualReviewError, match="서로 다른 파일"):
        dual.merge_dual_review([case.a], [case.a], output_dir=case.out)

    case.out.mkdir(parents=True)
    in_output_dir = case.out / dual.REPORT_NAME
    in_output_dir.write_bytes(case.b.read_bytes())
    with pytest.raises(dual.DualReviewError, match="서로 다른 파일"):
        dual.merge_dual_review([case.a], [in_output_dir], output_dir=case.out)

    # --adjudication 은 어떤 출력 파일과도 같으면 안 된다.
    adjudication = case.out / dual.MERGED_NAME
    write_jsonl(adjudication, [])
    with pytest.raises(dual.DualReviewError, match="서로 다른 파일"):
        dual.merge_dual_review(
            [case.a], [case.b], output_dir=case.out, adjudication_path=adjudication
        )
    # 입력 파일은 그대로다.
    assert len(read_jsonl(case.a)) == 20


def test_output_dir_must_be_a_folder(tmp_path: Path) -> None:
    case = two_rule_case(tmp_path)
    case.out.parent.mkdir(parents=True, exist_ok=True)
    case.out.write_text("not a folder", encoding="utf-8")

    with pytest.raises(dual.DualReviewError, match="폴더 경로"):
        case.run()


def test_nothing_is_written_when_validation_fails(tmp_path: Path) -> None:
    rows_a, rows_b = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    rows_b[0]["label"] = "BOGUS"
    case = Case(tmp_path, rows_a, rows_b)

    with pytest.raises(dual.DualReviewError):
        case.run()

    assert not case.out.exists()


# --- 명령줄 ---------------------------------------------------------------------------


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/merge_dual_review.py", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_exit_codes_and_stdout(tmp_path: Path) -> None:
    ok = two_rule_case(tmp_path, name="ok")
    completed = run_cli(*ok.argv())
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert CONTEXT_PREFIX not in completed.stdout

    rows_a, rows_b = pair_rows("SEX-001", "TTTTTFFFFF", "TTTFFFFFTT", 1)
    bad = Case(tmp_path, rows_a, rows_b, name="bad")
    completed = run_cli(*bad.argv())
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "alert"

    rows_b[0]["label"] = "BOGUS"
    broken = Case(tmp_path, rows_a, rows_b, name="broken")
    completed = run_cli(*broken.argv())
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.startswith("오류:")

    completed = run_cli("--input-a", str(ok.a))
    assert completed.returncode == 1


def test_report_has_no_nan_or_infinite_numbers(tmp_path: Path) -> None:
    # 모든 경계(빈 규칙, 단일 라벨, 전부 uncertain)를 한 번에 거친다.
    a1, b1 = pair_rows("SEX-001", "T" * 10, "T" * 10, 1)
    a2, b2 = pair_rows("AGE-002", "U" * 3, "U" * 3, 11)
    case = Case(tmp_path, a1 + a2, b1 + b2)
    case.run()

    def walk(value: Any) -> None:
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(read_report(case.out))
