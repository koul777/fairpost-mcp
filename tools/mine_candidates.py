from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


SYSTEM_INSTRUCTION = """당신은 채용공고 표현 후보를 추출하는 사전 구축 보조자입니다.
판정하지 말고 재현율을 우선하여 검토 후보만 제안하십시오.
각 후보에 expression, sentence, posting_id, section, category를 기록하십시오.
category는 age, gender, body, origin, family, disability, health, automation,
process, information, other 중 하나입니다.
법령명이나 조항 번호를 제안하지 마십시오. 최종 채택과 법령 연결은 사람이 합니다."""


def read_jsonl(path: Path) -> list[dict]:
    if "holdout" in {part.casefold() for part in path.parts}:
        raise ValueError("후보 생성기는 홀드아웃 파일을 읽을 수 없습니다")
    records: list[dict] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: JSON 오류") from exc
        if not record.get("id") or not isinstance(record.get("text"), str):
            raise ValueError(f"{path}:{line_number}: id와 text가 필요합니다")
        records.append(record)
    return records


def compact_text(text: str, limit: int) -> str:
    value = re.sub(r"\n{3,}", "\n\n", text.strip())
    return value[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "사전 구축용 70% 코퍼스를 LLM 후보 추출 작업 JSONL로 변환합니다. "
            "이 도구는 모델 API를 호출하지 않습니다."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(".corpus/train/records.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".corpus/candidates/llm_tasks.jsonl"),
    )
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-chars-per-posting", type=int, default=12000)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size는 1 이상이어야 합니다")

    try:
        records = read_jsonl(args.input)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    task_count = 0
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for offset in range(0, len(records), args.batch_size):
            batch = records[offset : offset + args.batch_size]
            payload = [
                {
                    "posting_id": record["id"],
                    "sector": record.get("sector"),
                    "occupation": record.get("occupation"),
                    "employment_type": record.get("employment_type"),
                    "text": compact_text(
                        record["text"], args.max_chars_per_posting
                    ),
                }
                for record in batch
            ]
            task = {
                "custom_id": f"fairpost-candidates-{offset // args.batch_size + 1:05d}",
                "system": SYSTEM_INSTRUCTION,
                "input": payload,
                "response_schema": {
                    "candidates": [
                        {
                            "expression": "string",
                            "sentence": "string",
                            "posting_id": "string",
                            "section": "string|null",
                            "category": "string",
                        }
                    ]
                },
            }
            handle.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")
            task_count += 1
    print(f"{len(records)}건을 {task_count}개 후보 추출 작업으로 생성: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
