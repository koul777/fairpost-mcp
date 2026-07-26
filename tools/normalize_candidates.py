from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import unicodedata


ENDING_RE = re.compile(
    r"(?:인|한|하는|해주실|하실|있는|이신|인재|분|사람|자)\s*$"
)


def normalized_expression(value: str) -> str:
    result = unicodedata.normalize("NFC", value)
    result = re.sub(r"[\"'“”‘’()\[\]{}]", " ", result)
    result = re.sub(r"\s+", " ", result).strip().casefold()
    stemmed = ENDING_RE.sub("", result).strip()
    return stemmed or result


def iter_candidates(path: Path):
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        payload = json.loads(line)
        values = payload.get("candidates", payload)
        if not isinstance(values, list):
            raise ValueError(f"{path}:{line_number}: candidates 목록이 필요합니다")
        for candidate in values:
            if not isinstance(candidate, dict) or not candidate.get("expression"):
                raise ValueError(f"{path}:{line_number}: expression이 필요합니다")
            if candidate.get("article") or candidate.get("statute_id"):
                raise ValueError(
                    f"{path}:{line_number}: 모델 후보에 법령 조항을 넣을 수 없습니다"
                )
            yield candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LLM 후보 표현의 표기 변형을 병합하고 빈도와 출처를 집계합니다."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".corpus/candidates/normalized.json"),
    )
    args = parser.parse_args()

    groups: dict[str, list[dict]] = defaultdict(list)
    try:
        for path in args.inputs:
            for candidate in iter_candidates(path):
                groups[normalized_expression(candidate["expression"])].append(candidate)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    result = []
    for expression, values in groups.items():
        postings = sorted(
            {
                str(value.get("posting_id"))
                for value in values
                if value.get("posting_id")
            }
        )
        categories = sorted(
            {str(value.get("category")) for value in values if value.get("category")}
        )
        variants = sorted({str(value["expression"]).strip() for value in values})
        result.append(
            {
                "expression": expression,
                "variants": variants,
                "corpus_hits": len(postings),
                "posting_ids": postings,
                "categories": categories,
                "decision": "pending_human_review",
            }
        )
    result.sort(key=lambda item: (-item["corpus_hits"], item["expression"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{sum(len(v) for v in groups.values())}개 후보를 {len(result)}개로 병합")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
