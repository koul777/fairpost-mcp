from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import audit_private_fairness as fairness_audit  # noqa: E402
from tools import build_private_monitoring_snapshot as snapshot_builder  # noqa: E402


class MonitoringRunError(ValueError):
    """A user-facing failure from one stage of the offline monitoring run."""


def validate_run_paths(
    input_path: Path,
    output_dir: Path,
    snapshot_summary_path: Path,
    audit_output_path: Path,
    *,
    exclude_manifests: Sequence[Path] = (),
    baseline_path: Path | None = None,
) -> Path:
    """Validate every user path before any run input is opened.

    The snapshot builder owns the shared train-only path policy.  Applying that
    policy here to audit and baseline outputs too prevents an accidentally named
    evaluation partition from entering either side of this combined workflow.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    snapshot_summary_path = Path(snapshot_summary_path)
    audit_output_path = Path(audit_output_path)
    exclude_paths = tuple(Path(path) for path in exclude_manifests)
    baseline_path = Path(baseline_path) if baseline_path is not None else None

    user_paths: list[tuple[str, Path]] = [
        ("--input", input_path),
        ("--output-dir", output_dir),
        ("--snapshot-summary", snapshot_summary_path),
        ("--audit-output", audit_output_path),
    ]
    user_paths.extend(("--exclude-manifest", path) for path in exclude_paths)
    if baseline_path is not None:
        user_paths.append(("--baseline", baseline_path))
    for argument, path in user_paths:
        snapshot_builder.reject_non_train_path(path, argument=argument)

    snapshot_builder.validate_paths(
        input_path,
        output_dir,
        snapshot_summary_path,
        exclude_paths,
    )

    train_dir = (output_dir / "train").resolve(strict=False)
    records_path = train_dir / "records.jsonl"
    manifest_path = train_dir / "manifest.json"
    write_paths = {
        "snapshot records": records_path,
        "snapshot manifest": manifest_path,
        "--snapshot-summary": snapshot_summary_path.resolve(strict=False),
        "--audit-output": audit_output_path.resolve(strict=False),
    }
    write_keys: dict[str, str] = {}
    for label, path in write_paths.items():
        key = str(path).casefold()
        if key in write_keys:
            raise ValueError(f"출력 경로가 충돌합니다: {write_keys[key]}, {label}")
        if path.exists() and path.is_dir():
            raise ValueError(f"{label}은 파일 경로여야 합니다")
        write_keys[key] = label
    audit_resolved = audit_output_path.resolve(strict=False)
    if audit_resolved == train_dir or train_dir in audit_resolved.parents:
        raise ValueError("--audit-output은 output-dir/train 밖에 있어야 합니다")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("--output-dir은 디렉터리 경로여야 합니다")

    read_paths = [("--input", input_path), *(
        ("--exclude-manifest", path) for path in exclude_paths
    )]
    if baseline_path is not None:
        read_paths.append(("--baseline", baseline_path))
    output_dir_key = str(output_dir.resolve(strict=False)).casefold()
    for label, path in read_paths:
        key = str(path.resolve(strict=False)).casefold()
        if key in write_keys or key == output_dir_key:
            raise ValueError(f"{label}은 출력 경로와 달라야 합니다")
    # The audit tool additionally requires an explicit train component.
    fairness_audit._validate_train_path(records_path)
    return records_path


def _load_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MonitoringRunError(
            "기준선 읽기 실패: 파일을 읽거나 JSON으로 해석할 수 없습니다"
        ) from exc
    if not isinstance(value, dict):
        raise MonitoringRunError("기준선 읽기 실패: baseline은 JSON 객체여야 합니다")
    return value


def _write_audit(path: Path, report: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (OSError, UnicodeError) as exc:
        raise MonitoringRunError("감사 결과 쓰기 실패") from exc


def _publish_files(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Publish all files as one recoverable transaction.

    A filesystem cannot atomically rename several unrelated paths together.
    Existing targets are therefore moved to same-directory backups first.  If
    any publish rename fails, every target is restored to its exact pre-run
    state before the privacy-safe error is returned.
    """
    token = uuid4().hex
    backups: dict[Path, Path | None] = {}
    try:
        for _source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
        for _source, target in pairs:
            backup: Path | None = None
            if target.exists():
                backup = target.with_name(
                    f".{target.name}.fairpost-backup-{token}"
                )
                os.replace(target, backup)
            backups[target] = backup
        for source, target in pairs:
            os.replace(source, target)
    except OSError as exc:
        rollback_failed = False
        for _source, target in reversed(pairs):
            # A failure can happen while existing targets are still being
            # moved to backups.  Targets not yet recorded in ``backups`` were
            # never touched and must not be deleted during rollback.
            if target not in backups:
                continue
            backup = backups.get(target)
            try:
                if target.exists():
                    target.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, target)
            except OSError:
                rollback_failed = True
        message = "완료 산출물 게시 실패"
        if rollback_failed:
            message += ": 이전 상태 복원에도 실패했습니다"
        raise MonitoringRunError(message) from exc
    else:
        for backup in backups.values():
            if backup is not None:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    # All four new targets are already consistent.  A hidden
                    # stale backup is safer than reporting a failed run after
                    # the transaction has committed.
                    pass


def run_monitoring(
    input_path: Path,
    output_dir: Path,
    snapshot_summary_path: Path,
    audit_output_path: Path,
    *,
    exclude_manifests: Sequence[Path] = (),
    baseline_path: Path | None = None,
    high_frequency_threshold: float = fairness_audit.DEFAULT_HIGH_FREQUENCY_THRESHOLD,
) -> dict[str, int | bool | str]:
    """Build a de-identified train snapshot and audit exactly its records."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    snapshot_summary_path = Path(snapshot_summary_path)
    audit_output_path = Path(audit_output_path)
    exclude_paths = tuple(Path(path) for path in exclude_manifests)
    baseline_path = Path(baseline_path) if baseline_path is not None else None

    try:
        records_path = validate_run_paths(
            input_path,
            output_dir,
            snapshot_summary_path,
            audit_output_path,
            exclude_manifests=exclude_paths,
            baseline_path=baseline_path,
        )
    except (OSError, ValueError) as exc:
        raise MonitoringRunError(f"경로 사전 검사 실패: {exc}") from exc

    if not 0 < high_frequency_threshold <= 1:
        raise MonitoringRunError(
            "감사 설정 오류: --high-frequency-threshold는 0 초과 1 이하여야 합니다"
        )

    # Baseline is loaded only after every path has passed the preflight, and
    # before snapshot files are created, so malformed baseline JSON cannot leave
    # a partially completed run behind.
    baseline = _load_baseline(baseline_path)

    with TemporaryDirectory(prefix="fairpost-private-monitoring-") as temp_value:
        temp_root = Path(temp_value)
        staged_output_dir = temp_root / "snapshot"
        staged_summary_path = temp_root / "snapshot-summary.json"
        staged_audit_path = temp_root / "audit.json"
        staged_records_path = staged_output_dir / "train" / "records.jsonl"
        staged_manifest_path = staged_output_dir / "train" / "manifest.json"

        try:
            snapshot_summary = snapshot_builder.build_snapshot(
                input_path,
                staged_output_dir,
                staged_summary_path,
                exclude_manifests=exclude_paths,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise MonitoringRunError("스냅샷 생성 실패") from exc

        try:
            raw, records = fairness_audit.load_private_training_records(
                staged_records_path
            )
            fairness_audit.validate_snapshot_manifest(
                staged_manifest_path,
                raw,
                records,
            )
            report = fairness_audit.build_report(
                raw,
                records,
                high_frequency_threshold=high_frequency_threshold,
                baseline=baseline,
                manifest_verified=True,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise MonitoringRunError("공정성 감사 실패") from exc

        _write_audit(staged_audit_path, report)
        _publish_files(
            (
                (staged_records_path, records_path),
                (staged_manifest_path, output_dir / "train" / "manifest.json"),
                (staged_summary_path, snapshot_summary_path),
                (staged_audit_path, audit_output_path),
            )
        )

    # Deliberately omit paths, URLs, hashes, text, and every record/rule ID.
    # This is the complete stdout payload used by the CLI.
    return {
        "audit_records": int(report["input"]["records"]),
        "baseline_compared": baseline is not None,
        "records_with_law_findings": int(
            report["summary"]["records_with_law_findings"]
        ),
        "snapshot_records": int(snapshot_summary["counts"]["written"]),
        "status": "ok",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "공개·허용 민간 JSONL을 비식별 train-only 스냅샷으로 만든 뒤 "
            "그 스냅샷을 익명 공정성 감사까지 오프라인으로 실행합니다."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--snapshot-summary", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument(
        "--exclude-manifest",
        type=Path,
        action="append",
        default=[],
        help="제외할 content_hashes를 가진 manifest (반복 가능)",
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--high-frequency-threshold",
        type=float,
        default=fairness_audit.DEFAULT_HIGH_FREQUENCY_THRESHOLD,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = run_monitoring(
            args.input,
            args.output_dir,
            args.snapshot_summary,
            args.audit_output,
            exclude_manifests=args.exclude_manifest,
            baseline_path=args.baseline,
            high_frequency_threshold=args.high_frequency_threshold,
        )
    except MonitoringRunError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
