from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), name="KST")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402


LABELER_PAYLOAD_RE = re.compile(
    r'<script id="payload" type="application/octet-stream">([^<]+)</script>'
)


def _file_info(payload: bytes) -> dict[str, object]:
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def validate_paths(
    records_path: Path,
    manifest_path: Path,
    labeler_path: Path,
    annotations_path: Path,
    output_path: Path,
) -> None:
    inputs = [
        ("--records", records_path),
        ("--manifest", manifest_path),
        ("--labeler", labeler_path),
        ("--annotations", annotations_path),
    ]
    for name, path in inputs:
        if _path_key(path) == _path_key(output_path) or _same_existing_file(
            path, output_path
        ):
            raise ValueError(f"--output은 {name}와 다른 파일이어야 합니다")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("--output은 파일 경로여야 합니다")


def _load_manifest(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("홀드아웃 manifest가 유효한 UTF-8 JSON이 아닙니다") from exc
    if not isinstance(value, dict):
        raise ValueError("홀드아웃 manifest는 JSON 객체여야 합니다")
    if set(value) != {"content_hashes", "count", "ids"}:
        raise ValueError("홀드아웃 manifest 필드가 올바르지 않습니다")
    count = value["count"]
    ids = value["ids"]
    content_hashes = value["content_hashes"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("홀드아웃 manifest count가 올바르지 않습니다")
    for field, items in (("ids", ids), ("content_hashes", content_hashes)):
        if (
            not isinstance(items, list)
            or len(items) != count
            or any(not isinstance(item, str) or not item for item in items)
            or len(set(items)) != count
        ):
            raise ValueError(f"홀드아웃 manifest {field}가 올바르지 않습니다")
    return value


def _verify_records(payload: bytes, manifest: dict[str, Any]) -> int:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("홀드아웃 records가 유효한 UTF-8이 아닙니다") from exc
    ids: list[str] = []
    content_hashes: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"홀드아웃 records {line_number}행이 유효한 JSON이 아닙니다"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"홀드아웃 records {line_number}행은 객체여야 합니다")
        record_id = row.get("id")
        content_hash = row.get("content_hash")
        record_text = row.get("text")
        if (
            not isinstance(record_id, str)
            or not record_id
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or not isinstance(record_text, str)
            or hashlib.sha256(record_text.encode("utf-8")).hexdigest()
            != content_hash
        ):
            raise ValueError(
                f"홀드아웃 records {line_number}행의 ID·본문 해시가 올바르지 않습니다"
            )
        ids.append(record_id)
        content_hashes.append(content_hash)
    if len(ids) != manifest["count"]:
        raise ValueError("홀드아웃 manifest count와 records 건수가 다릅니다")
    if len(set(ids)) != len(ids) or ids != manifest["ids"]:
        raise ValueError("홀드아웃 manifest IDs와 records가 일치하지 않습니다")
    if (
        len(set(content_hashes)) != len(content_hashes)
        or set(content_hashes) != set(manifest["content_hashes"])
    ):
        raise ValueError("홀드아웃 manifest 본문 해시와 records가 일치하지 않습니다")
    return len(ids)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(payload)
            staged_path = Path(stream.name)
        os.replace(staged_path, path)
    finally:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)


def _load_labeler_payload(labeler: str) -> dict[str, Any]:
    matched = LABELER_PAYLOAD_RE.search(labeler)
    if matched is None:
        raise ValueError(
            "라벨링 화면에 버전 결합 payload가 없습니다. "
            "build_annotation_ui.py로 다시 생성하세요"
        )
    try:
        decoded = base64.b64decode(matched.group(1), validate=True).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("라벨링 화면의 버전 결합 payload가 올바르지 않습니다") from exc
    if not isinstance(payload, dict):
        raise ValueError("라벨링 화면의 버전 결합 payload는 객체여야 합니다")
    return payload


def _verify_labeler_binding(
    labeler: str,
    manifest: dict[str, Any],
    ruleset: Any,
) -> dict[str, str]:
    payload = _load_labeler_payload(labeler)
    embedded_ruleset = payload.get("ruleset_version")
    embedded_matching = payload.get("matching_version")
    if (
        embedded_ruleset != ruleset.version
        or embedded_matching != ruleset.matching_version
    ):
        raise ValueError(
            "라벨링 화면이 현재 규칙 또는 매칭 버전보다 오래되었습니다. "
            "build_annotation_ui.py로 다시 생성하세요"
        )
    if payload.get("evaluation_phase") != "sealed_holdout_final":
        raise ValueError("라벨링 화면의 평가 단계가 sealed holdout final이 아닙니다")
    metric_scope = payload.get("metric_scope")
    if not isinstance(metric_scope, dict) or (
        metric_scope.get("question_cards") != "pilot_only_not_g1_g2"
    ):
        raise ValueError("라벨링 화면의 G1/G2 측정 범위가 올바르지 않습니다")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("라벨링 화면의 holdout records 결합 정보가 없습니다")
    ids: list[str] = []
    hashes: list[str] = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("라벨링 화면의 holdout records 결합 정보가 올바르지 않습니다")
        record_id = row.get("id")
        content_hash = row.get("content_hash")
        if not isinstance(record_id, str) or not isinstance(content_hash, str):
            raise ValueError("라벨링 화면의 holdout records 결합 정보가 올바르지 않습니다")
        ids.append(record_id)
        hashes.append(content_hash)
    if ids != manifest["ids"] or set(hashes) != set(manifest["content_hashes"]):
        raise ValueError(
            "라벨링 화면이 현재 holdout manifest와 다릅니다. "
            "build_annotation_ui.py로 다시 생성하세요"
        )
    return {
        "ruleset_version": str(embedded_ruleset),
        "matching_version": str(embedded_matching),
        "status": "current",
    }


def build_report(
    records_path: Path,
    manifest_path: Path,
    labeler_path: Path,
    annotations_path: Path,
) -> dict[str, object]:
    records_payload = records_path.read_bytes()
    manifest_payload = manifest_path.read_bytes()
    labeler_payload = labeler_path.read_bytes()
    manifest = _load_manifest(manifest_payload)
    count = _verify_records(records_payload, manifest)
    try:
        labeler = labeler_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("라벨링 화면이 유효한 UTF-8이 아닙니다") from exc
    required_csp = (
        "default-src 'none'",
        "connect-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    )
    if any(directive not in labeler for directive in required_csp):
        raise ValueError("라벨링 화면의 오프라인 보안 정책이 불완전합니다")

    ruleset = load_ruleset(ROOT / "data")
    labeler_binding = _verify_labeler_binding(labeler, manifest, ruleset)
    records_info = _file_info(records_payload)
    manifest_info = _file_info(manifest_payload)
    labeler_info = _file_info(labeler_payload)
    relative_records = records_path.as_posix()
    relative_manifest = manifest_path.as_posix()
    relative_labeler = labeler_path.as_posix()
    relative_annotations = annotations_path.as_posix()
    relative_attestation = annotations_path.with_name(
        "human-attestation.json"
    ).as_posix()
    relative_train_manifest = (
        records_path.parent.parent / "train" / "manifest.json"
    ).as_posix()
    return {
        "schema_version": "fairpost-human-labeling-handoff-v1",
        "prepared_at": datetime.now(KST).isoformat(
            timespec="seconds"
        ),
        "status": "awaiting_human_annotations",
        "evaluation_phase": "sealed_holdout_final",
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "labeler_binding": labeler_binding,
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
        "expected_human_attestation_path": relative_attestation,
        "evaluation_command": (
            f"python tools/evaluate.py {relative_annotations} "
            f"--train-manifest {relative_train_manifest} "
            f"--holdout-manifest {relative_manifest} "
            f"--holdout-records {relative_records} "
            f"--phase final --human-attestation {relative_attestation} "
            "--enforce-targets --output reports/evaluation.json"
        ),
        "human_attestation_requirements": {
            "schema_version": 2,
            "attestation": "human_gold",
            "prediction_blinded": True,
            "ai_generated_labels": False,
            "reviewer_ids": "one_or_more_pseudonymous_human_reviewer_ids",
            "annotations_sha256": "sha256_of_completed_annotations_jsonl",
            "holdout_manifest_sha256": manifest_info["sha256"],
            "holdout_records_sha256": records_info["sha256"],
            "ruleset_version": ruleset.version,
            "matching_version": ruleset.matching_version,
            "attested_at": "ISO-8601 timestamp",
        },
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
            f"G1/G2 성능 목표는 사람이 {count}건 전체를 라벨링하고 평가 명령이 "
            "통과하기 전까지 미충족입니다."
        ),
        "question_card_metrics": (
            "질문 카드는 G1/G2 대상이 아니며 사용자 파일럿에서 관련성·이해도·"
            "실행 가능성을 별도로 측정합니다."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="홀드아웃 사람 라벨링 파일의 해시와 평가 게이트를 기록합니다."
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
        validate_paths(
            args.records,
            args.manifest,
            args.labeler,
            args.annotations,
            args.output,
        )
        report = build_report(
            args.records,
            args.manifest,
            args.labeler,
            args.annotations,
        )
        _atomic_write_text(
            args.output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"사람 라벨링 인계 보고서 생성: {report['holdout_records']}건 "
        f"{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
