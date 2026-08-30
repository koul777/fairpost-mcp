from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), name="KST")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_private_review_queue as queue_builder  # noqa: E402
from tools import summarize_private_review as review_summary  # noqa: E402


SAMPLING_FIELDS = (
    "candidate_matches",
    "unique_contexts",
    "selected_rows",
    "collapsed_duplicate_contexts",
    "truncated_unique_contexts",
)


def build_sampling_audit(
    queue_path: Path,
    queue_manifest_path: Path,
    source_input_path: Path,
) -> dict[str, Any]:
    queue_path = Path(queue_path)
    queue_manifest_path = Path(queue_manifest_path)
    source_input_path = Path(source_input_path)
    rows = review_summary.load_review_rows(queue_path)
    try:
        source_payload = source_input_path.read_bytes()
    except OSError as exc:
        raise review_summary.ReviewSummaryError(
            "--source-input could not be read"
        ) from exc
    source_sha256 = hashlib.sha256(source_payload).hexdigest()
    manifest = review_summary._load_queue_manifest(
        queue_manifest_path,
        rows,
        source_input_sha256=source_sha256,
    )
    review_summary._verify_queue_derivation(
        source_payload,
        source_input_path,
        rows,
        manifest,
    )
    records = queue_builder.load_private_train_records_bytes(
        source_payload,
        source_path=source_input_path,
    )
    sampling: Mapping[str, Mapping[str, int]] = manifest["rule_sampling"]
    aggregate = {
        field: sum(rule[field] for rule in sampling.values())
        for field in SAMPLING_FIELDS
    }
    source_categories = {
        str(record["source"])
        for record in records
        if isinstance(record.get("source"), str) and record["source"]
    }
    return {
        "schema_version": "private-review-sampling-audit-v1",
        "created_at": datetime.now(KST).isoformat(
            timespec="seconds"
        ),
        "input": {
            "records": len(records),
            "sector": "private",
            "split": "train_only",
            "source_categories": len(source_categories),
        },
        "ruleset_version": manifest["ruleset_version"],
        "matching_version": manifest["matching_version"],
        "selection": {
            "per_rule": manifest["per_rule"],
            "context_chars": manifest["context_chars"],
            "selected_rule_count": len(manifest["selected_rule_ids"]),
            **aggregate,
        },
        "rule_sampling": {
            rule_id: dict(sampling[rule_id]) for rule_id in sorted(sampling)
        },
        "privacy_boundary": {
            "contains_posting_text": False,
            "contains_record_ids": False,
            "contains_source_ids": False,
            "contains_organization_identifiers": False,
            "contains_personal_identifiers": False,
        },
        "limitations": [
            "고정 해시 순서와 고유 문맥 축약을 사용한 목적표본이며 무작위 게시물 표본이 아니다.",
            "동일 공고가 여러 규칙에 걸리면 규칙별 후보와 선택 행에 중복 가중될 수 있다.",
            "선택 행 수와 aggregate precision은 시장 발생률이나 게시물 수준 성능을 뜻하지 않는다.",
            "현재 입력은 단일 출처 범주이므로 민간 채용시장 전체로 일반화하지 않는다.",
        ],
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "검토 큐 sidecar와 원본 train 해시를 검증해 원문 없는 sampling 감사를 만듭니다."
        )
    )
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        review_summary.validate_paths(
            args.queue,
            args.output,
            args.manifest,
            args.source_input,
        )
        report = build_sampling_audit(
            args.queue,
            args.manifest,
            args.source_input,
        )
        review_summary._write_report(args.output, report)
    except (OSError, ValueError, review_summary.ReviewSummaryError):
        print("error: private review sampling audit could not be built", file=sys.stderr)
        return 1
    print(
        json.dumps(
            report["selection"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
