from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.combine_corpora import (  # noqa: E402
    _write_jsonl,
    _write_manifest,
    read_partition,
)


SELECTION_FIELDS = ("id", "occupation", "employment_type")


def _stable_id_key(record: dict[str, str]) -> tuple[str, str]:
    return (
        hashlib.sha256(record["id"].encode("utf-8")).hexdigest(),
        record["id"],
    )


def select_stratified(
    records: list[dict[str, str]],
    target: int,
) -> list[dict[str, str]]:
    if target < 1 or target > len(records):
        raise ValueError("선택 목표는 1 이상 입력 건수 이하여야 합니다")
    strata: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for record in records:
        strata[(record["occupation"], record["employment_type"])].append(record)
    ordered = {
        key: sorted(values, key=_stable_id_key)
        for key, values in sorted(strata.items())
    }
    minimum = 1 if target >= len(ordered) else 0
    ideals = {
        key: target * len(values) / len(records)
        for key, values in ordered.items()
    }
    allocations = {
        key: min(len(values), max(minimum, int(ideals[key])))
        for key, values in ordered.items()
    }

    while sum(allocations.values()) < target:
        candidates = [
            key
            for key, values in ordered.items()
            if allocations[key] < len(values)
        ]
        if not candidates:
            raise ValueError("목표 건수를 만족하는 층화 표본을 만들 수 없습니다")
        key = min(
            candidates,
            key=lambda item: (
                -(ideals[item] - allocations[item]),
                item,
            ),
        )
        allocations[key] += 1

    while sum(allocations.values()) > target:
        candidates = [
            key
            for key in ordered
            if allocations[key] > minimum
        ]
        if not candidates:
            raise ValueError("목표 건수를 만족하는 층화 표본을 만들 수 없습니다")
        key = min(
            candidates,
            key=lambda item: (
                -(allocations[item] - ideals[item]),
                item,
            ),
        )
        allocations[key] -= 1

    selected: list[dict[str, str]] = []
    for key, values in ordered.items():
        selected.extend(values[: allocations[key]])
    return sorted(selected, key=lambda item: item["id"])


def _counts(records: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(record[field] for record in records).items()))


def build_canonical(
    public_dir: Path,
    private_dir: Path,
    *,
    private_train_target: int,
    private_holdout_target: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    public_train = read_partition(public_dir, "train")
    public_holdout = read_partition(public_dir, "holdout")
    private_train = read_partition(private_dir, "train")
    private_holdout = read_partition(private_dir, "holdout")
    if len(public_train) != 210 or len(public_holdout) != 90:
        raise ValueError("공공 코퍼스는 고정된 210/90 분할이어야 합니다")
    if {record["sector"] for record in public_train + public_holdout} != {
        "public"
    }:
        raise ValueError("공공 코퍼스에 다른 sector가 섞여 있습니다")
    if {record["sector"] for record in private_train + private_holdout} != {
        "private"
    }:
        raise ValueError("민간 코퍼스에 다른 sector가 섞여 있습니다")

    selected_private_train = select_stratified(
        private_train,
        private_train_target,
    )
    selected_private_holdout = select_stratified(
        private_holdout,
        private_holdout_target,
    )
    train = sorted(public_train + selected_private_train, key=lambda item: item["id"])
    holdout = sorted(
        public_holdout + selected_private_holdout,
        key=lambda item: item["id"],
    )
    all_records = train + holdout
    ids = [record["id"] for record in all_records]
    hashes = [record["content_hash"] for record in all_records]
    if len(ids) != len(set(ids)) or len(hashes) != len(set(hashes)):
        raise ValueError("canonical 코퍼스에 ID 또는 원문 해시 중복이 있습니다")
    if {record["content_hash"] for record in train} & {
        record["content_hash"] for record in holdout
    }:
        raise ValueError("canonical 학습/홀드아웃 원문 해시가 겹칩니다")

    summary = {
        "schema_version": "fairpost-prd-corpus-summary-v1",
        "total": len(all_records),
        "train": len(train),
        "holdout": len(holdout),
        "sources": _counts(all_records, "source"),
        "sectors": _counts(all_records, "sector"),
        "occupations": _counts(all_records, "occupation"),
        "employment_types": _counts(all_records, "employment_type"),
        "train_holdout_hash_overlap": 0,
        "canonical_prd_corpus": True,
        "derived_from_fixed_partitions": True,
        "extended_corpus_preserved": True,
        "selection_uses_posting_text": False,
        "selection_fields": list(SELECTION_FIELDS),
        "selection_algorithm": (
            "proportional occupation/employment strata with SHA-256(id) ordering"
        ),
        "private_selected": {
            "train": len(selected_private_train),
            "holdout": len(selected_private_holdout),
        },
        "raw_postings_committed": False,
    }
    return train, holdout, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "확장 코퍼스의 기존 분할을 보존하면서 PRD의 공공 300+민간 300 "
            "canonical 평가 세트를 결정론적으로 파생합니다."
        )
    )
    parser.add_argument("--public-dir", type=Path, default=Path(".corpus"))
    parser.add_argument(
        "--private-dir",
        type=Path,
        default=Path(".corpus-private-open"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path(".corpus-prd"))
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("reports/prd_corpus_summary.json"),
    )
    parser.add_argument("--private-train-target", type=int, default=210)
    parser.add_argument("--private-holdout-target", type=int, default=90)
    args = parser.parse_args()
    inputs = {args.public_dir.resolve(), args.private_dir.resolve()}
    if args.output_dir.resolve() in inputs:
        raise SystemExit("--output-dir은 입력 코퍼스 경로와 달라야 합니다")
    try:
        train, holdout, summary = build_canonical(
            args.public_dir,
            args.private_dir,
            private_train_target=args.private_train_target,
            private_holdout_target=args.private_holdout_target,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    for split, records in (("train", train), ("holdout", holdout)):
        directory = args.output_dir / split
        _write_jsonl(directory / "records.jsonl", records)
        _write_manifest(directory / "manifest.json", records)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    read_partition(args.output_dir, "train")
    read_partition(args.output_dir, "holdout")
    print(
        "PRD canonical 코퍼스 생성 완료: "
        f"train={len(train)}, holdout={len(holdout)}, "
        f"summary={args.summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
