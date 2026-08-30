from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import run_private_fairness_cycle as cycle
from tools import run_private_monitoring as monitoring


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_private_fairness_cycle.py"


def posting(
    text: str,
    *,
    source_url: str = "https://careers.example.com/jobs/1",
    organization: str = "비공개회사",
    source_category: str = "company-career-page",
) -> dict[str, str]:
    return {
        "source_category": source_category,
        "source_url": source_url,
        "published_at": "2026-08-01",
        "organization": organization,
        "text": text,
    }


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_baseline(tmp_path: Path, rows: list[object]) -> Path:
    input_path = tmp_path / "baseline-input.jsonl"
    audit_path = tmp_path / "baseline-audit.json"
    write_jsonl(input_path, rows)
    monitoring.run_monitoring(
        input_path,
        tmp_path / "baseline-snapshot",
        tmp_path / "baseline-summary.json",
        audit_path,
    )
    return audit_path


def cli_args(tmp_path: Path, baseline: Path) -> list[str]:
    return [
        sys.executable,
        str(TOOL),
        "--input",
        str(tmp_path / "current-input.jsonl"),
        "--output-dir",
        str(tmp_path / "current-snapshot"),
        "--snapshot-summary",
        str(tmp_path / "current-summary.json"),
        "--audit-output",
        str(tmp_path / "current-audit.json"),
        "--baseline-audit",
        str(baseline),
        "--drift-output",
        str(tmp_path / "current-drift.json"),
        "--review-queue-output",
        str(tmp_path / "current-review.jsonl"),
        "--sampling-audit-output",
        str(tmp_path / "current-review-sampling.json"),
    ]


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_cli_runs_connected_cycle_and_repeated_selected_rules(tmp_path: Path) -> None:
    text = "젊은 여성만 모집합니다. 직무 역량을 평가합니다."
    baseline = make_baseline(
        tmp_path,
        [posting(text, source_url="https://careers.example.com/baseline")],
    )
    write_jsonl(
        tmp_path / "current-input.jsonl",
        [posting(text, source_url="https://careers.example.com/current")],
    )
    args = cli_args(tmp_path, baseline) + [
        "--rule-id",
        "SEX-001",
        "--rule-id",
        "AGE-001",
        "--per-rule",
        "1",
        "--high-frequency-threshold",
        "0.5",
        "--max-record-rate-delta",
        "0.25",
        "--max-source-share-delta",
        "0.3",
    ]

    completed = run_cli(args)

    assert completed.returncode == 0, completed.stderr
    stdout = json.loads(completed.stdout)
    assert stdout == {
        "alerts": 0,
        "audit_records": 1,
        "queued_reviews": 2,
        "snapshot_records": 1,
        "status": "ok",
    }
    assert "SEX-001" not in completed.stdout
    assert "AGE-001" not in completed.stdout

    records_path = tmp_path / "current-snapshot" / "train" / "records.jsonl"
    manifest_path = tmp_path / "current-snapshot" / "train" / "manifest.json"
    summary = json.loads((tmp_path / "current-summary.json").read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    audit = json.loads((tmp_path / "current-audit.json").read_text("utf-8"))
    drift = json.loads((tmp_path / "current-drift.json").read_text("utf-8"))
    queue_rows = read_jsonl(tmp_path / "current-review.jsonl")
    queue_manifest = json.loads(
        cycle.review_queue.queue_manifest_path(
            tmp_path / "current-review.jsonl"
        ).read_text("utf-8")
    )
    sampling = json.loads(
        (tmp_path / "current-review-sampling.json").read_text("utf-8")
    )
    records_hash = hashlib.sha256(records_path.read_bytes()).hexdigest()

    assert manifest["records_sha256"] == records_hash
    assert summary["snapshot_hash"] == records_hash
    assert audit["input"]["sha256"] == records_hash
    assert audit["input"]["manifest_verified"] is True
    assert audit["summary"]["high_frequency_threshold"] == 0.5
    assert drift["records"] == {"baseline": 1, "current": 1, "delta": 0}
    assert drift["thresholds"]["max_record_rate_delta"] == 0.25
    assert drift["thresholds"]["max_source_share_delta"] == 0.3
    assert {row["rule_id"] for row in queue_rows} == {"SEX-001", "AGE-001"}
    assert queue_manifest["schema_version"] == "private-review-queue-manifest-v1"
    assert queue_manifest["selected_rule_ids"] == ["AGE-001", "SEX-001"]
    assert queue_manifest["row_count"] == 2
    assert sampling["selection"]["selected_rows"] == 2
    assert sampling["selection"]["selected_rule_count"] == 2
    assert all(value is False for value in sampling["privacy_boundary"].values())


def test_alert_exits_two_after_preserving_every_artifact(tmp_path: Path) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    write_jsonl(
        tmp_path / "current-input.jsonl",
        [posting("여성만 모집합니다.", source_url="https://careers.example.com/new")],
    )

    completed = run_cli(cli_args(tmp_path, baseline) + ["--rule-id", "SEX-001"])

    assert completed.returncode == 2, completed.stderr
    assert json.loads(completed.stdout)["status"] == "alert"
    assert json.loads(completed.stdout)["alerts"] > 0
    assert json.loads(
        (tmp_path / "current-drift.json").read_text(encoding="utf-8")
    )["status"] == "alert"
    for path in (
        tmp_path / "current-snapshot" / "train" / "records.jsonl",
        tmp_path / "current-snapshot" / "train" / "manifest.json",
        tmp_path / "current-summary.json",
        tmp_path / "current-audit.json",
        tmp_path / "current-drift.json",
        tmp_path / "current-review.jsonl",
        cycle.review_queue.queue_manifest_path(tmp_path / "current-review.jsonl"),
        tmp_path / "current-review-sampling.json",
    ):
        assert path.exists()
    assert {row["rule_id"] for row in read_jsonl(tmp_path / "current-review.jsonl")} == {
        "SEX-001"
    }


def test_malformed_input_error_exposes_no_pii_path_hash_or_rule_id(
    tmp_path: Path,
) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    secret_path = tmp_path / "customer-secret-path.jsonl"
    secret_hash = "a" * 64
    secret_pii = "private.person@example.com"
    secret_path.write_text(f"{{broken {secret_pii} {secret_hash}\n", encoding="utf-8")
    args = cli_args(tmp_path, baseline)
    args[args.index(str(tmp_path / "current-input.jsonl"))] = str(secret_path)
    args += ["--rule-id", "SEX-001"]

    completed = run_cli(args)

    assert completed.returncode == 1
    combined = completed.stdout + completed.stderr
    for secret in (str(secret_path), "customer-secret-path", secret_pii, secret_hash, "SEX-001"):
        assert secret not in combined
    assert "Traceback" not in combined
    assert not (tmp_path / "current-drift.json").exists()
    assert not (tmp_path / "current-review.jsonl").exists()


def test_invalid_rule_and_argument_values_are_not_echoed(tmp_path: Path) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    write_jsonl(tmp_path / "current-input.jsonl", [posting("여성만 모집합니다.")])
    invalid_rule = "SECRET-CUSTOMER-RULE"
    completed = run_cli(cli_args(tmp_path, baseline) + ["--rule-id", invalid_rule])
    assert completed.returncode == 1
    assert invalid_rule not in completed.stderr

    invalid_threshold = "secret-threshold-value"
    completed = run_cli(
        cli_args(tmp_path, baseline)
        + ["--max-record-rate-delta", invalid_threshold]
    )
    assert completed.returncode == 1
    assert invalid_threshold not in completed.stderr


def test_output_collision_is_rejected_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "shared.json"
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("read"))
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("read"))

    with pytest.raises(ValueError, match="distinct|differ"):
        cycle.validate_cycle_paths(
            tmp_path / "missing-input.jsonl",
            tmp_path / "snapshot",
            tmp_path / "summary.json",
            shared,
            tmp_path / "missing-baseline.json",
            shared,
            tmp_path / "queue.jsonl",
        )


def test_hardlink_input_output_alias_is_rejected_and_preserved(tmp_path: Path) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    queue_path = tmp_path / "queue.jsonl"
    os.link(baseline, queue_path)
    before = baseline.read_bytes()

    with pytest.raises(ValueError, match="distinct"):
        cycle.validate_cycle_paths(
            tmp_path / "missing-input.jsonl",
            tmp_path / "snapshot",
            tmp_path / "summary.json",
            tmp_path / "audit.json",
            baseline,
            tmp_path / "drift.json",
            queue_path,
        )

    assert baseline.read_bytes() == queue_path.read_bytes() == before


@pytest.mark.parametrize(
    "forbidden", ["holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"]
)
def test_every_forbidden_partition_is_rejected_before_read(
    tmp_path: Path, forbidden: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("read"))
    with pytest.raises(ValueError):
        cycle.validate_cycle_paths(
            tmp_path / forbidden / "input.jsonl",
            tmp_path / "snapshot",
            tmp_path / "summary.json",
            tmp_path / "audit.json",
            tmp_path / "baseline.json",
            tmp_path / "drift.json",
            tmp_path / "queue.jsonl",
        )


@pytest.mark.parametrize(
    ("position", "argument"),
    [
        (1, "--output-dir"),
        (2, "--snapshot-summary"),
        (3, "--audit-output"),
        (4, "--baseline-audit"),
        (5, "--drift-output"),
        (6, "--review-queue-output"),
        (7, "--sampling-audit-output"),
    ],
)
def test_each_cycle_path_uses_train_only_policy(
    tmp_path: Path, position: int, argument: str
) -> None:
    paths = [
        tmp_path / "input.jsonl",
        tmp_path / "snapshot",
        tmp_path / "summary.json",
        tmp_path / "audit.json",
        tmp_path / "baseline.json",
        tmp_path / "drift.json",
        tmp_path / "queue.jsonl",
        tmp_path / "sampling.json",
    ]
    paths[position] = tmp_path / "holdout" / paths[position].name
    with pytest.raises(ValueError, match=argument):
        cycle.validate_cycle_paths(*paths)


def test_help_includes_local_review_queue_risk_warning() -> None:
    completed = run_cli([sys.executable, str(TOOL), "--help"])

    assert completed.returncode == 0
    assert "LOCAL-RISK WARNING" in completed.stdout
    assert "로컬 위험 경고" in completed.stdout
    assert "do not transmit" in completed.stdout


def test_review_queue_failure_preserves_every_previous_cycle_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    input_path = tmp_path / "current-input.jsonl"
    write_jsonl(input_path, [posting("여성만 모집합니다.")])
    output_dir = tmp_path / "current-snapshot"
    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"
    summary_path = tmp_path / "current-summary.json"
    audit_path = tmp_path / "current-audit.json"
    drift_path = tmp_path / "current-drift.json"
    queue_path = tmp_path / "current-review.jsonl"
    queue_manifest_path = cycle.review_queue.queue_manifest_path(queue_path)
    targets = (
        records_path,
        manifest_path,
        summary_path,
        audit_path,
        drift_path,
        queue_path,
        queue_manifest_path,
    )
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"old-{index}", encoding="utf-8")

    def fail_queue(*_args: object, **_kwargs: object) -> dict[str, int]:
        raise OSError("simulated late-stage failure")

    monkeypatch.setattr(cycle.review_queue, "build_review_queue", fail_queue)

    with pytest.raises(cycle.PrivateFairnessCycleError, match="review queue"):
        cycle.run_cycle(
            input_path,
            output_dir,
            summary_path,
            audit_path,
            baseline,
            drift_path,
            queue_path,
            rule_ids=("SEX-001",),
        )

    assert [path.read_text(encoding="utf-8") for path in targets] == [
        f"old-{index}" for index in range(7)
    ]


def test_cycle_publish_failure_restores_all_seven_previous_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    input_path = tmp_path / "current-input.jsonl"
    write_jsonl(input_path, [posting("여성만 모집합니다.")])
    output_dir = tmp_path / "current-snapshot"
    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"
    summary_path = tmp_path / "current-summary.json"
    audit_path = tmp_path / "current-audit.json"
    drift_path = tmp_path / "current-drift.json"
    queue_path = tmp_path / "current-review.jsonl"
    queue_manifest_path = cycle.review_queue.queue_manifest_path(queue_path)
    targets = (
        records_path,
        manifest_path,
        summary_path,
        audit_path,
        drift_path,
        queue_path,
        queue_manifest_path,
    )
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"old-{index}", encoding="utf-8")

    original_replace = cycle.monitoring.os.replace
    public_publish_calls = 0

    def fail_third_publish(source: object, target: object) -> None:
        nonlocal public_publish_calls
        # Count only publication to the seven public cycle targets. Snapshot,
        # audit, and drift now use their own atomic replaces in the temp root.
        if Path(target) in targets:
            public_publish_calls += 1
            if public_publish_calls == 4:
                raise OSError("simulated cycle publish failure")
        original_replace(source, target)

    monkeypatch.setattr(cycle.monitoring.os, "replace", fail_third_publish)

    with pytest.raises(cycle.PrivateFairnessCycleError, match="publish"):
        cycle.run_cycle(
            input_path,
            output_dir,
            summary_path,
            audit_path,
            baseline,
            drift_path,
            queue_path,
            rule_ids=("SEX-001",),
        )

    assert [path.read_text(encoding="utf-8") for path in targets] == [
        f"old-{index}" for index in range(7)
    ]
    assert not list(tmp_path.rglob("*.fairpost-backup-*"))


def test_require_version_match_alert_reaches_cli_exit_two(tmp_path: Path) -> None:
    baseline = make_baseline(tmp_path, [posting("직무 역량을 평가합니다.")])
    baseline_payload = json.loads(baseline.read_text(encoding="utf-8"))
    baseline_payload["ruleset_version"] = "older-ruleset-version"
    baseline.write_text(
        json.dumps(baseline_payload, ensure_ascii=False), encoding="utf-8"
    )
    write_jsonl(
        tmp_path / "current-input.jsonl",
        [posting("직무 역량을 평가합니다.")],
    )

    completed = run_cli(cli_args(tmp_path, baseline) + ["--require-version-match"])

    assert completed.returncode == 2, completed.stderr
    assert json.loads(completed.stdout)["status"] == "alert"
    drift = json.loads((tmp_path / "current-drift.json").read_text("utf-8"))
    assert drift["status"] == "alert"
    assert any(alert["type"] == "version_mismatch" for alert in drift["alerts"])


def test_baseline_is_pinned_once_for_audit_and_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [posting("직무 역량을 평가합니다.")]
    baseline = make_baseline(tmp_path, rows)
    input_path = tmp_path / "current-input.jsonl"
    write_jsonl(input_path, rows)
    original_run_monitoring = cycle.monitoring.run_monitoring

    def replace_source_baseline_then_monitor(
        *args: object, **kwargs: object
    ) -> dict[str, int | bool | str]:
        source_payload = json.loads(baseline.read_text(encoding="utf-8"))
        source_payload["input"]["records"] = 999
        baseline.write_text(
            json.dumps(source_payload, ensure_ascii=False), encoding="utf-8"
        )
        pinned_path = Path(kwargs["baseline_path"])
        assert pinned_path != baseline
        pinned_payload = json.loads(pinned_path.read_text(encoding="utf-8"))
        assert pinned_payload["input"]["records"] == 1
        return original_run_monitoring(*args, **kwargs)

    monkeypatch.setattr(
        cycle.monitoring, "run_monitoring", replace_source_baseline_then_monitor
    )
    audit_path = tmp_path / "current-audit.json"
    drift_path = tmp_path / "current-drift.json"

    cycle.run_cycle(
        input_path,
        tmp_path / "current-snapshot",
        tmp_path / "current-summary.json",
        audit_path,
        baseline,
        drift_path,
        tmp_path / "current-review.jsonl",
    )

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    drift = json.loads(drift_path.read_text(encoding="utf-8"))
    assert audit["change_from_baseline"]["record_delta"] == 0
    assert drift["records"] == {"baseline": 1, "current": 1, "delta": 0}
