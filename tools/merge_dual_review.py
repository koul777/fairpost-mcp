"""이중 검토 병합과 일치율 계산.

민간 검토 큐(`build_private_review_queue.py`)를 두 검토자가 따로 라벨링한 파일을
행 ID로 맞춰 일치율과 Cohen 카파를 계산하고, 불일치 행만 모은 조정 파일을 쓴다.
조정자가 조정 파일을 채우면 같은 명령에 `--adjudication`을 붙여 다시 실행해서
합의 행과 조정 행으로 이루어진 최종 라벨 파일을 만든다.

개인정보 경계
-------------
- 표준 출력과 보고서에는 공고 문맥, 매치 문자열을 쓰지 않는다. ID, 규칙 ID, 숫자만 쓴다.
- 조정 파일과 병합 라벨 파일은 큐와 같은 문맥을 담으므로 `.private-review/` 안에서만 쓴다.
- 홀드아웃, 평가, 개발 분할 경로는 `summarize_private_review.py`와 같은 규칙으로 거부한다.

종료 코드는 정상 0, 입력 또는 경로 오류 1, 기준 미달 경보 2다.
"""

from __future__ import annotations

import argparse
from collections import Counter
import functools
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, NoReturn, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_private_review_queue as review_queue  # noqa: E402
from tools import summarize_private_review as review_summary  # noqa: E402
from tools._paths import atomic_write_text, paths_alias  # noqa: E402


REPORT_SCHEMA_VERSION = "dual-review-merge-report-v1"
SOURCES_SCHEMA_VERSION = "dual-review-merge-sources-v1"

LABELS = review_summary.ALLOWED_LABELS
UNLABELED = "unreviewed"
UNCERTAIN = "uncertain"
QUEUE_FIELDS = review_summary.INPUT_FIELDS
REVIEW_ID_RE = review_summary.REVIEW_ID_RE

ADJUDICATOR_FIELDS = ("adjudicated_label", "adjudicator_id", "reason_code")
ADJUDICATION_FIELDS = frozenset(
    {
        "review_id",
        "rule_id",
        "layer",
        "dimension",
        "section",
        "context",
        "matched_text",
        "allowed_labels",
        "label_a",
        "label_b",
        *ADJUDICATOR_FIELDS,
    }
)
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
REASON_CODE_RE = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣._-]{0,39}\Z")

DEFAULT_OUTPUT_DIR = Path(".private-review")
REPORT_NAME = "dual-review-report.json"
ADJUDICATION_NAME = "dual-review-adjudication.jsonl"
MERGED_NAME = "dual-review-merged.jsonl"
SOURCES_NAME = "dual-review-merged.jsonl.sources.json"

DEFAULT_MIN_AGREEMENT = 0.80
DEFAULT_MIN_KAPPA = 0.60
DEFAULT_MAX_UNCERTAIN_SHARE = 0.10
DEFAULT_MIN_ROWS_PER_RULE = 10

KAPPA_REASONS = {
    "expected_agreement_is_one": (
        "우연 일치율이 1이라 카파를 계산할 수 없음. "
        "두 검토자가 모든 비교 행에 같은 한 가지 라벨만 씀"
    ),
    "no_rows": "라벨이 모두 채워진 비교 행이 없어 카파를 계산할 수 없음",
}
SIDES = ("a", "b")


@functools.lru_cache(maxsize=1)
def _rule_metadata() -> Mapping[str, tuple[str, str]]:
    """현재 규칙셋의 presence law/question 규칙. 한 프로세스에서 한 번만 읽는다."""
    return review_summary._rule_metadata()


class DualReviewError(ValueError):
    """사용자에게 보여도 되는 오류. 입력 값, 문맥, 경로는 담지 않는다."""


class _DuplicateField(ValueError):
    pass


# ---------------------------------------------------------------------------
# 경로 규칙
# ---------------------------------------------------------------------------


def output_paths(output_dir: Path) -> dict[str, Path]:
    output_dir = Path(output_dir)
    return {
        "report": output_dir / REPORT_NAME,
        "adjudication": output_dir / ADJUDICATION_NAME,
        "merged": output_dir / MERGED_NAME,
        "sources": output_dir / SOURCES_NAME,
    }


def _reject_forbidden_path(path: Path, *, argument: str) -> None:
    # summarize_private_review와 같은 train 전용 경로 규칙을 그대로 쓴다.
    try:
        review_summary._reject_forbidden_path(path, argument=argument)
    except review_summary.ReviewSummaryError as exc:
        raise DualReviewError(
            f"{argument} 경로에는 홀드아웃, 평가, 개발 분할 이름을 사용할 수 없습니다"
        ) from exc


def validate_paths(
    inputs_a: Sequence[Path],
    inputs_b: Sequence[Path],
    output_dir: Path,
    *,
    adjudication_path: Path | None = None,
) -> None:
    """입력을 열기 전에 경로 경계와 덮어쓰기 충돌을 검사한다."""
    outputs = output_paths(output_dir)
    labeled: list[tuple[str, Path]] = []
    labeled.extend(("--input-a", Path(path)) for path in inputs_a)
    labeled.extend(("--input-b", Path(path)) for path in inputs_b)
    if adjudication_path is not None:
        labeled.append(("--adjudication", Path(adjudication_path)))
    input_count = len(labeled)
    labeled.append(("--output-dir", Path(output_dir)))
    labeled.append(("보고서 출력", outputs["report"]))
    labeled.append(("병합 라벨 출력", outputs["merged"]))
    labeled.append(("출처 기록 출력", outputs["sources"]))
    if adjudication_path is None:
        labeled.append(("조정 파일 출력", outputs["adjudication"]))

    for argument, path in labeled:
        _reject_forbidden_path(path, argument=argument)

    for index, (left_name, left_path) in enumerate(labeled[:input_count]):
        for right_name, right_path in labeled[index + 1 :]:
            if paths_alias(left_path, right_path):
                raise DualReviewError(
                    f"{left_name}, {right_name}: 서로 다른 파일이어야 합니다"
                )
    if Path(output_dir).exists() and not Path(output_dir).is_dir():
        raise DualReviewError("--output-dir은 폴더 경로여야 합니다")
    for path in outputs.values():
        if path.exists() and path.is_dir():
            raise DualReviewError(f"{path.name} 경로가 폴더라서 출력할 수 없습니다")


# ---------------------------------------------------------------------------
# 입력 읽기와 검증
# ---------------------------------------------------------------------------


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateField("duplicate field")
        value[key] = item
    return value


def _where(name: str, line_number: int) -> str:
    return f"{name} {line_number}행"


def _read_json_lines(path: Path, *, name: str) -> list[tuple[int, Any]]:
    """JSONL을 읽는다. 오류에는 파일 이름표와 줄 번호만 담는다."""
    path = Path(path)
    if path.exists() and path.is_dir():
        raise DualReviewError(f"{name}: 파일 경로여야 합니다")
    entries: list[tuple[int, Any]] = []
    try:
        # utf-8-sig: PowerShell 5.1이 BOM을 붙여 저장한 파일도 읽는다.
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line, object_pairs_hook=_unique_object)
                except _DuplicateField as exc:
                    raise DualReviewError(
                        f"{_where(name, line_number)}: JSON 필드가 중복되었습니다"
                    ) from exc
                except json.JSONDecodeError as exc:
                    raise DualReviewError(
                        f"{_where(name, line_number)}: JSON 형식이 올바르지 않습니다"
                    ) from exc
                entries.append((line_number, value))
    except DualReviewError:
        raise
    except (OSError, UnicodeError) as exc:
        raise DualReviewError(f"{name}: 읽을 수 없습니다") from exc
    return entries


def _validate_queue_row(
    value: object,
    *,
    name: str,
    line_number: int,
    rules: Mapping[str, tuple[str, str]],
) -> dict[str, Any]:
    where = _where(name, line_number)
    if not isinstance(value, dict):
        raise DualReviewError(f"{where}: JSON 객체가 필요합니다")
    if set(value) != QUEUE_FIELDS:
        raise DualReviewError(f"{where}: 필드가 익명 검토 큐 스키마와 다릅니다")

    review_id = value["review_id"]
    if not isinstance(review_id, str) or REVIEW_ID_RE.fullmatch(review_id) is None:
        raise DualReviewError(f"{where}: review_id 형식이 올바르지 않습니다")
    rule_id = value["rule_id"]
    if not isinstance(rule_id, str) or rule_id not in rules:
        raise DualReviewError(f"{where}: rule_id가 현재 규칙에 없습니다")
    expected_layer, expected_dimension = rules[rule_id]
    if value["layer"] != expected_layer:
        raise DualReviewError(f"{where}: layer가 rule_id와 맞지 않습니다")
    if value["dimension"] != expected_dimension:
        raise DualReviewError(f"{where}: dimension이 rule_id와 맞지 않습니다")
    for field in ("context", "matched_text"):
        item = value[field]
        if not isinstance(item, str) or not item:
            raise DualReviewError(f"{where}: {field}는 비어 있지 않은 문자열이어야 합니다")
    section = value["section"]
    if section is not None and not isinstance(section, str):
        raise DualReviewError(f"{where}: section은 문자열 또는 null이어야 합니다")
    if value["allowed_labels"] != list(LABELS):
        raise DualReviewError(f"{where}: allowed_labels가 올바르지 않습니다")
    label = value["label"]
    if not isinstance(label, str) or label not in (*LABELS, UNLABELED):
        raise DualReviewError(f"{where}: label 값이 허용 목록에 없습니다")
    return {field: value[field] for field in QUEUE_FIELDS}


def load_review_side(
    paths: Sequence[Path],
    *,
    side: str,
    rules: Mapping[str, tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    """한쪽 검토자의 파일들을 review_id로 색인한다. 중복 ID는 오류다."""
    rows: dict[str, dict[str, Any]] = {}
    for index, path in enumerate(paths, start=1):
        name = f"검토 파일 {side.upper()}" + (f"({index}번째)" if len(paths) > 1 else "")
        for line_number, value in _read_json_lines(path, name=name):
            row = _validate_queue_row(
                value, name=name, line_number=line_number, rules=rules
            )
            review_id = row["review_id"]
            if review_id in rows:
                raise DualReviewError(
                    f"{_where(name, line_number)}: review_id가 중복되었습니다"
                )
            rows[review_id] = row
    if not rows:
        raise DualReviewError(f"검토 파일 {side.upper()}에 행이 없습니다")
    return rows


def _row_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row["rule_id"]), str(row["review_id"]))


# ---------------------------------------------------------------------------
# 두 파일 맞추기
# ---------------------------------------------------------------------------


def match_rows(
    rows_a: Mapping[str, dict[str, Any]],
    rows_b: Mapping[str, dict[str, Any]],
    *,
    allow_partial: bool = False,
    allow_subset: bool = False,
) -> dict[str, Any]:
    """행 집합, 큐 지문, 미라벨 행을 검사하고 비교 대상을 나눈다.

    반환값
    - ``compared``: 양쪽 라벨이 모두 있는 행 (rule_id, review_id 순)
    - ``incomplete``: 한쪽 이상 미검토인 행의 ID
    - ``singles``: 한쪽 파일에만 있는 행 (``allow_subset`` 일 때만 생김)
    """
    ids_a, ids_b = set(rows_a), set(rows_b)
    only_a, only_b = ids_a - ids_b, ids_b - ids_a
    if only_a or only_b:
        counts = f"A에만 {len(only_a)}행, B에만 {len(only_b)}행"
        if not allow_subset:
            raise DualReviewError(
                f"두 검토 파일의 행 집합이 같지 않습니다 ({counts}). "
                "같은 큐에서 나온 파일인지 확인하세요. 한쪽이 다른 쪽의 일부이면 "
                "--allow-subset을 쓰세요"
            )
        if only_a and only_b:
            raise DualReviewError(
                f"한쪽 검토 파일이 다른 쪽에 포함되지 않습니다 ({counts})"
            )
    both = sorted(ids_a & ids_b, key=lambda rid: _row_key(rows_a[rid]))
    if not both:
        raise DualReviewError("두 검토 파일에 공통된 행이 없습니다")

    # 큐 지문: 라벨을 뺀 모든 필드가 같아야 같은 큐에서 나온 파일이다.
    fingerprint_a = review_queue.immutable_rows_sha256([rows_a[rid] for rid in both])
    fingerprint_b = review_queue.immutable_rows_sha256([rows_b[rid] for rid in both])
    if fingerprint_a != fingerprint_b:
        raise DualReviewError(
            "두 검토 파일의 큐 지문이 다릅니다. 라벨 외 필드가 서로 다른 행이 있습니다"
        )

    unlabeled = {
        side: sum(1 for row in rows.values() if row["label"] == UNLABELED)
        for side, rows in (("a", rows_a), ("b", rows_b))
    }
    if not allow_partial and (unlabeled["a"] or unlabeled["b"]):
        raise DualReviewError(
            f"미검토 행이 있습니다 (A {unlabeled['a']}행, B {unlabeled['b']}행). "
            "모두 라벨링하거나 --allow-partial을 쓰세요"
        )

    compared: list[str] = []
    incomplete: list[str] = []
    for rid in both:
        if UNLABELED in (rows_a[rid]["label"], rows_b[rid]["label"]):
            incomplete.append(rid)
        else:
            compared.append(rid)
    singles: dict[str, str] = {}
    for rid in sorted(only_a, key=lambda item: _row_key(rows_a[item])):
        singles[rid] = "a"
    for rid in sorted(only_b, key=lambda item: _row_key(rows_b[item])):
        singles[rid] = "b"
    if not compared:
        raise DualReviewError("라벨이 모두 채워진 비교 행이 없습니다")
    return {
        "compared": compared,
        "incomplete": incomplete,
        "singles": singles,
        "unlabeled": unlabeled,
        "fingerprints": {
            "a": review_queue.immutable_rows_sha256(list(rows_a.values())),
            "b": review_queue.immutable_rows_sha256(list(rows_b.values())),
            "compared_scope": fingerprint_a,
        },
    }


# ---------------------------------------------------------------------------
# 일치율과 카파
# ---------------------------------------------------------------------------


def _round(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if rounded == 0 else rounded


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return _round(numerator / denominator)


def pair_statistics(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """라벨 쌍 목록의 일치율, 카파, uncertain 비율을 계산한다.

    카파는 정수 연산으로 계산한다: (일치 수 * n - S) / (n * n - S),
    S = sum(A의 i 라벨 수 * B의 i 라벨 수). 분모가 0이면 우연 일치율이 1이므로
    카파를 null로 두고 사유를 쓴다. NaN은 만들지 않는다.
    """
    rows = len(pairs)
    counts_a = Counter(a for a, _b in pairs)
    counts_b = Counter(b for _a, b in pairs)
    agreed = sum(1 for a, b in pairs if a == b)
    stats: dict[str, Any] = {
        "rows": rows,
        "agreed": agreed,
        "disagreed": rows - agreed,
        "agreement": _ratio(agreed, rows),
        "label_counts": {
            "a": {label: counts_a[label] for label in LABELS},
            "b": {label: counts_b[label] for label in LABELS},
        },
        "uncertain_share": {
            "a": _ratio(counts_a[UNCERTAIN], rows),
            "b": _ratio(counts_b[UNCERTAIN], rows),
        },
        "expected_agreement": None,
        "kappa": None,
        "kappa_reason_code": None,
        "kappa_reason": None,
    }
    if rows == 0:
        reason = "no_rows"
    else:
        overlap = sum(counts_a[label] * counts_b[label] for label in LABELS)
        stats["expected_agreement"] = _round(overlap / (rows * rows))
        denominator = rows * rows - overlap
        if denominator == 0:
            reason = "expected_agreement_is_one"
        else:
            stats["kappa"] = _round((agreed * rows - overlap) / denominator)
            return stats
    stats["kappa_reason_code"] = reason
    stats["kappa_reason"] = KAPPA_REASONS[reason]
    return stats


def _validate_thresholds(
    min_agreement: object,
    min_kappa: object,
    max_uncertain_share: object,
    min_rows_per_rule: object,
) -> dict[str, int | float]:
    def finite(value: object, name: str, low: float, high: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not low <= float(value) <= high
        ):
            raise DualReviewError(f"{name}: {low:g} 이상 {high:g} 이하의 숫자여야 합니다")
        return float(value)

    if (
        isinstance(min_rows_per_rule, bool)
        or not isinstance(min_rows_per_rule, int)
        or min_rows_per_rule < 0
    ):
        raise DualReviewError("--min-rows-per-rule: 0 이상의 정수여야 합니다")
    return {
        "min_agreement_per_rule": finite(min_agreement, "--min-agreement", 0, 1),
        "min_kappa_overall": finite(min_kappa, "--min-kappa", -1, 1),
        "max_uncertain_share": finite(
            max_uncertain_share, "--max-uncertain-share", 0, 1
        ),
        "min_rows_per_rule": min_rows_per_rule,
    }


def _side_name(side: str) -> str:
    return side.upper()


def evaluate_gates(
    rules: Sequence[Mapping[str, Any]],
    overall: Mapping[str, Any],
    thresholds: Mapping[str, int | float],
) -> dict[str, list[dict[str, Any]]]:
    """기준 미달 목록을 만든다. coverage_gap 규칙은 기준 평가에서 빠진다."""
    min_agreement = float(thresholds["min_agreement_per_rule"])
    min_kappa = float(thresholds["min_kappa_overall"])
    max_uncertain = float(thresholds["max_uncertain_share"])
    alerts: dict[str, list[dict[str, Any]]] = {
        "agreement_below_threshold": [],
        "kappa_below_threshold": [],
        "uncertain_share_above_threshold": [],
    }
    for rule in rules:
        if rule["status"] == "coverage_gap":
            continue
        agreement = rule["agreement"]
        if agreement is not None and agreement < min_agreement:
            alerts["agreement_below_threshold"].append(
                {
                    "rule_id": rule["rule_id"],
                    "rows": rule["rows"],
                    "agreement": agreement,
                    "minimum": min_agreement,
                    "message": (
                        f"규칙 {rule['rule_id']}의 일치율 {agreement:.4f}이 "
                        f"기준 {min_agreement:.4f} 미만임 ({rule['rows']}행)"
                    ),
                }
            )
        for side in SIDES:
            share = rule["uncertain_share"][side]
            if share is not None and share > max_uncertain:
                alerts["uncertain_share_above_threshold"].append(
                    {
                        "scope": "rule",
                        "rule_id": rule["rule_id"],
                        "reviewer": side,
                        "rows": rule["rows"],
                        "share": share,
                        "maximum": max_uncertain,
                        "message": (
                            f"규칙 {rule['rule_id']}의 검토자 {_side_name(side)} "
                            f"uncertain 비율 {share:.4f}이 상한 {max_uncertain:.4f} 초과임"
                        ),
                    }
                )
    kappa = overall["kappa"]
    if kappa is not None and kappa < min_kappa:
        alerts["kappa_below_threshold"].append(
            {
                "scope": "overall",
                "rows": overall["rows"],
                "kappa": kappa,
                "minimum": min_kappa,
                "message": (
                    f"전체 카파 {kappa:.4f}이 기준 {min_kappa:.4f} 미만임 "
                    f"({overall['rows']}행)"
                ),
            }
        )
    for side in SIDES:
        share = overall["uncertain_share"][side]
        if share is not None and share > max_uncertain:
            alerts["uncertain_share_above_threshold"].append(
                {
                    "scope": "overall",
                    "rule_id": None,
                    "reviewer": side,
                    "rows": overall["rows"],
                    "share": share,
                    "maximum": max_uncertain,
                    "message": (
                        f"전체 검토자 {_side_name(side)}의 uncertain 비율 {share:.4f}이 "
                        f"상한 {max_uncertain:.4f} 초과임"
                    ),
                }
            )
    return alerts


def build_statistics(
    rows_a: Mapping[str, dict[str, Any]],
    rows_b: Mapping[str, dict[str, Any]],
    matched: Mapping[str, Any],
    thresholds: Mapping[str, int | float],
) -> dict[str, Any]:
    """규칙별, 전체 통계와 기준 평가를 만든다."""
    minimum_rows = int(thresholds["min_rows_per_rule"])
    pairs_by_rule: dict[str, list[tuple[str, str]]] = {}
    layers: dict[str, str] = {}
    for rid in matched["compared"]:
        row = rows_a[rid]
        pairs_by_rule.setdefault(row["rule_id"], []).append(
            (row["label"], rows_b[rid]["label"])
        )
    for rows in (rows_a, rows_b):
        for row in rows.values():
            layers[row["rule_id"]] = row["layer"]

    rule_entries: list[dict[str, Any]] = []
    for rule_id in sorted(layers):
        stats = pair_statistics(pairs_by_rule.get(rule_id, []))
        entry = {"rule_id": rule_id, "layer": layers[rule_id], **stats}
        entry["status"] = "coverage_gap" if stats["rows"] < minimum_rows else "ok"
        rule_entries.append(entry)

    overall = pair_statistics(
        [pair for rule_id in sorted(pairs_by_rule) for pair in pairs_by_rule[rule_id]]
    )
    alerts = evaluate_gates(rule_entries, overall, thresholds)
    alerting_rules = {
        item["rule_id"]
        for kind in ("agreement_below_threshold", "uncertain_share_above_threshold")
        for item in alerts[kind]
        if item.get("rule_id")
    }
    for entry in rule_entries:
        if entry["status"] != "coverage_gap" and entry["rule_id"] in alerting_rules:
            entry["status"] = "alert"
    return {
        "rules": rule_entries,
        "overall": overall,
        "alerts": alerts,
        "coverage_gap_rules": [
            entry["rule_id"] for entry in rule_entries if entry["status"] == "coverage_gap"
        ],
    }


# ---------------------------------------------------------------------------
# 조정 파일
# ---------------------------------------------------------------------------


def disagreement_ids(
    rows_a: Mapping[str, dict[str, Any]],
    rows_b: Mapping[str, dict[str, Any]],
    matched: Mapping[str, Any],
) -> list[str]:
    ids = [
        rid
        for rid in matched["compared"]
        if rows_a[rid]["label"] != rows_b[rid]["label"]
    ]
    return sorted(ids, key=lambda rid: _row_key(rows_a[rid]))


def build_adjudication_rows(
    ids: Sequence[str],
    rows_a: Mapping[str, dict[str, Any]],
    rows_b: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """불일치 행만 담은 조정 파일 행. 조정자 필드는 비어 있다."""
    result: list[dict[str, Any]] = []
    for rid in ids:
        base = rows_a[rid]
        row = {
            field: base[field]
            for field in (
                "review_id",
                "rule_id",
                "layer",
                "dimension",
                "section",
                "context",
                "matched_text",
            )
        }
        row["allowed_labels"] = list(LABELS)
        row["label_a"] = rows_a[rid]["label"]
        row["label_b"] = rows_b[rid]["label"]
        for field in ADJUDICATOR_FIELDS:
            row[field] = ""
        result.append(row)
    return result


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )


def _guard_existing_adjudication(path: Path) -> None:
    """이미 조정자가 채운 조정 파일을 새 템플릿으로 덮어쓰지 않는다."""
    if not path.exists():
        return
    try:
        entries = _read_json_lines(path, name="기존 조정 파일")
    except DualReviewError as exc:
        raise DualReviewError(
            "기존 조정 파일을 읽을 수 없어 덮어쓰지 않습니다. "
            "파일을 옮기거나 --output-dir을 바꾸세요"
        ) from exc
    for _line_number, value in entries:
        if isinstance(value, dict) and any(
            value.get(field) for field in ADJUDICATOR_FIELDS
        ):
            raise DualReviewError(
                "기존 조정 파일에 조정 기록이 들어 있어 덮어쓰지 않습니다. "
                "조정을 마쳤다면 --adjudication으로 지정하고, 아니면 파일을 옮기거나 "
                "--output-dir을 바꾸세요"
            )


def load_adjudication(
    path: Path,
    expected_rows: Sequence[Mapping[str, Any]],
    *,
    reviewer_ids: Sequence[str] = (),
) -> dict[str, dict[str, str]]:
    """조정 파일을 읽어 현재 불일치 행과 대조한 뒤 review_id별 판정을 돌려준다."""
    name = "조정 파일"
    expected = {row["review_id"]: row for row in expected_rows}
    given: dict[str, dict[str, Any]] = {}
    for line_number, value in _read_json_lines(path, name=name):
        where = _where(name, line_number)
        if not isinstance(value, dict):
            raise DualReviewError(f"{where}: JSON 객체가 필요합니다")
        if set(value) != ADJUDICATION_FIELDS:
            raise DualReviewError(f"{where}: 필드가 조정 파일 스키마와 다릅니다")
        review_id = value["review_id"]
        if not isinstance(review_id, str) or REVIEW_ID_RE.fullmatch(review_id) is None:
            raise DualReviewError(f"{where}: review_id 형식이 올바르지 않습니다")
        if review_id in given:
            raise DualReviewError(f"{where}: review_id가 중복되었습니다")
        given[review_id] = value

    extra = set(given) - set(expected)
    missing = set(expected) - set(given)
    if extra:
        raise DualReviewError(
            f"조정 파일에 현재 불일치가 아닌 행이 {len(extra)}개 있습니다. "
            "검토 파일이 바뀌었다면 조정 파일을 새로 만드세요"
        )
    if missing:
        raise DualReviewError(
            f"조정 파일에 없는 불일치 행이 {len(missing)}개 있습니다"
        )
    static_fields = ADJUDICATION_FIELDS - set(ADJUDICATOR_FIELDS)
    stale = [
        rid
        for rid, row in given.items()
        if any(row[field] != expected[rid][field] for field in static_fields)
    ]
    if stale:
        raise DualReviewError(
            f"조정 파일의 {len(stale)}개 행이 현재 검토 파일의 라벨 또는 내용과 다릅니다. "
            "조정 파일을 새로 만드세요"
        )

    blank = 0
    partial = 0
    invalid = 0
    decisions: dict[str, dict[str, str]] = {}
    for rid, row in given.items():
        filled = [bool(row[field]) for field in ADJUDICATOR_FIELDS]
        if not any(filled):
            blank += 1
            continue
        if not all(filled):
            partial += 1
            continue
        label, adjudicator, reason = (row[field] for field in ADJUDICATOR_FIELDS)
        if (
            not isinstance(label, str)
            or label not in LABELS
            or not isinstance(adjudicator, str)
            or ID_RE.fullmatch(adjudicator) is None
            or adjudicator in reviewer_ids
            or not isinstance(reason, str)
            or REASON_CODE_RE.fullmatch(reason) is None
        ):
            invalid += 1
            continue
        decisions[rid] = {
            "adjudicated_label": label,
            "adjudicator_id": adjudicator,
            "reason_code": reason,
        }
    # 잘못 채운 행을 먼저 알리고, 아직 비어 있는 행은 그 다음에 알린다.
    if partial:
        raise DualReviewError(
            f"조정자 필드를 일부만 채운 행이 {partial}개 있습니다. "
            "adjudicated_label, adjudicator_id, reason_code를 모두 채우세요"
        )
    if invalid:
        raise DualReviewError(
            f"조정 값이 올바르지 않은 행이 {invalid}개 있습니다. "
            "조정 라벨은 true_positive, false_positive, uncertain 중 하나여야 하고, "
            "조정자 ID와 사유 코드는 공백 없는 짧은 코드여야 하며, "
            "조정자 ID는 --reviewer-id에 적은 검토자와 달라야 합니다"
        )
    if blank:
        raise DualReviewError(f"아직 조정되지 않은 행이 {blank}개 있습니다")
    return decisions


# ---------------------------------------------------------------------------
# 최종 라벨 파일
# ---------------------------------------------------------------------------


def build_merged(
    rows_a: Mapping[str, dict[str, Any]],
    rows_b: Mapping[str, dict[str, Any]],
    matched: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """합의 행과 조정 행을 합친 큐 형식 라벨 행과, 행별 출처 기록을 만든다."""
    merged: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    def add(rid: str, row: Mapping[str, Any], *, source: str, final: str) -> None:
        in_a, in_b = rid in rows_a, rid in rows_b
        decision = decisions.get(rid)
        merged.append({**row, "label": final})
        sources.append(
            {
                "review_id": rid,
                "rule_id": row["rule_id"],
                "source": source,
                "label_a": rows_a[rid]["label"] if in_a else None,
                "label_b": rows_b[rid]["label"] if in_b else None,
                "final_label": final,
                "adjudicator_id": decision["adjudicator_id"] if decision else None,
                "reason_code": decision["reason_code"] if decision else None,
            }
        )

    for rid in matched["compared"]:
        decision = decisions.get(rid)
        if decision is not None:
            add(rid, rows_a[rid], source="adjudicated", final=decision["adjudicated_label"])
        else:
            add(rid, rows_a[rid], source="agreed", final=rows_a[rid]["label"])
    for rid in matched["incomplete"]:
        add(rid, rows_a[rid], source="incomplete", final=UNLABELED)
    for rid, side in matched["singles"].items():
        row = (rows_a if side == "a" else rows_b)[rid]
        label = row["label"]
        add(
            rid,
            row,
            source="incomplete" if label == UNLABELED else "single_review",
            final=label,
        )
    order = sorted(range(len(merged)), key=lambda index: _row_key(merged[index]))
    return [merged[index] for index in order], [sources[index] for index in order]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------


def build_report(
    rows_a: Mapping[str, dict[str, Any]],
    rows_b: Mapping[str, dict[str, Any]],
    matched: Mapping[str, Any],
    statistics: Mapping[str, Any],
    thresholds: Mapping[str, int | float],
    *,
    disagreements: Sequence[str],
    decisions: Mapping[str, Mapping[str, str]] | None,
    merged_sha256: str | None,
    options: Mapping[str, bool],
) -> dict[str, Any]:
    alerts = statistics["alerts"]
    status = "alert" if any(alerts.values()) else "ok"
    by_rule = Counter(rows_a[rid]["rule_id"] for rid in disagreements)
    resolved = decisions is not None or not disagreements
    adjudicators = sorted({item["adjudicator_id"] for item in (decisions or {}).values()})
    final_counts = Counter(
        item["adjudicated_label"] for item in (decisions or {}).values()
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluation_phase": "train_review",
        "release_claim_eligible": False,
        "status": status,
        "merge_status": "complete" if resolved else "awaiting_adjudication",
        "thresholds": dict(thresholds),
        "options": dict(options),
        "label_scheme": {
            "labels": list(LABELS),
            "kappa": "categorical_cohen",
            "uncertain_is_a_category": True,
        },
        "files": {
            "a": {"rows": len(rows_a), "unreviewed": matched["unlabeled"]["a"]},
            "b": {"rows": len(rows_b), "unreviewed": matched["unlabeled"]["b"]},
        },
        "queue_fingerprint": {
            "a": matched["fingerprints"]["a"],
            "b": matched["fingerprints"]["b"],
            "compared_rows": matched["fingerprints"]["compared_scope"],
            "compared_rows_match": True,
        },
        "rows": {
            "union": len(rows_a.keys() | rows_b.keys()),
            "compared": len(matched["compared"]),
            "agreed": statistics["overall"]["agreed"],
            "disagreed": len(disagreements),
            "incomplete": len(matched["incomplete"]),
            "single_review": len(matched["singles"]),
        },
        "overall": statistics["overall"],
        "rules": statistics["rules"],
        "coverage_gap_rules": statistics["coverage_gap_rules"],
        "disagreements": {
            "count": len(disagreements),
            "review_ids": list(disagreements),
            "by_rule": {rule_id: by_rule[rule_id] for rule_id in sorted(by_rule)},
        },
        "adjudication": {
            "required_rows": len(disagreements),
            "provided": decisions is not None,
            "resolved_rows": len(decisions or {}),
            "adjudicator_ids": adjudicators,
            "final_label_counts": {
                label: final_counts[label] for label in LABELS
            },
        },
        "merged_labels_sha256": merged_sha256,
        "alerts": alerts,
        "interpretation": {
            "labels_are_rule_relevance_not_discrimination_or_illegality_determinations": True,
            "coverage_gap_rules_are_not_failures": True,
            "per_rule_gates_skip_coverage_gap_rules": True,
        },
        "privacy_boundary": {
            "contains_posting_text": False,
            "contains_review_ids": True,
            "contains_record_ids": False,
            "contains_source_ids": False,
            "contains_organization_identifiers": False,
            "contains_personal_identifiers": False,
            "keep_inside_private_review_directory": True,
        },
    }


def _message(report: Mapping[str, Any]) -> str:
    rows = report["rows"]
    overall = report["overall"]
    agreement = "없음" if overall["agreement"] is None else f"{overall['agreement']:.4f}"
    kappa = "계산 불가" if overall["kappa"] is None else f"{overall['kappa']:.4f}"
    parts = [
        f"비교 {rows['compared']}행, 일치율 {agreement}, 카파 {kappa}, "
        f"불일치 {rows['disagreed']}행"
    ]
    if report["coverage_gap_rules"]:
        parts.append(f"coverage_gap 규칙 {len(report['coverage_gap_rules'])}개")
    if report["status"] == "alert":
        parts.append("기준 미달 경보가 있습니다. 보고서의 alerts를 확인하세요")
    if report["merge_status"] == "awaiting_adjudication":
        parts.append(
            "조정 파일을 채운 뒤 --adjudication으로 다시 실행하면 최종 라벨 파일을 만듭니다"
        )
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def merge_dual_review(
    inputs_a: Sequence[Path],
    inputs_b: Sequence[Path],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    adjudication_path: Path | None = None,
    reviewer_ids: Sequence[str] = (),
    allow_partial: bool = False,
    allow_subset: bool = False,
    min_agreement: float = DEFAULT_MIN_AGREEMENT,
    min_kappa: float = DEFAULT_MIN_KAPPA,
    max_uncertain_share: float = DEFAULT_MAX_UNCERTAIN_SHARE,
    min_rows_per_rule: int = DEFAULT_MIN_ROWS_PER_RULE,
) -> dict[str, Any]:
    inputs_a = [Path(path) for path in inputs_a]
    inputs_b = [Path(path) for path in inputs_b]
    output_dir = Path(output_dir)
    adjudication_path = Path(adjudication_path) if adjudication_path is not None else None
    thresholds = _validate_thresholds(
        min_agreement, min_kappa, max_uncertain_share, min_rows_per_rule
    )
    for reviewer_id in reviewer_ids:
        if not isinstance(reviewer_id, str) or ID_RE.fullmatch(reviewer_id) is None:
            raise DualReviewError("--reviewer-id는 공백 없는 짧은 가명 ID여야 합니다")
    if not inputs_a or not inputs_b:
        raise DualReviewError("--input-a와 --input-b에 파일을 하나 이상 지정해야 합니다")
    validate_paths(inputs_a, inputs_b, output_dir, adjudication_path=adjudication_path)
    outputs = output_paths(output_dir)

    rules = _rule_metadata()
    rows_a = load_review_side(inputs_a, side="a", rules=rules)
    rows_b = load_review_side(inputs_b, side="b", rules=rules)
    matched = match_rows(
        rows_a, rows_b, allow_partial=allow_partial, allow_subset=allow_subset
    )
    statistics = build_statistics(rows_a, rows_b, matched, thresholds)
    disagreements = disagreement_ids(rows_a, rows_b, matched)
    template = build_adjudication_rows(disagreements, rows_a, rows_b)

    decisions: dict[str, dict[str, str]] | None = None
    if adjudication_path is not None:
        decisions = load_adjudication(
            adjudication_path, template, reviewer_ids=tuple(reviewer_ids)
        )
    else:
        _guard_existing_adjudication(outputs["adjudication"])

    merged_payload = sources_payload = None
    if decisions is not None or not disagreements:
        merged_rows, source_rows = build_merged(
            rows_a, rows_b, matched, decisions or {}
        )
        merged_payload = _jsonl(merged_rows)
        merged_sha256 = _sha256_text(merged_payload)
        source_counts = Counter(item["source"] for item in source_rows)
        sources_payload = (
            json.dumps(
                {
                    "schema_version": SOURCES_SCHEMA_VERSION,
                    "merged_labels_sha256": merged_sha256,
                    "row_count": len(source_rows),
                    "source_counts": {
                        source: source_counts[source]
                        for source in (
                            "agreed",
                            "adjudicated",
                            "single_review",
                            "incomplete",
                        )
                    },
                    "rows": source_rows,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    else:
        merged_sha256 = None

    report = build_report(
        rows_a,
        rows_b,
        matched,
        statistics,
        thresholds,
        disagreements=disagreements,
        decisions=decisions,
        merged_sha256=merged_sha256,
        options={"allow_partial": allow_partial, "allow_subset": allow_subset},
    )
    report["message"] = _message(report)

    try:
        if adjudication_path is None:
            atomic_write_text(outputs["adjudication"], _jsonl(template))
        if merged_payload is not None and sources_payload is not None:
            atomic_write_text(outputs["sources"], sources_payload)
            atomic_write_text(outputs["merged"], merged_payload)
        else:
            # 조정 전에는 이전 실행이 남긴 최종 파일이 현재 입력과 맞지 않는다.
            outputs["merged"].unlink(missing_ok=True)
            outputs["sources"].unlink(missing_ok=True)
        atomic_write_text(
            outputs["report"],
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    except OSError as exc:
        raise DualReviewError("출력 파일을 쓸 수 없습니다") from exc
    return report


def stdout_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """표준 출력용 요약. ID가 아닌 숫자와 규칙 ID만 담는다."""
    return {
        "agreement": report["overall"]["agreement"],
        "alert_kinds": sorted(kind for kind, items in report["alerts"].items() if items),
        "compared_rows": report["rows"]["compared"],
        "coverage_gap_rules": report["coverage_gap_rules"],
        "disagreement_count": report["disagreements"]["count"],
        "kappa": report["overall"]["kappa"],
        "kappa_reason_code": report["overall"]["kappa_reason_code"],
        "merge_status": report["merge_status"],
        "message": report["message"],
        "status": report["status"],
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse의 기본 종료 코드 2는 이 도구에서 경보를 뜻하므로 1로 바꾼다.
        self.print_usage(sys.stderr)
        print(f"오류: 명령줄 인자가 올바르지 않습니다 ({message})", file=sys.stderr)
        raise SystemExit(1)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _Parser(
        description=(
            "두 검토자의 민간 검토 큐 라벨 파일을 review_id로 맞춰 일치율과 카파를 "
            "계산하고, 불일치 행만 담은 조정 파일을 씁니다. 조정 파일을 채워 "
            "--adjudication으로 다시 실행하면 합의 행과 조정 행으로 이루어진 최종 라벨 "
            "파일을 만듭니다. 개인정보 경고: 조정 파일과 최종 라벨 파일에는 공고 문맥이 "
            "들어 있으므로 .private-review/ 안에서만 다루고 외부에 보내지 마십시오. "
            "표준 출력에는 ID, 규칙 ID, 숫자만 나옵니다."
        )
    )
    parser.add_argument(
        "--input-a",
        required=True,
        nargs="+",
        type=Path,
        help="검토자 A의 라벨 파일(JSONL). 여러 개를 주면 합쳐서 한쪽으로 봅니다",
    )
    parser.add_argument(
        "--input-b",
        required=True,
        nargs="+",
        type=Path,
        help="검토자 B의 라벨 파일(JSONL). 여러 개를 주면 합쳐서 한쪽으로 봅니다",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="출력 폴더. 기본값은 .private-review",
    )
    parser.add_argument(
        "--adjudication",
        type=Path,
        help="조정자가 채운 조정 파일. 주면 최종 라벨 파일을 만듭니다",
    )
    parser.add_argument(
        "--reviewer-id",
        action="append",
        default=[],
        help="검토자 가명 ID. 조정자 ID와 같으면 거부합니다. 여러 번 줄 수 있습니다",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="미검토 행을 허용합니다. 미검토 행은 일치율 계산에서 빠집니다",
    )
    parser.add_argument(
        "--allow-subset",
        action="store_true",
        help="한쪽 파일이 다른 쪽의 일부일 때 겹치는 행만 비교합니다",
    )
    parser.add_argument(
        "--min-agreement",
        type=float,
        default=DEFAULT_MIN_AGREEMENT,
        help="규칙별 일치율 하한 (기본 0.80)",
    )
    parser.add_argument(
        "--min-kappa",
        type=float,
        default=DEFAULT_MIN_KAPPA,
        help="전체 카파 하한 (기본 0.60)",
    )
    parser.add_argument(
        "--max-uncertain-share",
        type=float,
        default=DEFAULT_MAX_UNCERTAIN_SHARE,
        help="검토자별 uncertain 비율 상한 (기본 0.10)",
    )
    parser.add_argument(
        "--min-rows-per-rule",
        type=int,
        default=DEFAULT_MIN_ROWS_PER_RULE,
        help="이 값보다 비교 행이 적은 규칙은 coverage_gap으로 보고합니다 (기본 10)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = merge_dual_review(
            args.input_a,
            args.input_b,
            output_dir=args.output_dir,
            adjudication_path=args.adjudication,
            reviewer_ids=args.reviewer_id,
            allow_partial=args.allow_partial,
            allow_subset=args.allow_subset,
            min_agreement=args.min_agreement,
            min_kappa=args.min_kappa,
            max_uncertain_share=args.max_uncertain_share,
            min_rows_per_rule=args.min_rows_per_rule,
        )
    except DualReviewError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            stdout_summary(report),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 2 if report["status"] == "alert" else 0


if __name__ == "__main__":
    raise SystemExit(main())
