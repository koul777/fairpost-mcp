from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


HASH_RE = re.compile(r"[0-9a-f]{64}")
TARGETS = {
    "expression_precision": 0.90,
    "absence_recall": 0.85,
    "absence_precision": 0.80,
}
MIN_HOLDOUT_BY_SECTOR = {"public": 90, "private": 90}
FINAL_RECEIPT_NAME = "final-evaluation-receipt.json"
ATTESTATION_SCHEMA_VERSION = 2
EVALUATION_SCHEMA_VERSION = 3
EVALUATOR_SOURCE_FILES = (
    "tools/evaluate.py",
    "core/__init__.py",
    "core/engine.py",
    "core/extractor.py",
    "core/loader.py",
    "core/morph.py",
    "core/schema.py",
)


def evaluator_fingerprint() -> str:
    primary = Path(__file__).resolve()
    canonical_primary = (ROOT / "tools" / "evaluate.py").resolve()
    paths = (
        [(relative, ROOT / relative) for relative in EVALUATOR_SOURCE_FILES]
        if primary == canonical_primary
        else [(primary.name, primary)]
    )
    digest = hashlib.sha256(b"fairpost-evaluator-v2\0")
    for relative, path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        canonical = ast.dump(
            tree,
            annotate_fields=True,
            include_attributes=False,
        ).encode("utf-8")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(canonical).to_bytes(8, byteorder="big", signed=False))
        digest.update(canonical)
        digest.update(b"\0")
    try:
        pyyaml_version = package_version("PyYAML")
    except PackageNotFoundError:
        pyyaml_version = "missing"
    environment = json.dumps(
        {
            "python_cache_tag": sys.implementation.cache_tag,
            "python_version": list(sys.version_info[:3]),
            "pyyaml_version": pyyaml_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest.update(environment)
    return f"evaluator-{digest.hexdigest()}"


def _decode_utf8(path: Path, payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: UTF-8 파일이어야 합니다") from exc


def load_jsonl(path: Path, payload: bytes | None = None) -> list[dict]:
    records = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for line_number, line in enumerate(
        _decode_utf8(path, path.read_bytes() if payload is None else payload).splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        record_id = value.get("id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"{path}:{line_number}: id가 필요합니다")
        if record_id in seen_ids:
            raise ValueError(f"{path}:{line_number}: 중복 id '{record_id}'")
        seen_ids.add(record_id)
        if not isinstance(value.get("text"), str):
            raise ValueError(f"{path}:{line_number}: text가 필요합니다")
        content_hash = value.get("content_hash")
        if not isinstance(content_hash, str) or not HASH_RE.fullmatch(content_hash):
            raise ValueError(
                f"{path}:{line_number}: content_hash는 SHA-256 64자리여야 합니다"
            )
        actual_hash = hashlib.sha256(value["text"].encode("utf-8")).hexdigest()
        if content_hash != actual_hash:
            raise ValueError(f"{path}:{line_number}: text와 content_hash가 일치하지 않습니다")
        if content_hash in seen_hashes:
            raise ValueError(f"{path}:{line_number}: 중복 content_hash")
        seen_hashes.add(content_hash)
        if not isinstance(value.get("expected_findings"), list):
            raise ValueError(f"{path}:{line_number}: expected_findings 목록이 필요합니다")
        if not isinstance(value.get("expected_absent_slots"), list):
            raise ValueError(
                f"{path}:{line_number}: expected_absent_slots 목록이 필요합니다"
            )
        if not all(isinstance(item, str) for item in value["expected_findings"]):
            raise ValueError(
                f"{path}:{line_number}: expected_findings 값은 문자열이어야 합니다"
            )
        if not all(isinstance(item, str) for item in value["expected_absent_slots"]):
            raise ValueError(
                f"{path}:{line_number}: expected_absent_slots 값은 문자열이어야 합니다"
            )
        expressions = value.get("expected_expressions")
        if not isinstance(expressions, list):
            raise ValueError(f"{path}:{line_number}: expected_expressions는 목록이어야 합니다")
        expression_rule_ids: list[str] = []
        for expression in expressions:
            if not isinstance(expression, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected_expressions 항목은 객체여야 합니다"
                )
            if expression.get("rule_id") is not None and not isinstance(
                expression.get("rule_id"), str
            ):
                raise ValueError(
                    f"{path}:{line_number}: expected_expressions.rule_id 형식 오류"
                )
            if expression.get("rule_id") is not None:
                expression_rule_ids.append(str(expression["rule_id"]))
        expected_findings = list(map(str, value["expected_findings"]))
        expected_absent_slots = list(map(str, value["expected_absent_slots"]))
        if len(expected_findings) != len(set(expected_findings)):
            raise ValueError(f"{path}:{line_number}: expected_findings 중복")
        if len(expected_absent_slots) != len(set(expected_absent_slots)):
            raise ValueError(f"{path}:{line_number}: expected_absent_slots 중복")
        if set(expected_findings) != set(expression_rule_ids):
            raise ValueError(
                f"{path}:{line_number}: expected_findings와 "
                "expected_expressions.rule_id가 일치해야 합니다"
            )
        records.append(value)
    return records


def load_hashes(path: Path, payload: bytes | None = None) -> set[str]:
    value = json.loads(
        _decode_utf8(path, path.read_bytes() if payload is None else payload)
    )
    values = value.get("content_hashes")
    if not isinstance(values, list) or not all(
        isinstance(value, str) and HASH_RE.fullmatch(value) for value in values
    ):
        raise ValueError(f"{path}: content_hashes SHA-256 목록이 필요합니다")
    if len(values) != len(set(values)):
        raise ValueError(f"{path}: content_hashes가 중복되었습니다")
    return set(values)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def validate_output_paths(
    *,
    output: Path,
    receipt: Path | None,
    inputs: dict[str, Path | None],
) -> None:
    for name, path in inputs.items():
        if path is not None and _paths_alias(output, path):
            raise ValueError(f"--output은 {name}과 다른 파일이어야 합니다")
        if receipt is not None and path is not None and _paths_alias(receipt, path):
            raise ValueError(f"--evaluation-receipt는 {name}과 다른 파일이어야 합니다")
    if receipt is not None and _paths_alias(output, receipt):
        raise ValueError("--evaluation-receipt와 --output은 다른 파일이어야 합니다")
    for name, path in (("--output", output), ("--evaluation-receipt", receipt)):
        if path is not None and path.exists() and path.is_dir():
            raise ValueError(f"{name}은 파일 경로여야 합니다")


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _dataset_fingerprint(manifest_payload: bytes, records_payload: bytes) -> str:
    digest = hashlib.sha256()
    for label, payload in (
        (b"manifest\0", manifest_payload),
        (b"records\0", records_payload),
    ):
        digest.update(label)
        digest.update(payload)
    return digest.hexdigest()


def load_human_attestation(
    path: Path,
    *,
    annotations_sha256: str,
    holdout_manifest_sha256: str,
    holdout_records_sha256: str,
    ruleset_version: str,
    matching_version: str,
    payload: bytes | None = None,
) -> dict[str, Any]:
    try:
        value = json.loads(
            _decode_utf8(path, path.read_bytes() if payload is None else payload)
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("사람 라벨 확인서가 유효한 UTF-8 JSON이 아닙니다") from exc
    if not isinstance(value, dict):
        raise ValueError("사람 라벨 확인서는 JSON 객체여야 합니다")
    required = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "attestation": "human_gold",
        "prediction_blinded": True,
        "ai_generated_labels": False,
        "annotations_sha256": annotations_sha256,
        "holdout_manifest_sha256": holdout_manifest_sha256,
        "holdout_records_sha256": holdout_records_sha256,
        "ruleset_version": ruleset_version,
        "matching_version": matching_version,
    }
    mismatched = [key for key, expected in required.items() if value.get(key) != expected]
    if mismatched:
        raise ValueError(
            "사람 라벨 확인서가 현재 봉인 입력과 일치하지 않습니다: "
            + ", ".join(mismatched)
        )
    reviewer_ids = value.get("reviewer_ids")
    if (
        not isinstance(reviewer_ids, list)
        or not reviewer_ids
        or any(not isinstance(item, str) or not item.strip() for item in reviewer_ids)
    ):
        raise ValueError("사람 라벨 확인서에는 비어 있지 않은 reviewer_ids가 필요합니다")
    if not isinstance(value.get("attested_at"), str) or not value["attested_at"].strip():
        raise ValueError("사람 라벨 확인서에는 attested_at이 필요합니다")
    return value


def _receipt_binding(
    *,
    dataset_fingerprint: str,
    annotations_sha256: str,
    attestation_sha256: str | None,
    ruleset_version: str,
    matching_version: str,
    evaluator_source_fingerprint: str,
    complete_holdout: bool,
) -> dict[str, Any]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluation_phase": "sealed_holdout_final",
        "dataset_fingerprint": dataset_fingerprint,
        "annotations_sha256": annotations_sha256,
        "human_attestation_sha256": attestation_sha256,
        "ruleset_version": ruleset_version,
        "matching_version": matching_version,
        "evaluator_source_fingerprint": evaluator_source_fingerprint,
        "complete_holdout": complete_holdout,
        "release_claim_eligible": complete_holdout and attestation_sha256 is not None,
    }


def _load_receipt(receipt_path: Path) -> dict[str, Any]:
    try:
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "최종 평가 영수증이 손상되었습니다. 새 독립 홀드아웃이 필요합니다"
        ) from exc
    if not isinstance(existing, dict):
        raise ValueError(
            "최종 평가 영수증이 손상되었습니다. 새 독립 홀드아웃이 필요합니다"
        )
    return existing


def _verify_receipt_binding(
    existing: dict[str, Any], binding: dict[str, Any]
) -> None:
    if any(existing.get(key) != value for key, value in binding.items()):
        raise ValueError(
            "이 봉인 홀드아웃은 다른 라벨·규칙·매칭 버전으로 이미 결과가 "
            "공개되었습니다. 같은 세트로 재튜닝 성능을 주장할 수 없으므로 "
            "새 독립 홀드아웃을 사용하세요"
        )


def prepare_final_evaluation(
    receipt_path: Path,
    binding: dict[str, Any],
) -> str:
    """Reserve the first result reveal before scoring without claiming completion."""
    if receipt_path.exists():
        existing = _load_receipt(receipt_path)
        _verify_receipt_binding(existing, binding)
        return "reproduced" if existing.get("state", "finalized") == "finalized" else "pending_recovery"

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        **binding,
        "state": "pending",
        "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "평가가 시작되었지만 보고서와 완료 영수증은 아직 원자적으로 "
            "확정되지 않았습니다. 같은 결합 입력으로만 복구할 수 있습니다."
        ),
    }
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with receipt_path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return prepare_final_evaluation(receipt_path, binding)
    return "prepared"


def finalize_final_evaluation(
    receipt_path: Path,
    binding: dict[str, Any],
    *,
    report_sha256: str,
) -> str:
    existing = _load_receipt(receipt_path)
    _verify_receipt_binding(existing, binding)
    if existing.get("state", "finalized") == "finalized":
        existing_hash = existing.get("report_sha256")
        if existing_hash is not None and existing_hash != report_sha256:
            raise ValueError("재현 평가 보고서 해시가 최초 완료 영수증과 다릅니다")
        return "reproduced"
    receipt = {
        **binding,
        "state": "finalized",
        "prepared_at": existing.get("prepared_at"),
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report_sha256": report_sha256,
        "note": (
            "결과 공개 후에는 동일 입력·라벨·규칙 버전의 재현 실행만 허용됩니다. "
            "튜닝 후 성능 주장은 새 독립 홀드아웃이 필요합니다."
        ),
    }
    _atomic_write_text(
        receipt_path,
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return "registered"


def register_final_evaluation(
    receipt_path: Path,
    binding: dict[str, Any],
) -> str:
    """Compatibility helper for direct registration tests and older callers."""
    status = prepare_final_evaluation(receipt_path, binding)
    if status == "reproduced":
        return status
    return finalize_final_evaluation(
        receipt_path,
        binding,
        report_sha256="compatibility-registration",
    )


def load_holdout_records(
    path: Path,
    expected_hashes: set[str],
    payload: bytes | None = None,
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    seen_ids: set[str] = set()
    for line_number, line in enumerate(
        _decode_utf8(path, path.read_bytes() if payload is None else payload).splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        record_id = value.get("id")
        content_hash = value.get("content_hash")
        text = value.get("text")
        sector = value.get("sector")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"{path}:{line_number}: id가 필요합니다")
        if record_id in seen_ids:
            raise ValueError(f"{path}:{line_number}: 중복 id '{record_id}'")
        seen_ids.add(record_id)
        if not isinstance(content_hash, str) or not HASH_RE.fullmatch(content_hash):
            raise ValueError(
                f"{path}:{line_number}: content_hash는 SHA-256 64자리여야 합니다"
            )
        if content_hash in records:
            raise ValueError(f"{path}:{line_number}: 중복 content_hash")
        if not isinstance(text, str):
            raise ValueError(f"{path}:{line_number}: text가 필요합니다")
        actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash != actual_hash:
            raise ValueError(f"{path}:{line_number}: text와 content_hash가 일치하지 않습니다")
        if sector not in MIN_HOLDOUT_BY_SECTOR:
            raise ValueError(
                f"{path}:{line_number}: sector는 public 또는 private이어야 합니다"
            )
        records[content_hash] = {
            "id": record_id,
            "sector": str(sector),
            "source": str(value.get("source") or record_id.partition(":")[0]),
        }
    actual_hashes = set(records)
    missing = expected_hashes - actual_hashes
    extra = actual_hashes - expected_hashes
    if missing or extra:
        raise ValueError(
            f"{path}: manifest와 records 해시가 일치하지 않습니다 "
            f"(누락 {len(missing)}, 초과 {len(extra)})"
        )
    return records


def metrics(tp: int, fp: int, fn: int) -> dict[str, float | int | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 6) if precision is not None else None,
        "recall": round(recall, 6) if recall is not None else None,
    }


def _target_gate(
    report: dict[str, object],
    sector_counts: Counter[str],
) -> dict[str, object]:
    expression = report["expression_detection"]
    absence = report["absence_detection"]
    assert isinstance(expression, dict) and isinstance(absence, dict)
    checks = {
        "complete_holdout": bool(report["complete_holdout"]),
        "public_holdout_at_least_90": (
            sector_counts["public"] >= MIN_HOLDOUT_BY_SECTOR["public"]
        ),
        "private_holdout_at_least_90": (
            sector_counts["private"] >= MIN_HOLDOUT_BY_SECTOR["private"]
        ),
        "expression_precision_defined": expression["precision"] is not None,
        "expression_precision_at_least_0_90": (
            expression["precision"] is not None
            and float(expression["precision"]) >= TARGETS["expression_precision"]
        ),
        "absence_recall_defined": absence["recall"] is not None,
        "absence_recall_at_least_0_85": (
            absence["recall"] is not None
            and float(absence["recall"]) >= TARGETS["absence_recall"]
        ),
        "absence_precision_defined": absence["precision"] is not None,
        "absence_precision_at_least_0_80": (
            absence["precision"] is not None
            and float(absence["precision"]) >= TARGETS["absence_precision"]
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": TARGETS,
        "minimum_holdout_by_sector": MIN_HOLDOUT_BY_SECTOR,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "train calibration 또는 사람이 라벨링한 sealed holdout final 평가를 "
            "명시적으로 실행합니다."
        )
    )
    parser.add_argument("annotations", type=Path)
    parser.add_argument(
        "--phase",
        choices=("calibration", "final"),
        default="final",
        help="기본값 final은 기존 CLI와 호환되며 최초 결과 공개 영수증을 남깁니다.",
    )
    parser.add_argument(
        "--train-manifest",
        type=Path,
        default=Path(".corpus-prd/train/manifest.json"),
    )
    parser.add_argument(
        "--holdout-manifest",
        type=Path,
        default=Path(".corpus-prd/holdout/manifest.json"),
    )
    parser.add_argument(
        "--holdout-records",
        type=Path,
        help="생략하면 --holdout-manifest와 같은 폴더의 records.jsonl을 사용합니다.",
    )
    parser.add_argument(
        "--calibration-records",
        type=Path,
        help="calibration 단계의 train records. 생략하면 train manifest 옆 파일입니다.",
    )
    parser.add_argument(
        "--human-attestation",
        type=Path,
        help="final gold가 사람의 독립 라벨임을 입력 해시에 결합한 JSON 확인서입니다.",
    )
    parser.add_argument(
        "--evaluation-receipt",
        type=Path,
        help=(
            "최초 final 결과 공개 영수증. 생략하면 holdout manifest 옆 "
            f"{FINAL_RECEIPT_NAME}을 사용합니다."
        ),
    )
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    parser.add_argument("--enforce-targets", action="store_true")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "일부 데이터만 측정합니다. final에서 사용하면 홀드아웃이 공개된 것으로 "
            "등록되어 이후 성능 주장에 사용할 수 없습니다."
        ),
    )
    args = parser.parse_args()
    if args.enforce_targets and args.phase != "final":
        raise SystemExit("--enforce-targets는 --phase final에서만 사용할 수 있습니다")
    if args.enforce_targets and args.allow_partial:
        raise SystemExit("--enforce-targets와 --allow-partial은 함께 사용할 수 없습니다")
    if args.phase == "calibration" and args.human_attestation is not None:
        raise SystemExit("--human-attestation은 --phase final에서만 사용합니다")

    final_records_path = (
        args.holdout_records or args.holdout_manifest.with_name("records.jsonl")
    )
    calibration_records_path = (
        args.calibration_records or args.train_manifest.with_name("records.jsonl")
    )
    evaluation_records_path = (
        calibration_records_path if args.phase == "calibration" else final_records_path
    )
    receipt_path = (
        args.evaluation_receipt
        or args.holdout_manifest.with_name(FINAL_RECEIPT_NAME)
        if args.phase == "final"
        else None
    )
    evaluator_source_fingerprint = evaluator_fingerprint()

    try:
        validate_output_paths(
            output=args.output,
            receipt=receipt_path,
            inputs={
                "annotations": args.annotations,
                "--train-manifest": args.train_manifest,
                "--holdout-manifest": args.holdout_manifest,
                "--holdout-records": final_records_path,
                "--calibration-records": calibration_records_path,
                "--human-attestation": args.human_attestation,
            },
        )
        # Capture every evaluated input once. Parsing, hashing, attestation checks,
        # and receipt binding all reuse these exact bytes so a concurrent file
        # replacement cannot bind the report to data that was not evaluated.
        train_manifest_payload = args.train_manifest.read_bytes()
        holdout_manifest_payload = args.holdout_manifest.read_bytes()
        evaluation_records_payload = evaluation_records_path.read_bytes()
        annotations_payload = args.annotations.read_bytes()
        attestation_payload = (
            args.human_attestation.read_bytes()
            if args.human_attestation is not None
            else None
        )
        train_hashes = load_hashes(args.train_manifest, train_manifest_payload)
        holdout_hashes = load_hashes(args.holdout_manifest, holdout_manifest_payload)
        overlap = train_hashes & holdout_hashes
        if overlap:
            raise ValueError(f"훈련/홀드아웃 해시가 {len(overlap)}개 겹칩니다")
        evaluation_hashes = (
            train_hashes if args.phase == "calibration" else holdout_hashes
        )
        evaluation_records = load_holdout_records(
            evaluation_records_path,
            evaluation_hashes,
            evaluation_records_payload,
        )
        records = load_jsonl(args.annotations, annotations_payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    annotation_hashes = {str(record["content_hash"]) for record in records}
    if not annotation_hashes <= evaluation_hashes:
        raise SystemExit(
            f"라벨 데이터에 {args.phase} manifest 밖의 content_hash가 있습니다"
        )
    missing_annotations = evaluation_hashes - annotation_hashes
    partial_allowed = args.allow_partial or args.phase == "calibration"
    if missing_annotations and not partial_allowed:
        raise SystemExit(
            "홀드아웃 전체 라벨이 필요합니다: "
            f"{len(missing_annotations)}건의 content_hash가 누락되었습니다"
        )

    engine = FairpostEngine()
    all_finding_rules = {
        rule["id"] for rule in engine.ruleset.rules if rule["layer"] == "law"
    }
    all_slot_ids = set(engine.ruleset.slots)
    for record in records:
        metadata = evaluation_records[str(record["content_hash"])]
        if record["id"] != metadata["id"]:
            raise SystemExit(
                f"{record['id']}: content_hash에 연결된 {args.phase} id "
                f"'{metadata['id']}'와 일치하지 않습니다"
            )
        unknown_findings = set(map(str, record["expected_findings"])) - all_finding_rules
        if unknown_findings:
            raise SystemExit(
                f"{record['id']}: 알 수 없는 expected_findings: "
                f"{', '.join(sorted(unknown_findings))}"
            )
        unknown_slots = set(map(str, record["expected_absent_slots"])) - all_slot_ids
        if unknown_slots:
            raise SystemExit(
                f"{record['id']}: 알 수 없는 expected_absent_slots: "
                f"{', '.join(sorted(unknown_slots))}"
            )
        for expression in record.get("expected_expressions", []):
            rule_id = expression.get("rule_id")
            if rule_id is not None and rule_id not in all_finding_rules:
                raise SystemExit(
                    f"{record['id']}: 알 수 없는 expected_expressions.rule_id '{rule_id}'"
                )

    annotations_sha256 = hashlib.sha256(annotations_payload).hexdigest()
    manifest_payload = (
        train_manifest_payload
        if args.phase == "calibration"
        else holdout_manifest_payload
    )
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    records_sha256 = hashlib.sha256(evaluation_records_payload).hexdigest()
    attestation_sha256: str | None = None
    attestation_verified = False
    if args.human_attestation is not None:
        try:
            load_human_attestation(
                args.human_attestation,
                annotations_sha256=annotations_sha256,
                holdout_manifest_sha256=manifest_sha256,
                holdout_records_sha256=records_sha256,
                ruleset_version=engine.ruleset.version,
                matching_version=engine.ruleset.matching_version,
                payload=attestation_payload,
            )
            assert attestation_payload is not None
            attestation_sha256 = hashlib.sha256(attestation_payload).hexdigest()
            attestation_verified = True
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if args.enforce_targets and not attestation_verified:
        raise SystemExit(
            "최종 G1/G2 성능 주장에는 --human-attestation으로 검증된 사람 gold "
            "확인서가 필요합니다"
        )

    receipt_preparation_status: str | None = None
    release_claim_eligible = False
    receipt_binding: dict[str, Any] | None = None
    if args.phase == "final":
        assert receipt_path is not None
        receipt_binding = _receipt_binding(
            dataset_fingerprint=_dataset_fingerprint(
                holdout_manifest_payload, evaluation_records_payload
            ),
            annotations_sha256=annotations_sha256,
            attestation_sha256=attestation_sha256,
            ruleset_version=engine.ruleset.version,
            matching_version=engine.ruleset.matching_version,
            evaluator_source_fingerprint=evaluator_source_fingerprint,
            complete_holdout=annotation_hashes == holdout_hashes,
        )
        try:
            prepared = prepare_final_evaluation(receipt_path, receipt_binding)
        except (OSError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        receipt_preparation_status = prepared
        release_claim_eligible = bool(receipt_binding["release_claim_eligible"])

    finding_tp = finding_fp = finding_fn = 0
    absence_tp = absence_fp = absence_fn = 0
    expression_total = expression_covered = 0
    sector_counters: dict[str, dict[str, int]] = {
        sector: {
            "finding_tp": 0,
            "finding_fp": 0,
            "finding_fn": 0,
            "absence_tp": 0,
            "absence_fp": 0,
            "absence_fn": 0,
        }
        for sector in MIN_HOLDOUT_BY_SECTOR
    }
    details = []
    for record in records:
        result = engine.check(record["text"])
        predicted_findings = {item.id for item in result.findings}
        expected_findings = set(map(str, record["expected_findings"]))
        predicted_absence = {item.slot for item in result.slots if not item.found}
        expected_absence = set(map(str, record["expected_absent_slots"]))
        expected_expressions = record.get("expected_expressions", [])
        expression_total += len(expected_expressions)
        expression_covered += sum(
            expression.get("rule_id") in all_finding_rules
            for expression in expected_expressions
        )

        finding_tp += len(predicted_findings & expected_findings)
        finding_fp += len(predicted_findings - expected_findings)
        finding_fn += len(expected_findings - predicted_findings)
        absence_tp += len(predicted_absence & expected_absence)
        absence_fp += len(predicted_absence - expected_absence)
        absence_fn += len(expected_absence - predicted_absence)
        sector = evaluation_records[str(record["content_hash"])]["sector"]
        counters = sector_counters[sector]
        counters["finding_tp"] += len(predicted_findings & expected_findings)
        counters["finding_fp"] += len(predicted_findings - expected_findings)
        counters["finding_fn"] += len(expected_findings - predicted_findings)
        counters["absence_tp"] += len(predicted_absence & expected_absence)
        counters["absence_fp"] += len(predicted_absence - expected_absence)
        counters["absence_fn"] += len(expected_absence - predicted_absence)
        details.append(
            {
                "id": record.get("id"),
                "finding_extra": sorted(predicted_findings - expected_findings),
                "finding_missed": sorted(expected_findings - predicted_findings),
                "absence_extra": sorted(predicted_absence - expected_absence),
                "absence_missed": sorted(expected_absence - predicted_absence),
            }
        )

    evaluation_sector_counts = Counter(
        metadata["sector"] for metadata in evaluation_records.values()
    )
    annotation_sector_counts = Counter(
        evaluation_records[str(record["content_hash"])]["sector"]
        for record in records
    )
    report = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluator_source_fingerprint": evaluator_source_fingerprint,
        "evaluation_phase": (
            "train_calibration"
            if args.phase == "calibration"
            else "sealed_holdout_final"
        ),
        "records": len(records),
        "dataset_records": len(evaluation_hashes),
        "holdout_records": len(holdout_hashes),
        "annotation_coverage": round(
            len(annotation_hashes) / len(evaluation_hashes), 6
        )
        if evaluation_hashes
        else 1.0,
        "complete_dataset": annotation_hashes == evaluation_hashes,
        "complete_holdout": (
            args.phase == "final" and annotation_hashes == holdout_hashes
        ),
        "holdout_hash_overlap": 0,
        "release_claim_eligible": release_claim_eligible,
        "human_attestation": {
            "verified": attestation_verified,
            "sha256": attestation_sha256,
        },
        "final_evaluation_receipt": (
            {
                "file": receipt_path.name if receipt_path else None,
                "status": "bound",
            }
            if args.phase == "final"
            else None
        ),
        "measurement_units": {
            "expression_detection": "posting_rule_pair",
            "absence_detection": "posting_slot_pair",
            "dictionary_coverage": "human_labeled_expression_occurrence",
        },
        "question_card_metrics": {
            "included_in_g1_g2": False,
            "evaluation": "separate_user_pilot",
            "dimensions": ["relevance", "comprehension", "actionability"],
        },
        "expression_detection": metrics(finding_tp, finding_fp, finding_fn),
        "absence_detection": metrics(absence_tp, absence_fp, absence_fn),
        "dictionary_coverage": round(
            expression_covered / expression_total, 6
        )
        if expression_total
        else None,
        "dictionary_coverage_expressions": expression_total,
        "holdout_by_sector": dict(sorted(evaluation_sector_counts.items())),
        "annotations_by_sector": dict(sorted(annotation_sector_counts.items())),
        "holdout_by_source": dict(
            sorted(
                Counter(
                    metadata["source"] for metadata in evaluation_records.values()
                ).items()
            )
        ),
        "sector_metrics": {
            sector: {
                "records": annotation_sector_counts[sector],
                "expression_detection": metrics(
                    counters["finding_tp"],
                    counters["finding_fp"],
                    counters["finding_fn"],
                ),
                "absence_detection": metrics(
                    counters["absence_tp"],
                    counters["absence_fp"],
                    counters["absence_fn"],
                ),
            }
            for sector, counters in sorted(sector_counters.items())
        },
        "details": details,
        "ruleset_version": engine.ruleset.version,
        "matching_version": engine.ruleset.matching_version,
    }
    if args.phase == "final":
        report["target_gate"] = _target_gate(report, evaluation_sector_counts)
        report["target_gate"]["human_attestation_verified"] = attestation_verified
        report["target_gate"]["release_claim_eligible"] = release_claim_eligible
        report["target_gate"]["passed"] = bool(
            report["target_gate"]["passed"] and release_claim_eligible
        )
    else:
        report["target_gate"] = {
            "passed": False,
            "applicable": False,
            "reason": "train calibration 결과는 G1/G2 최종 성능 주장이 아닙니다",
        }
    if evaluator_fingerprint() != evaluator_source_fingerprint:
        raise SystemExit(
            "평가 실행 중 평가기 또는 엔진 소스가 변경되었습니다. 다시 실행하세요"
        )
    encoded_report = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    report_sha256 = hashlib.sha256(encoded_report.encode("utf-8")).hexdigest()
    try:
        if (
            receipt_path is not None
            and receipt_binding is not None
            and receipt_preparation_status == "reproduced"
        ):
            # Verify an already-finalized receipt before replacing any report.
            finalize_final_evaluation(
                receipt_path,
                receipt_binding,
                report_sha256=report_sha256,
            )
        _atomic_write_text(args.output, encoded_report)
        if (
            receipt_path is not None
            and receipt_binding is not None
            and receipt_preparation_status != "reproduced"
        ):
            finalize_final_evaluation(
                receipt_path,
                receipt_binding,
                report_sha256=report_sha256,
            )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))

    if args.enforce_targets:
        if not report["target_gate"]["passed"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
