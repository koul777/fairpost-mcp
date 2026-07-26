from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402


def _file_info(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def build_report(
    records_path: Path,
    manifest_path: Path,
    labeler_path: Path,
    annotations_path: Path,
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    count = sum(
        1
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if manifest.get("count") != count:
        raise ValueError("홀드아웃 manifest count와 records 건수가 다릅니다")
    labeler = labeler_path.read_text(encoding="utf-8")
    if "connect-src 'none'" not in labeler:
        raise ValueError("라벨러가 네트워크 요청을 차단하지 않습니다")

    ruleset = load_ruleset(ROOT / "data")
    records_info = _file_info(records_path)
    manifest_info = _file_info(manifest_path)
    labeler_info = _file_info(labeler_path)
    relative_records = records_path.as_posix()
    relative_manifest = manifest_path.as_posix()
    relative_labeler = labeler_path.as_posix()
    relative_annotations = annotations_path.as_posix()
    relative_train_manifest = (
        records_path.parent.parent / "train" / "manifest.json"
    ).as_posix()
    return {
        "prepared_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
            timespec="seconds"
        ),
        "status": "awaiting_human_annotations",
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "holdout_records": count,
        "holdout_source": {
            "records_path": relative_records,
            "records_bytes": records_info["bytes"],
            "records_sha256": records_info["sha256"],
            "manifest_path": relative_manifest,
            "manifest_bytes": manifest_info["bytes"],
            "manifest_sha256": manifest_info["sha256"],
        },
        "labeler": {
            "path": relative_labeler,
            "bytes": labeler_info["bytes"],
            "sha256": labeler_info["sha256"],
            "network_requests": "blocked",
        },
        "expected_annotation_path": relative_annotations,
        "evaluation_command": (
            f"python tools/evaluate.py {relative_annotations} "
            f"--train-manifest {relative_train_manifest} "
            f"--holdout-manifest {relative_manifest} "
            f"--holdout-records {relative_records} "
            "--enforce-targets --output reports/evaluation.json"
        ),
        "evaluation_gate": {
            "minimum_holdout_by_sector": {"public": 90, "private": 90},
            "expression_precision": 0.9,
            "absence_recall": 0.85,
            "absence_precision": 0.8,
            "undefined_metric_passes": False,
            "measurement_units": {
                "expression_detection": "posting_rule_pair",
                "absence_detection": "posting_slot_pair",
                "dictionary_coverage": "human_labeled_expression_occurrence",
            },
        },
        "release_claim": (
            f"G1/G2 성능 목표는 사람이 {count}건 전체를 라벨링하고 "
            "평가 명령이 통과하기 전까지 미측정입니다."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="홀드아웃 라벨링 인계 파일의 해시와 평가 게이트를 기록합니다."
    )
    parser.add_argument(
        "--records",
        type=Path,
        default=Path(".corpus-prd/holdout/records.jsonl"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(".corpus-prd/holdout/manifest.json"),
    )
    parser.add_argument(
        "--labeler",
        type=Path,
        default=Path(".corpus-prd/holdout/labeler.html"),
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path(".corpus-prd/holdout/annotations.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/human_labeling_handoff.json"),
    )
    args = parser.parse_args()
    try:
        report = build_report(
            args.records,
            args.manifest,
            args.labeler,
            args.annotations,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"사람 라벨링 인계 보고서 생성: {report['holdout_records']}건, "
        f"{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
