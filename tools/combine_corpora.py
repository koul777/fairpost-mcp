from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any


HASH_RE = re.compile(r"[0-9a-f]{64}")
OCCUPATION_CLASSES = {"office", "tech", "research", "field"}


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: manifest는 객체여야 합니다")
    return payload


def read_partition(
    root: Path,
    split: str,
    *,
    allow_legacy_occupation: bool = False,
) -> list[dict[str, str]]:
    records_path = root / split / "records.jsonl"
    manifest_path = root / split / "manifest.json"
    records: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for line_number, line in enumerate(
        records_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{records_path}:{line_number}: 레코드는 객체여야 합니다")
        record_id = record.get("id")
        text = record.get("text")
        content_hash = record.get("content_hash")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"{records_path}:{line_number}: id가 필요합니다")
        if not isinstance(text, str):
            raise ValueError(f"{records_path}:{line_number}: text가 필요합니다")
        if not isinstance(content_hash, str) or not HASH_RE.fullmatch(content_hash):
            raise ValueError(f"{records_path}:{line_number}: content_hash 형식 오류")
        actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual_hash != content_hash:
            raise ValueError(f"{records_path}:{line_number}: 본문 해시 불일치")
        if record_id in seen_ids or content_hash in seen_hashes:
            raise ValueError(f"{records_path}:{line_number}: 중복 레코드")
        for field in ("source", "sector", "occupation", "employment_type"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError(f"{records_path}:{line_number}: {field}가 필요합니다")
        legacy_allowed = (
            allow_legacy_occupation and record["occupation"] == "other"
        )
        if record["occupation"] not in OCCUPATION_CLASSES and not legacy_allowed:
            raise ValueError(
                f"{records_path}:{line_number}: 허용되지 않은 직군 "
                f"'{record['occupation']}'"
            )
        seen_ids.add(record_id)
        seen_hashes.add(content_hash)
        records.append({str(key): str(value) for key, value in record.items()})

    manifest = _read_manifest(manifest_path)
    manifest_ids = manifest.get("ids")
    manifest_hashes = manifest.get("content_hashes")
    if manifest.get("count") != len(records):
        raise ValueError(f"{manifest_path}: count가 실제 레코드 수와 다릅니다")
    if not isinstance(manifest_ids, list) or set(map(str, manifest_ids)) != seen_ids:
        raise ValueError(f"{manifest_path}: ids가 실제 레코드와 다릅니다")
    if (
        not isinstance(manifest_hashes, list)
        or set(map(str, manifest_hashes)) != seen_hashes
    ):
        raise ValueError(f"{manifest_path}: content_hashes가 실제 레코드와 다릅니다")
    return records


def combine(
    public_dir: Path,
    private_dir: Path,
    *,
    expected_public: int,
    expected_private: int,
    train_ratio: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    public_train = read_partition(public_dir, "train")
    public_holdout = read_partition(public_dir, "holdout")
    private_train = read_partition(private_dir, "train")
    private_holdout = read_partition(private_dir, "holdout")

    expected_counts = {
        "public": expected_public,
        "private": expected_private,
    }
    partitions = {
        "public": (public_train, public_holdout),
        "private": (private_train, private_holdout),
    }
    for sector, (train, holdout) in partitions.items():
        records = train + holdout
        if len(records) != expected_counts[sector]:
            raise ValueError(
                f"{sector} 코퍼스는 {expected_counts[sector]}건이어야 합니다: "
                f"현재 {len(records)}건"
            )
        if {record["sector"] for record in records} != {sector}:
            raise ValueError(f"{sector} 코퍼스에 다른 sector가 섞여 있습니다")
        expected_train = round(expected_counts[sector] * train_ratio)
        if len(train) != expected_train:
            raise ValueError(
                f"{sector} 학습 세트는 {expected_train}건이어야 합니다: "
                f"현재 {len(train)}건"
            )

    train = sorted(public_train + private_train, key=lambda item: item["id"])
    holdout = sorted(public_holdout + private_holdout, key=lambda item: item["id"])
    all_records = train + holdout
    ids = [record["id"] for record in all_records]
    hashes = [record["content_hash"] for record in all_records]
    if len(set(ids)) != len(ids):
        raise ValueError("공공/민간 코퍼스 사이에 중복 id가 있습니다")
    if len(set(hashes)) != len(hashes):
        raise ValueError("공공/민간 또는 학습/홀드아웃 사이에 원문 해시 중복이 있습니다")

    def counts(field: str) -> dict[str, int]:
        return dict(sorted(Counter(record[field] for record in all_records).items()))

    summary = {
        "total": len(all_records),
        "train": len(train),
        "holdout": len(holdout),
        "sources": counts("source"),
        "sectors": counts("sector"),
        "occupations": counts("occupation"),
        "employment_types": counts("employment_type"),
        "train_holdout_hash_overlap": 0,
        "deidentification": [
            "email",
            "phone",
            "labeled_contact_name",
            "organization",
        ],
        "raw_postings_committed": False,
        "combined_from_fixed_partitions": True,
    }
    return train, holdout, summary


def _write_jsonl(path: Path, records: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _write_manifest(path: Path, records: list[dict[str, str]]) -> None:
    payload = {
        "count": len(records),
        "ids": [record["id"] for record in records],
        "content_hashes": sorted(record["content_hash"] for record in records),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="이미 고정된 공공/민간 70/30 분할을 재분할 없이 결합합니다."
    )
    parser.add_argument("--public-dir", type=Path, default=Path(".corpus"))
    parser.add_argument("--private-dir", type=Path, default=Path(".corpus-private"))
    parser.add_argument("--output-dir", type=Path, default=Path(".corpus-final"))
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("reports/corpus_summary.json"),
    )
    parser.add_argument("--expected-public", type=int, default=300)
    parser.add_argument("--expected-private", type=int, default=300)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    args = parser.parse_args()

    inputs = {args.public_dir.resolve(), args.private_dir.resolve()}
    if args.output_dir.resolve() in inputs:
        raise SystemExit("--output-dir은 입력 코퍼스 경로와 달라야 합니다")
    if args.expected_public < 1 or args.expected_private < 1:
        raise SystemExit("예상 공고 수는 1 이상이어야 합니다")
    if not 0.5 <= args.train_ratio < 1:
        raise SystemExit("--train-ratio는 0.5 이상 1 미만이어야 합니다")

    try:
        train, holdout, summary = combine(
            args.public_dir,
            args.private_dir,
            expected_public=args.expected_public,
            expected_private=args.expected_private,
            train_ratio=args.train_ratio,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    train_path = args.output_dir / "train"
    holdout_path = args.output_dir / "holdout"
    _write_jsonl(train_path / "records.jsonl", train)
    _write_jsonl(holdout_path / "records.jsonl", holdout)
    _write_manifest(train_path / "manifest.json", train)
    _write_manifest(holdout_path / "manifest.json", holdout)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"고정 분할 결합 완료: train={len(train)}, holdout={len(holdout)}, "
        f"summary={args.summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
