from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from core import FairpostEngine


ROOT = Path(__file__).resolve().parents[1]


def _reject_holdout(path: Path) -> None:
    if any("holdout" in part.casefold() for part in path.parts):
        raise ValueError("웹 패리티 감사에는 봉인 홀드아웃 경로를 사용할 수 없습니다")


def _load_training_records(path: Path) -> list[dict[str, Any]]:
    _reject_holdout(path)
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSON 객체가 필요합니다")
            if not isinstance(value.get("id"), str) or not isinstance(
                value.get("text"), str
            ):
                raise ValueError(f"{path}:{line_number}: id와 text가 필요합니다")
            records.append(value)
    if not records:
        raise ValueError(f"{path}: 학습 레코드가 비어 있습니다")
    return records


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def audit(input_path: Path, *, node: str = "node") -> dict[str, Any]:
    records = _load_training_records(input_path)
    if shutil.which(node) is None:
        raise RuntimeError("웹 패리티 감사에는 Node.js가 필요합니다")

    completed = subprocess.run(
        [node, str(ROOT / "tools" / "js_batch_runner.cjs"), str(input_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    web = json.loads(completed.stdout)
    web_rows = web.get("records", [])
    if len(web_rows) != len(records):
        raise RuntimeError(
            f"웹 결과 건수가 입력과 다릅니다: {len(web_rows)} != {len(records)}"
        )

    engine = FairpostEngine()
    mismatches: list[str] = []
    for record, web_row in zip(records, web_rows, strict=True):
        if web_row.get("id") != record["id"]:
            raise RuntimeError("웹 결과의 레코드 순서가 입력과 다릅니다")
        python_hash = _canonical_hash(engine.check(record["text"]).to_dict())
        if web_row.get("result_sha256") != python_hash:
            mismatches.append(record["id"])

    return {
        "input": {
            "path": str(input_path).replace("\\", "/"),
            "records": len(records),
            "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "split": "train_only",
        },
        "ruleset_version": engine.ruleset.version,
        "web_ruleset_version": web.get("ruleset_version"),
        "matched_records": len(records) - len(mismatches),
        "mismatched_records": len(mismatches),
        "mismatch_ids": mismatches[:50],
        "contains_posting_text": False,
        "passed": not mismatches
        and web.get("ruleset_version") == engine.ruleset.version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "학습 코퍼스에서 Python 코어와 정적 웹 엔진 결과를 구조 해시로 "
            "전수 비교합니다. 공고문 원문은 보고서에 기록하지 않습니다."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / ".corpus-final" / "train" / "records.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "web_engine_parity.json",
    )
    args = parser.parse_args()

    try:
        report = audit(args.input)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"{report['input']['records']}건 비교 완료: "
        f"불일치 {report['mismatched_records']}건, {args.output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
