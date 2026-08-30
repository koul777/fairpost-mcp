from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from core import FairpostEngine
from tools import summarize_private_review as summary
from tools import build_private_review_queue as queue_builder


ROOT = Path(__file__).resolve().parents[1]


def queue_row(
    review_number: int,
    *,
    rule_id: str = "SEX-001",
    layer: str = "law",
    dimension: str | None = None,
    label: str = "unreviewed",
    context: str = "private context",
    matched_text: str = "match",
) -> dict[str, object]:
    if dimension is None:
        dimension = summary._rule_metadata()[rule_id][1]
    return {
        "review_id": f"{review_number:064x}",
        "rule_id": rule_id,
        "layer": layer,
        "dimension": dimension,
        "context": context,
        "matched_text": matched_text,
        "section": None,
        "label": label,
        "allowed_labels": [
            "true_positive",
            "false_positive",
            "uncertain",
        ],
    }


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_counts_labels_and_calculates_decided_precision(tmp_path: Path) -> None:
    rows = [
        queue_row(1, label="true_positive"),
        queue_row(2, label="true_positive"),
        queue_row(3, label="false_positive"),
        queue_row(4, label="uncertain"),
        queue_row(5),
        queue_row(
            6,
            rule_id="Q-DIST-014",
            layer="question",
            label="uncertain",
        ),
    ]
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "reports" / "summary.json"
    write_jsonl(input_path, rows)

    report = summary.summarize_private_review(input_path, output_path)

    assert report["rules"] == [
        {
            "rule_id": "Q-DIST-014",
            "layer": "question",
            "true_positive": 0,
            "false_positive": 0,
            "uncertain": 1,
            "unreviewed": 0,
            "decided": 0,
            "precision": None,
        },
        {
            "rule_id": "SEX-001",
            "layer": "law",
            "true_positive": 2,
            "false_positive": 1,
            "uncertain": 1,
            "unreviewed": 1,
            "decided": 3,
            "precision": 0.666667,
        },
    ]
    assert report["aggregate"] == {
        "true_positive": 2,
        "false_positive": 1,
        "uncertain": 2,
        "unreviewed": 1,
        "decided": 3,
        "precision": 0.666667,
    }
    assert report["aggregates_by_layer"] == {
        "law": {
            "true_positive": 2,
            "false_positive": 1,
            "uncertain": 1,
            "unreviewed": 1,
            "decided": 3,
            "precision": 0.666667,
        },
        "question": {
            "true_positive": 0,
            "false_positive": 0,
            "uncertain": 1,
            "unreviewed": 0,
            "decided": 0,
            "precision": None,
        },
    }
    assert report["interpretation"]["aggregate_is_row_weighted"] is True
    assert report["interpretation"][
        "aggregate_is_not_posting_level_performance"
    ] is True
    assert report["interpretation"][
        "labels_are_rule_relevance_not_discrimination_or_illegality_determinations"
    ] is True
    assert report["schema_version"] == "private-review-summary-v2"
    assert report["evaluation_phase"] == "train_review"
    assert report["release_claim_eligible"] is False
    assert all(value is False for value in report["privacy_boundary"].values())
    assert json.loads(output_path.read_text(encoding="utf-8")) == report


def test_is_deterministic_for_reordered_input(tmp_path: Path) -> None:
    rows = [
        queue_row(1, label="false_positive"),
        queue_row(2, label="true_positive"),
        queue_row(
            3,
            rule_id="Q-DIST-014",
            layer="question",
            label="unreviewed",
        ),
    ]
    first_input = tmp_path / "first" / "review.jsonl"
    second_input = tmp_path / "second" / "review.jsonl"
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    write_jsonl(first_input, rows)
    write_jsonl(second_input, list(reversed(rows)))

    summary.summarize_private_review(first_input, first_output)
    summary.summarize_private_review(second_input, second_output)

    assert first_output.read_bytes() == second_output.read_bytes()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row.update(extra="secret"), "anonymous queue schema"),
        (lambda row: row.pop("section"), "anonymous queue schema"),
        (lambda row: row.update(review_id="A" * 64), "review_id"),
        (lambda row: row.update(rule_id="NOT-A-RULE"), "rule_id"),
        (lambda row: row.update(layer="question"), "layer"),
        (lambda row: row.update(dimension="wrong"), "dimension"),
        (lambda row: row.update(label="approved"), "label"),
        (
            lambda row: row.update(
                allowed_labels=["false_positive", "true_positive", "uncertain"]
            ),
            "allowed_labels",
        ),
    ],
)
def test_rejects_non_exact_schema_and_metadata(
    tmp_path: Path, mutation: object, message: str
) -> None:
    row = queue_row(1)
    mutation(row)  # type: ignore[operator]
    input_path = tmp_path / "private" / "review.jsonl"
    write_jsonl(input_path, [row])

    with pytest.raises(summary.ReviewSummaryError, match=message):
        summary.summarize_private_review(input_path, tmp_path / "summary.json")


def test_rejects_duplicate_review_id(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    write_jsonl(
        input_path,
        [queue_row(1, label="true_positive"), queue_row(1, label="uncertain")],
    )

    with pytest.raises(summary.ReviewSummaryError, match="duplicate review_id"):
        summary.summarize_private_review(input_path, tmp_path / "summary.json")


def test_rejects_duplicate_json_field(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    write_jsonl(input_path, [queue_row(1)])
    raw = input_path.read_text(encoding="utf-8").rstrip()
    input_path.write_text(raw[:-1] + ',"label":"uncertain"}\n', encoding="utf-8")

    with pytest.raises(summary.ReviewSummaryError, match="duplicate object field"):
        summary.summarize_private_review(input_path, tmp_path / "summary.json")


@pytest.mark.parametrize(
    "partition", ["holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"]
)
@pytest.mark.parametrize("argument", ["input", "output"])
def test_rejects_partition_paths_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partition: str,
    argument: str,
) -> None:
    safe_input = tmp_path / "private" / "review.jsonl"
    safe_output = tmp_path / "summary.json"
    input_path = (
        tmp_path / partition / "review.jsonl" if argument == "input" else safe_input
    )
    output_path = (
        tmp_path / partition / "summary.json" if argument == "output" else safe_output
    )
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read"))

    with pytest.raises(summary.ReviewSummaryError, match="train-only path policy"):
        summary.summarize_private_review(input_path, output_path)


def test_rejects_input_output_collision_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private" / "review.jsonl"
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("must not read"))

    with pytest.raises(summary.ReviewSummaryError, match="different files"):
        summary.summarize_private_review(path, path)


def test_report_does_not_expose_queue_text_or_review_ids(tmp_path: Path) -> None:
    secrets = {
        "context": "candidate.name@example.invalid 010-9999-8888",
        "matched_text": "SECRET_MATCH",
        "review_id": "a" * 64,
    }
    row = queue_row(
        1,
        context=secrets["context"],
        matched_text=secrets["matched_text"],
    )
    row["review_id"] = secrets["review_id"]
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [row])

    report = summary.summarize_private_review(input_path, output_path)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert "context" not in encoded
    assert "matched_text" not in encoded
    for secret in secrets.values():
        assert secret not in encoded


def test_threshold_alerts_are_separate_and_report_is_written(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(
        input_path,
        [
            queue_row(1, label="true_positive"),
            queue_row(2, label="false_positive"),
            queue_row(3, label="unreviewed"),
        ],
    )

    report = summary.summarize_private_review(
        input_path,
        output_path,
        min_reviewed_per_rule=3,
        min_precision=0.75,
    )

    assert report["status"] == "alert"
    assert report["alerts"]["insufficient_reviews"] == [
        {"rule_id": "SEX-001", "reviewed": 2, "minimum": 3}
    ]
    assert report["alerts"]["precision_below_threshold"] == [
        {"rule_id": "SEX-001", "precision": 0.5, "minimum": 0.75}
    ]
    assert report["alerts"]["unverified_provenance"] == [
        {"required_for_quality_gate": True}
    ]


def test_uncertain_rows_do_not_satisfy_minimum_precision_sample(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(
        input_path,
        [queue_row(1, label="true_positive")]
        + [queue_row(index, label="uncertain") for index in range(2, 11)],
    )

    report = summary.summarize_private_review(
        input_path,
        output_path,
        min_reviewed_per_rule=10,
        min_precision=0.8,
        expected_rule_ids=("SEX-001",),
    )

    assert report["rules"][0]["decided"] == 1
    assert report["rules"][0]["uncertain"] == 9
    assert report["rules"][0]["precision"] == 1.0
    assert report["status"] == "alert"
    assert report["alerts"]["insufficient_reviews"] == [
        {"rule_id": "SEX-001", "reviewed": 1, "minimum": 10}
    ]


def test_missing_expected_rule_is_included_and_alerted(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [queue_row(1, label="true_positive")])

    report = summary.summarize_private_review(
        input_path,
        output_path,
        min_reviewed_per_rule=1,
        min_precision=0.8,
        expected_rule_ids=("SEX-001", "PHOTO-001"),
    )

    photo = next(row for row in report["rules"] if row["rule_id"] == "PHOTO-001")
    assert photo["decided"] == 0
    assert photo["precision"] is None
    assert report["status"] == "alert"
    assert report["alerts"]["insufficient_reviews"] == [
        {"rule_id": "PHOTO-001", "reviewed": 0, "minimum": 1}
    ]
    assert report["alerts"]["precision_below_threshold"] == [
        {"rule_id": "PHOTO-001", "precision": None, "minimum": 0.8}
    ]


def test_empty_queue_with_thresholds_cannot_pass(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [])

    report = summary.summarize_private_review(
        input_path,
        output_path,
        min_reviewed_per_rule=10,
        min_precision=0.8,
    )

    assert report["status"] == "alert"
    assert report["alerts"]["empty_queue"] == [
        {"reviewed": 0, "minimum_per_rule": 10, "minimum_precision": 0.8}
    ]


def test_verified_manifest_allows_label_only_quality_gate(tmp_path: Path) -> None:
    text = "여성만 모집합니다."
    snapshot_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(
        snapshot_path,
        [
            {
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": text,
            }
        ],
    )
    queue_builder.build_review_queue(
        snapshot_path,
        queue_path,
        rule_ids=("SEX-001",),
        per_rule=1,
    )
    rows = [json.loads(line) for line in queue_path.read_text("utf-8").splitlines()]
    rows[0]["label"] = "true_positive"
    write_jsonl(queue_path, rows)

    report = summary.summarize_private_review(
        queue_path,
        output_path,
        manifest_path=queue_builder.queue_manifest_path(queue_path),
        source_input_path=snapshot_path,
        min_reviewed_per_rule=1,
        min_precision=0.8,
        expected_rule_ids=("SEX-001",),
    )

    assert report["status"] == "ok"
    assert report["provenance"] == {
        "verified": True,
        "manifest_verified": True,
        "source_input_verified": True,
        "expected_rules_explicit": True,
        "selected_rules_from_manifest": True,
    }
    assert report["alerts"]["unverified_provenance"] == []


def test_manifest_alone_cannot_hide_a_zero_row_expected_rule(
    tmp_path: Path,
) -> None:
    text = "여성만 모집합니다."
    snapshot_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    manifest_path = queue_builder.queue_manifest_path(queue_path)
    write_jsonl(
        snapshot_path,
        [
            {
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": text,
            }
        ],
    )
    queue_builder.build_review_queue(
        snapshot_path,
        queue_path,
        rule_ids=("SEX-001", "FAMILY-001"),
        per_rule=1,
    )
    rows = [json.loads(line) for line in queue_path.read_text("utf-8").splitlines()]
    rows[0]["label"] = "true_positive"
    write_jsonl(queue_path, rows)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["selected_rule_ids"].remove("FAMILY-001")
    manifest["rule_sampling"].pop("FAMILY-001")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = summary.summarize_private_review(
        queue_path,
        tmp_path / "manifest-only-summary.json",
        manifest_path=manifest_path,
        min_reviewed_per_rule=1,
        min_precision=0.8,
    )
    assert report["status"] == "alert"
    assert report["provenance"]["verified"] is False
    assert report["alerts"]["unverified_provenance"]

    with pytest.raises(summary.ReviewSummaryError, match="must match"):
        summary.summarize_private_review(
            queue_path,
            tmp_path / "externally-bound-summary.json",
            manifest_path=manifest_path,
            source_input_path=snapshot_path,
            min_reviewed_per_rule=1,
            min_precision=0.8,
            expected_rule_ids=("SEX-001", "FAMILY-001"),
        )


def test_manifest_sampling_must_match_each_rules_actual_rows(tmp_path: Path) -> None:
    texts = [
        "여성만 모집하며 TOEIC 750점 이상을 요구합니다.",
        "추가 사례에서는 여성만 모집합니다.",
    ]
    snapshot_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    manifest_path = queue_builder.queue_manifest_path(queue_path)
    write_jsonl(
        snapshot_path,
        [
            {
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": text,
            }
            for text in texts
        ],
    )
    queue_builder.build_review_queue(
        snapshot_path,
        queue_path,
        rule_ids=("SEX-001", "Q-DIST-014"),
        per_rule=2,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rule_sampling"]["SEX-001"], manifest["rule_sampling"][
        "Q-DIST-014"
    ] = (
        manifest["rule_sampling"]["Q-DIST-014"],
        manifest["rule_sampling"]["SEX-001"],
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(summary.ReviewSummaryError, match="per-rule queue rows"):
        summary.summarize_private_review(
            queue_path,
            tmp_path / "summary.json",
            manifest_path=manifest_path,
        )


def test_source_input_hash_must_match_queue_manifest(tmp_path: Path) -> None:
    source_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    first = "여성만 모집합니다."
    write_jsonl(
        source_path,
        [
            {
                "content_hash": hashlib.sha256(first.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": first,
            }
        ],
    )
    queue_builder.build_review_queue(
        source_path,
        queue_path,
        rule_ids=("SEX-001",),
    )
    second = "남성만 모집합니다."
    write_jsonl(
        source_path,
        [
            {
                "content_hash": hashlib.sha256(second.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": second,
            }
        ],
    )

    with pytest.raises(summary.ReviewSummaryError, match="input_sha256"):
        summary.summarize_private_review(
            queue_path,
            tmp_path / "summary.json",
            manifest_path=queue_builder.queue_manifest_path(queue_path),
            source_input_path=source_path,
        )


def test_self_consistent_forged_queue_must_be_derived_from_source(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    manifest_path = queue_builder.queue_manifest_path(queue_path)
    text = "직무와 전형 절차를 안내합니다."
    write_jsonl(
        source_path,
        [
            {
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": text,
            }
        ],
    )
    forged_rows = [queue_row(1, label="true_positive")]
    write_jsonl(queue_path, forged_rows)
    manifest = queue_builder.build_queue_manifest(
        forged_rows,
        engine=FairpostEngine(),
        input_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        selected_rule_ids=("SEX-001",),
        per_rule=1,
        context_chars=120,
        rule_sampling={
            "SEX-001": {
                "candidate_matches": 1,
                "unique_contexts": 1,
                "selected_rows": 1,
                "collapsed_duplicate_contexts": 0,
                "truncated_unique_contexts": 0,
            }
        },
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(summary.ReviewSummaryError, match="does not reproduce"):
        summary.summarize_private_review(
            queue_path,
            tmp_path / "summary.json",
            manifest_path=manifest_path,
            source_input_path=source_path,
            min_reviewed_per_rule=1,
            min_precision=0.8,
            expected_rule_ids=("SEX-001",),
        )


@pytest.mark.parametrize("mutation", ["context", "ruleset_version"])
def test_manifest_rejects_immutable_or_stale_queue_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    text = "여성만 모집합니다."
    snapshot_path = tmp_path / "snapshot" / "train" / "records.jsonl"
    queue_path = tmp_path / "private" / "queue.jsonl"
    manifest_path = queue_builder.queue_manifest_path(queue_path)
    write_jsonl(
        snapshot_path,
        [
            {
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sector": "private",
                "split": "train",
                "text": text,
            }
        ],
    )
    queue_builder.build_review_queue(
        snapshot_path,
        queue_path,
        rule_ids=("SEX-001",),
    )
    if mutation == "context":
        rows = [
            json.loads(line) for line in queue_path.read_text("utf-8").splitlines()
        ]
        rows[0]["context"] += " changed"
        write_jsonl(queue_path, rows)
        expected = "immutable row digest"
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["ruleset_version"] = "stale-ruleset"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        expected = "ruleset_version is stale"

    with pytest.raises(summary.ReviewSummaryError, match=expected):
        summary.summarize_private_review(
            queue_path,
            tmp_path / "summary.json",
            manifest_path=manifest_path,
            min_reviewed_per_rule=1,
            min_precision=0.8,
        )


def test_atomic_report_failure_preserves_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [queue_row(1, label="true_positive")])
    output_path.write_text("previous report\n", encoding="utf-8")
    monkeypatch.setattr(
        summary.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated")),
    )

    with pytest.raises(summary.ReviewSummaryError, match="could not be written"):
        summary.summarize_private_review(input_path, output_path)

    assert output_path.read_text(encoding="utf-8") == "previous report\n"
    assert not list(tmp_path.rglob("*.fairpost-temp-*"))
    assert output_path.exists()


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/summarize_private_review.py", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_success_stdout_is_aggregate_only(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [queue_row(1, label="true_positive")])

    completed = run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == json.loads(
        output_path.read_text(encoding="utf-8")
    )["aggregate"]
    assert "SEX-001" not in completed.stdout
    assert "review_id" not in completed.stdout


def test_cli_gate_failure_writes_report_then_exits_two(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [queue_row(1, label="false_positive")])

    completed = run_cli(
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--min-reviewed-per-rule",
        "2",
        "--min-precision",
        "0.8",
    )

    assert completed.returncode == 2
    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "alert"
    assert json.loads(completed.stdout)["precision"] == 0.0


def test_cli_empty_queue_gate_exits_two(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    write_jsonl(input_path, [])

    completed = run_cli(
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--min-reviewed-per-rule",
        "1",
        "--min-precision",
        "0.8",
        "--expect-rule-id",
        "SEX-001",
    )

    assert completed.returncode == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "alert"
    assert report["rules"][0]["rule_id"] == "SEX-001"
    assert report["rules"][0]["decided"] == 0


def test_cli_invalid_input_exits_one_without_leaking_row(tmp_path: Path) -> None:
    input_path = tmp_path / "private" / "review.jsonl"
    output_path = tmp_path / "summary.json"
    secret = "HIGHLY_SENSITIVE_CONTEXT"
    write_jsonl(input_path, [queue_row(1, label="invalid", context=secret)])

    completed = run_cli("--input", str(input_path), "--output", str(output_path))

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert secret not in completed.stderr
    assert "0" * 63 not in completed.stderr
    assert not output_path.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--min-reviewed-per-rule", "-1"), "non-negative integer"),
        (("--min-precision", "1.01"), "between 0 and 1"),
        (("--min-precision", "nan"), "between 0 and 1"),
    ],
)
def test_cli_invalid_threshold_exits_one_before_reading(
    tmp_path: Path, arguments: tuple[str, str], message: str
) -> None:
    input_path = tmp_path / "private" / "missing.jsonl"
    output_path = tmp_path / "summary.json"

    completed = run_cli(
        "--input", str(input_path), "--output", str(output_path), *arguments
    )

    assert completed.returncode == 1
    assert message in completed.stderr
    assert "could not be read" not in completed.stderr
