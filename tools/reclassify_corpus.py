from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_corpus import (  # noqa: E402
    OCCUPATION_CLASSES,
    classify_occupation,
    summarize,
)
from tools.combine_corpora import read_partition  # noqa: E402


def reclassify_records(
    records: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    updated: list[dict[str, str]] = []
    changes = 0
    for record in records:
        occupation = classify_occupation(record["text"], record["sector"])
        replacement = dict(record)
        if replacement["occupation"] != occupation:
            replacement["occupation"] = occupation
            changes += 1
        updated.append(replacement)
    if {record["occupation"] for record in updated} - set(OCCUPATION_CLASSES):
        raise ValueError("재분류 결과에 허용되지 않은 직군이 있습니다")
    return updated, changes


def _identity(records: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(record["id"], record["content_hash"]) for record in records]


def _serialized(records: list[dict[str, str]]) -> str:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def _stage(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


def migrate(
    corpus_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    train = read_partition(
        corpus_dir,
        "train",
        allow_legacy_occupation=True,
    )
    holdout = read_partition(
        corpus_dir,
        "holdout",
        allow_legacy_occupation=True,
    )
    updated_train, train_changes = reclassify_records(train)
    updated_holdout, holdout_changes = reclassify_records(holdout)

    if _identity(updated_train) != _identity(train):
        raise ValueError("학습 세트의 ID 또는 원문 해시가 변경되었습니다")
    if _identity(updated_holdout) != _identity(holdout):
        raise ValueError("홀드아웃의 ID 또는 원문 해시가 변경되었습니다")

    summary = summarize(
        updated_train + updated_holdout,
        updated_train,
        updated_holdout,
    )
    summary["reclassified_preserving_fixed_partitions"] = True

    train_path = corpus_dir / "train" / "records.jsonl"
    holdout_path = corpus_dir / "holdout" / "records.jsonl"
    staged_train = _stage(train_path, _serialized(updated_train))
    staged_holdout = _stage(holdout_path, _serialized(updated_holdout))
    staged_summary = _stage(
        summary_path,
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    try:
        os.replace(staged_train, train_path)
        os.replace(staged_holdout, holdout_path)
        os.replace(staged_summary, summary_path)
    finally:
        staged_train.unlink(missing_ok=True)
        staged_holdout.unlink(missing_ok=True)
        staged_summary.unlink(missing_ok=True)

    # The unchanged manifests must still validate the migrated partitions.
    read_partition(corpus_dir, "train")
    read_partition(corpus_dir, "holdout")
    return {
        "train": len(updated_train),
        "holdout": len(updated_holdout),
        "changed": train_changes + holdout_changes,
        "occupations": summary["occupations"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "기존 IDㆍ원문 해시ㆍ학습/홀드아웃 배정을 보존하면서 "
            "PRD의 사무ㆍ기술ㆍ연구ㆍ현장 네 직군으로 메타데이터를 재분류합니다."
        )
    )
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = migrate(args.corpus_dir, args.summary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "직군 재분류 완료: "
        f"train={result['train']}, holdout={result['holdout']}, "
        f"changed={result['changed']}, occupations={result['occupations']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
