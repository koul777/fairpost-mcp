from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import run_private_monitoring as runner


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_private_monitoring.py"


def posting(
    *,
    source_url: str = "https://careers.example.com/jobs/1",
    published_at: str = "2026-07-01",
    organization: str = "비공개회사",
    text: str = "백엔드 개발자를 채용합니다.",
    source_category: str = "company-career-page",
) -> dict[str, str]:
    return {
        "source_category": source_category,
        "source_url": source_url,
        "published_at": published_at,
        "organization": organization,
        "text": text,
    }


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def cli_args(tmp_path: Path, *, name: str = "run") -> list[str]:
    return [
        sys.executable,
        str(TOOL),
        "--input",
        str(tmp_path / f"{name}-input.jsonl"),
        "--output-dir",
        str(tmp_path / f"{name}-snapshot"),
        "--snapshot-summary",
        str(tmp_path / f"{name}-snapshot-summary.json"),
        "--audit-output",
        str(tmp_path / f"{name}-audit.json"),
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


def test_cli_runs_snapshot_then_anonymous_audit_without_exposing_private_data(
    tmp_path: Path,
) -> None:
    secret_url = "https://careers.example.com/private/secret-777"
    secret_org = "한결비밀회사"
    secret_text = "여성만 모집 담당자: 홍길동 private.person@example.com 010-9876-5432"
    args = cli_args(tmp_path)
    write_jsonl(
        tmp_path / "run-input.jsonl",
        [
            posting(
                source_url=secret_url,
                organization=secret_org,
                text=secret_text,
            )
        ],
    )

    completed = run_cli(args)

    assert completed.returncode == 0, completed.stderr
    stdout = json.loads(completed.stdout)
    assert stdout == {
        "audit_records": 1,
        "baseline_compared": False,
        "records_with_law_findings": 1,
        "snapshot_records": 1,
        "status": "ok",
    }
    records_path = tmp_path / "run-snapshot" / "train" / "records.jsonl"
    record = json.loads(records_path.read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "run-audit.json").read_text(encoding="utf-8"))
    assert audit["input"]["sha256"] == hashlib.sha256(
        records_path.read_bytes()
    ).hexdigest()
    assert audit["input"]["records"] == 1
    assert audit["input"]["split"] == "train_only"
    assert audit["input"]["manifest_verified"] is True
    assert audit["privacy_boundary"]["contains_posting_text"] is False

    serialized_stdout = completed.stdout
    for private_value in (
        secret_url,
        secret_org,
        secret_text,
        "홍길동",
        "private.person@example.com",
        "010-9876-5432",
        record["id"],
        record["content_hash"],
    ):
        assert private_value not in serialized_stdout
    assert "http" not in serialized_stdout.casefold()
    assert set(stdout).isdisjoint({"id", "ids", "url", "text", "path"})


def test_runner_is_deterministic_for_reordered_input(tmp_path: Path) -> None:
    rows = [
        posting(
            source_url="https://careers.example.com/jobs/2",
            published_at="2026-07-02",
            text="데이터 분석가를 채용합니다.",
        ),
        posting(
            source_url="https://feed.example.net/jobs/1",
            source_category="licensed-feed",
            text="장애인 우대 품질 담당자를 채용합니다.",
        ),
    ]
    first_args = cli_args(tmp_path, name="first")
    second_args = cli_args(tmp_path, name="second")
    write_jsonl(tmp_path / "first-input.jsonl", rows)
    write_jsonl(tmp_path / "second-input.jsonl", list(reversed(rows)))

    first = run_cli(first_args)
    second = run_cli(second_args)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    for left, right in (
        (
            tmp_path / "first-snapshot" / "train" / "records.jsonl",
            tmp_path / "second-snapshot" / "train" / "records.jsonl",
        ),
        (
            tmp_path / "first-snapshot" / "train" / "manifest.json",
            tmp_path / "second-snapshot" / "train" / "manifest.json",
        ),
        (
            tmp_path / "first-snapshot-summary.json",
            tmp_path / "second-snapshot-summary.json",
        ),
        (tmp_path / "first-audit.json", tmp_path / "second-audit.json"),
    ):
        assert left.read_bytes() == right.read_bytes()


def test_committed_example_runs_the_documented_offline_pipeline(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "example-snapshot"
    summary_path = tmp_path / "example-summary.json"
    audit_path = tmp_path / "example-audit.json"

    stdout_summary = runner.run_monitoring(
        ROOT / "examples" / "private_monitoring_input.example.jsonl",
        output_dir,
        summary_path,
        audit_path,
    )

    assert stdout_summary["status"] == "ok"
    assert stdout_summary["snapshot_records"] == 2
    assert stdout_summary["audit_records"] == 2
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    photo = next(row for row in audit["law_rules"] if row["id"] == "PHOTO-001")
    gender_review = next(
        row for row in audit["questions"] if row["id"] == "Q-DIST-015"
    )
    assert photo["records"] == 1
    assert gender_review["records"] == 1

    records_text = (output_dir / "train" / "records.jsonl").read_text(
        encoding="utf-8"
    )
    assert "예시기술" not in records_text
    assert "예시서비스" not in records_text
    assert json.loads(summary_path.read_text(encoding="utf-8"))["counts"][
        "written"
    ] == 2


def test_repeated_excludes_and_baseline_are_forwarded(tmp_path: Path) -> None:
    rows = [
        posting(source_url="https://careers.example.com/1", text="첫 공고"),
        posting(source_url="https://careers.example.com/2", text="둘째 공고"),
        posting(source_url="https://careers.example.com/3", text="셋째 공고"),
    ]
    baseline_args = cli_args(tmp_path, name="baseline")
    write_jsonl(tmp_path / "baseline-input.jsonl", [rows[2]])
    baseline_completed = run_cli(baseline_args)
    assert baseline_completed.returncode == 0, baseline_completed.stderr

    from tools import build_private_monitoring_snapshot as snapshot

    hashes = [
        hashlib.sha256(
            snapshot._clean_text(row["text"], row["organization"]).encode("utf-8")
        ).hexdigest()
        for row in rows
    ]
    first_manifest = tmp_path / "exclude-one.json"
    second_manifest = tmp_path / "exclude-two.json"
    first_manifest.write_text(
        json.dumps({"content_hashes": [hashes[0]]}), encoding="utf-8"
    )
    second_manifest.write_text(
        json.dumps({"content_hashes": [hashes[1]]}), encoding="utf-8"
    )
    args = cli_args(tmp_path, name="current") + [
        "--exclude-manifest",
        str(first_manifest),
        "--exclude-manifest",
        str(second_manifest),
        "--baseline",
        str(tmp_path / "baseline-audit.json"),
        "--high-frequency-threshold",
        "0.5",
    ]
    write_jsonl(tmp_path / "current-input.jsonl", rows)

    completed = run_cli(args)

    assert completed.returncode == 0, completed.stderr
    stdout = json.loads(completed.stdout)
    audit = json.loads((tmp_path / "current-audit.json").read_text(encoding="utf-8"))
    summary = json.loads(
        (tmp_path / "current-snapshot-summary.json").read_text(encoding="utf-8")
    )
    assert stdout["baseline_compared"] is True
    assert stdout["audit_records"] == 1
    assert summary["counts"]["excluded_content_hash"] == 2
    assert audit["summary"]["high_frequency_threshold"] == 0.5
    assert audit["change_from_baseline"]["record_delta"] == 0


@pytest.mark.parametrize(
    ("argument", "path_factory"),
    [
        ("--input", lambda root, word: root / word / "input.jsonl"),
        ("--output-dir", lambda root, word: root / word / "snapshot"),
        (
            "--snapshot-summary",
            lambda root, word: root / word / "snapshot-summary.json",
        ),
        ("--audit-output", lambda root, word: root / word / "audit.json"),
        (
            "--exclude-manifest",
            lambda root, word: root / word / "manifest.json",
        ),
        ("--baseline", lambda root, word: root / word / "baseline.json"),
    ],
)
@pytest.mark.parametrize(
    "forbidden", ["holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"]
)
def test_every_user_path_rejects_non_train_partition(
    tmp_path: Path,
    argument: str,
    path_factory,
    forbidden: str,
) -> None:
    values = {
        "--input": tmp_path / "safe-input.jsonl",
        "--output-dir": tmp_path / "safe-snapshot",
        "--snapshot-summary": tmp_path / "safe-summary.json",
        "--audit-output": tmp_path / "safe-audit.json",
        "--exclude-manifest": tmp_path / "safe-manifest.json",
        "--baseline": tmp_path / "safe-baseline.json",
    }
    values[argument] = path_factory(tmp_path, forbidden)

    with pytest.raises(ValueError, match=argument):
        runner.validate_run_paths(
            values["--input"],
            values["--output-dir"],
            values["--snapshot-summary"],
            values["--audit-output"],
            exclude_manifests=[values["--exclude-manifest"]],
            baseline_path=values["--baseline"],
        )


def test_all_paths_are_preflighted_before_baseline_or_input_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden_baseline = tmp_path / "holdout" / "baseline.json"
    monkeypatch.setattr(
        runner,
        "_load_baseline",
        lambda path: pytest.fail("baseline must not be read"),
    )
    monkeypatch.setattr(
        runner.snapshot_builder,
        "build_snapshot",
        lambda *args, **kwargs: pytest.fail("input must not be read"),
    )

    with pytest.raises(runner.MonitoringRunError, match="경로 사전 검사 실패"):
        runner.run_monitoring(
            tmp_path / "missing-input.jsonl",
            tmp_path / "safe-snapshot",
            tmp_path / "safe-summary.json",
            tmp_path / "safe-audit.json",
            baseline_path=forbidden_baseline,
        )


@pytest.mark.parametrize(
    ("summary_relative", "audit_relative"),
    [
        ("train/records.jsonl", "audit.json"),
        ("train/manifest.json", "audit.json"),
        ("summary.json", "train/records.jsonl"),
        ("summary.json", "train/manifest.json"),
        ("shared.json", "shared.json"),
    ],
)
def test_rejects_output_collisions_before_read(
    tmp_path: Path, summary_relative: str, audit_relative: str
) -> None:
    output_dir = tmp_path / "snapshot"
    summary_path = output_dir / Path(summary_relative)
    audit_path = output_dir / Path(audit_relative)

    with pytest.raises(ValueError, match="충돌|train 밖"):
        runner.validate_run_paths(
            tmp_path / "missing-input.jsonl",
            output_dir,
            summary_path,
            audit_path,
        )


def test_rejects_input_collision_with_audit_output_before_read(tmp_path: Path) -> None:
    shared = tmp_path / "shared.jsonl"
    with pytest.raises(ValueError, match="--input.*출력"):
        runner.validate_run_paths(
            shared,
            tmp_path / "snapshot",
            tmp_path / "summary.json",
            shared,
        )


def test_semantic_baseline_failure_leaves_no_published_outputs(
    tmp_path: Path,
) -> None:
    args = cli_args(tmp_path)
    write_jsonl(tmp_path / "run-input.jsonl", [posting()])
    invalid_baseline = tmp_path / "invalid-baseline.json"
    invalid_baseline.write_text(
        json.dumps(
            {
                "input": {
                    "records": 1,
                    "sector": "public",
                    "split": "train_only",
                },
                "law_rule_posting_hits": {},
                "question_posting_hits": {},
                "slot_found_posting_hits": {},
            }
        ),
        encoding="utf-8",
    )
    args.extend(["--baseline", str(invalid_baseline)])

    completed = run_cli(args)

    assert completed.returncode != 0
    assert "공정성 감사 실패" in completed.stderr
    assert not (tmp_path / "run-snapshot").exists()
    assert not (tmp_path / "run-snapshot-summary.json").exists()
    assert not (tmp_path / "run-audit.json").exists()


def test_cli_reports_meaningful_failure_without_partial_snapshot(
    tmp_path: Path,
) -> None:
    args = cli_args(tmp_path)
    write_jsonl(tmp_path / "run-input.jsonl", [posting()])
    malformed_baseline = tmp_path / "malformed-baseline.json"
    malformed_baseline.write_text("{not-json", encoding="utf-8")
    args.extend(["--baseline", str(malformed_baseline)])

    completed = run_cli(args)

    assert completed.returncode != 0
    assert "기준선 읽기 실패" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not (tmp_path / "run-snapshot").exists()
    assert not (tmp_path / "run-audit.json").exists()


def test_missing_baseline_error_does_not_echo_sensitive_path(tmp_path: Path) -> None:
    args = cli_args(tmp_path)
    write_jsonl(tmp_path / "run-input.jsonl", [posting()])
    secret = "customer-secret-company"
    baseline = tmp_path / secret / "baseline.json"
    args.extend(["--baseline", str(baseline)])

    completed = run_cli(args)

    assert completed.returncode != 0
    assert "기준선 읽기 실패" in completed.stderr
    assert secret not in completed.stderr
    assert str(baseline) not in completed.stderr


def test_publish_failure_removes_every_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = []
    pairs = []
    for index in range(4):
        source = tmp_path / "stage" / f"source-{index}.json"
        target = tmp_path / "published" / f"target-{index}.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"new-{index}", encoding="utf-8")
        sources.append(source)
        pairs.append((source, target))
    original_replace = runner.os.replace
    calls = 0

    def fail_third_replace(source: object, target: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated publish failure")
        original_replace(source, target)

    monkeypatch.setattr(runner.os, "replace", fail_third_replace)

    with pytest.raises(runner.MonitoringRunError, match="게시 실패"):
        runner._publish_files(pairs)

    assert not any(target.exists() for _source, target in pairs)
    assert not list(tmp_path.rglob("*.fairpost-backup-*"))


def test_publish_failure_restores_every_previous_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = []
    for index in range(4):
        source = tmp_path / "stage" / f"source-{index}.json"
        target = tmp_path / "published" / f"target-{index}.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"new-{index}", encoding="utf-8")
        target.write_text(f"old-{index}", encoding="utf-8")
        pairs.append((source, target))
    original_replace = runner.os.replace
    calls = 0

    def fail_third_publish(source: object, target: object) -> None:
        nonlocal calls
        calls += 1
        # Four old-target backup renames precede the publish renames.
        if calls == 7:
            raise OSError("simulated publish failure")
        original_replace(source, target)

    monkeypatch.setattr(runner.os, "replace", fail_third_publish)

    with pytest.raises(runner.MonitoringRunError, match="게시 실패"):
        runner._publish_files(pairs)

    assert [target.read_text(encoding="utf-8") for _source, target in pairs] == [
        f"old-{index}" for index in range(4)
    ]
    assert not list(tmp_path.rglob("*.fairpost-backup-*"))


def test_backup_phase_failure_preserves_unprocessed_previous_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = []
    for index in range(4):
        source = tmp_path / "stage" / f"source-{index}.json"
        target = tmp_path / "published" / f"target-{index}.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"new-{index}", encoding="utf-8")
        target.write_text(f"old-{index}", encoding="utf-8")
        pairs.append((source, target))
    original_replace = runner.os.replace
    calls = 0

    def fail_second_backup(source: object, target: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated backup failure")
        original_replace(source, target)

    monkeypatch.setattr(runner.os, "replace", fail_second_backup)

    with pytest.raises(runner.MonitoringRunError, match="게시 실패"):
        runner._publish_files(pairs)

    assert [target.read_text(encoding="utf-8") for _source, target in pairs] == [
        f"old-{index}" for index in range(4)
    ]
    assert not list(tmp_path.rglob("*.fairpost-backup-*"))


@pytest.mark.parametrize("threshold", ["0", "-0.1", "1.1"])
def test_cli_rejects_invalid_threshold_before_writing(
    tmp_path: Path, threshold: str
) -> None:
    args = cli_args(tmp_path)
    write_jsonl(tmp_path / "run-input.jsonl", [posting()])
    args.extend(["--high-frequency-threshold", threshold])

    completed = run_cli(args)

    assert completed.returncode != 0
    assert "감사 설정 오류" in completed.stderr
    assert not (tmp_path / "run-snapshot").exists()
