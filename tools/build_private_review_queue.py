from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402
from core.morph import normalize  # noqa: E402
from tools.collect_corpus import EMAIL_RE, LABELED_NAME_RE, PHONE_RE  # noqa: E402


FORBIDDEN_PATH_PARTS = frozenset(
    {"holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"}
)
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
URL_RE = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
ALLOWED_LABELS = ("true_positive", "false_positive", "uncertain")
OUTPUT_FIELDS = frozenset(
    {
        "review_id",
        "rule_id",
        "layer",
        "dimension",
        "context",
        "matched_text",
        "section",
        "label",
        "allowed_labels",
    }
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def _same_existing_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def queue_manifest_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(f"{path.name}.manifest.json")


def _path_variants(path: Path) -> tuple[Path, ...]:
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve(strict=False)
    except OSError:
        return (expanded,)
    return (expanded, resolved)


def _path_has_component(path: Path, names: frozenset[str]) -> str | None:
    for variant in _path_variants(path):
        for part in variant.parts:
            lowered = part.casefold()
            if lowered in names:
                return lowered
            stem = Path(part).stem.casefold()
            if stem in names:
                return stem
    return None


def _has_train_component(path: Path) -> bool:
    for variant in _path_variants(path):
        for part in variant.parts:
            if part.casefold() == "train" or Path(part).stem.casefold() == "train":
                return True
    return False


def validate_paths(input_path: Path, output_path: Path) -> None:
    """Validate the complete path boundary before opening the input."""
    manifest_path = queue_manifest_path(output_path)
    for argument, path in (
        ("--input", input_path),
        ("--output", output_path),
        ("queue manifest", manifest_path),
    ):
        forbidden = _path_has_component(path, FORBIDDEN_PATH_PARTS)
        if forbidden is not None:
            raise ValueError(
                f"{argument} 경로에는 비학습 분할 이름을 사용할 수 없습니다: "
                f"{forbidden}"
            )
    if not _has_train_component(input_path):
        raise ValueError("--input 경로에는 train 분할이 명시되어야 합니다")

    if _path_key(input_path) == _path_key(output_path):
        raise ValueError("--input과 --output은 서로 다른 파일이어야 합니다")
    if _path_key(input_path) == _path_key(manifest_path):
        raise ValueError("--input과 queue manifest는 서로 다른 파일이어야 합니다")
    if _same_existing_file(input_path, output_path):
        raise ValueError("--input과 --output은 서로 다른 파일이어야 합니다")
    if _same_existing_file(input_path, manifest_path):
        raise ValueError("--input과 queue manifest는 서로 다른 파일이어야 합니다")
    if _same_existing_file(output_path, manifest_path):
        raise ValueError("--output과 queue manifest는 서로 다른 파일이어야 합니다")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("--output은 파일 경로여야 합니다")
    if manifest_path.exists() and manifest_path.is_dir():
        raise ValueError("queue manifest는 파일 경로여야 합니다")


def load_private_train_records_bytes(
    payload: bytes, *, source_path: Path
) -> list[dict[str, Any]]:
    """Parse one captured private-train payload after enforcing its path boundary."""
    path = Path(source_path)
    forbidden = _path_has_component(path, FORBIDDEN_PATH_PARTS)
    if forbidden is not None:
        raise ValueError("민간 검토 큐는 train 경로만 읽을 수 있습니다")
    if not _has_train_component(path):
        raise ValueError("민간 검토 큐 입력 경로에는 train 분할이 필요합니다")

    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("민간 검토 큐 입력은 유효한 UTF-8이어야 합니다") from exc

    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        if not raw_line.strip():
            continue
        location = f"line {line_number}"
        try:
            value = json.loads(raw_line, object_pairs_hook=_unique_object)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{location}: JSON 형식 오류") from exc
        except ValueError as exc:
            raise ValueError(f"{location}: 중복 JSON 필드") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{location}: JSON 객체가 필요합니다")
        text = value.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{location}: 비어 있지 않은 text 문자열이 필요합니다")
        if value.get("sector") != "private":
            raise ValueError(f"{location}: sector=private 레코드만 허용합니다")
        if "split" in value and value.get("split") != "train":
            raise ValueError(f"{location}: split이 있으면 train이어야 합니다")
        content_hash = value.get("content_hash")
        if not isinstance(content_hash, str) or not SHA256_RE.fullmatch(
            content_hash
        ):
            raise ValueError(f"{location}: content_hash는 SHA-256이어야 합니다")
        actual_hash = _sha256(text)
        if content_hash.casefold() != actual_hash:
            raise ValueError(f"{location}: text와 content_hash가 일치하지 않습니다")
        records.append(value)
    return records


def load_private_train_records(path: Path) -> list[dict[str, Any]]:
    """Load a private train JSONL for callers outside the atomic build path."""
    path = Path(path)
    return load_private_train_records_bytes(path.read_bytes(), source_path=path)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


Replacement = str | Callable[[re.Match[str]], str]


def _replace_and_track(
    text: str,
    pattern: re.Pattern[str],
    replacement: Replacement,
    start: int,
    end: int,
) -> tuple[str, int, int]:
    """Apply substitutions while keeping one source span mapped to the result."""
    position = 0
    while True:
        match = pattern.search(text, position)
        if match is None:
            break
        replacement_text = (
            replacement(match) if callable(replacement) else replacement
        )
        match_start, match_end = match.span()
        difference = len(replacement_text) - (match_end - match_start)

        if start >= match_end:
            start += difference
        elif start > match_start:
            start = match_start
        if end >= match_end:
            end += difference
        elif end > match_start:
            end = match_start + len(replacement_text)

        text = text[:match_start] + replacement_text + text[match_end:]
        position = match_start + len(replacement_text)
    return text, start, end


def deidentify_with_span(
    text: str,
    organization: str | None,
    start: int,
    end: int,
) -> tuple[str, tuple[int, int]]:
    """Re-deidentify a normalized posting and preserve the engine match span."""
    if organization and organization.strip():
        organization_re = re.compile(re.escape(organization.strip()), re.IGNORECASE)
        text, start, end = _replace_and_track(
            text, organization_re, "[ORGANIZATION]", start, end
        )
    text, start, end = _replace_and_track(text, URL_RE, "[URL]", start, end)
    text, start, end = _replace_and_track(
        text, EMAIL_RE, "[EMAIL]", start, end
    )
    text, start, end = _replace_and_track(
        text, PHONE_RE, "[PHONE]", start, end
    )
    text, start, end = _replace_and_track(
        text,
        LABELED_NAME_RE,
        lambda match: f"{match.group('label')}: [CONTACT]",
        start,
        end,
    )
    return text, (start, end)


def _eligible_rules(engine: FairpostEngine) -> dict[str, dict[str, Any]]:
    return {
        str(rule["id"]): rule
        for rule in engine.ruleset.rules
        if rule["layer"] in {"law", "question"}
        and rule["trigger"]["type"] == "presence"
    }


def _validate_queue_options(per_rule: object, context_chars: object) -> tuple[int, int]:
    if isinstance(per_rule, bool) or not isinstance(per_rule, int) or per_rule < 1:
        raise ValueError("--per-rule은 1 이상의 정수여야 합니다")
    if (
        isinstance(context_chars, bool)
        or not isinstance(context_chars, int)
        or context_chars < 0
    ):
        raise ValueError("--context-chars는 0 이상의 정수여야 합니다")
    return per_rule, context_chars


def select_rules(
    engine: FairpostEngine, requested_rule_ids: Sequence[str]
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    eligible = _eligible_rules(engine)
    all_rules = {str(rule["id"]): rule for rule in engine.ruleset.rules}
    if not requested_rule_ids:
        return sorted(eligible), eligible

    selected: list[str] = []
    seen: set[str] = set()
    for rule_id in requested_rule_ids:
        if rule_id in seen:
            raise ValueError(f"중복 --rule-id: {rule_id}")
        seen.add(rule_id)
        rule = all_rules.get(rule_id)
        if rule is None:
            raise ValueError(f"알 수 없는 규칙 ID: {rule_id}")
        if rule_id not in eligible:
            raise ValueError(f"presence law/question 규칙이 아닙니다: {rule_id}")
        selected.append(rule_id)
    return sorted(selected), eligible


def _candidate(
    *,
    rule: dict[str, Any],
    content_hash: str,
    source: str,
    organization: str | None,
    matched_text: str,
    offset: tuple[int, int],
    section: str | None,
    context_chars: int,
) -> dict[str, Any]:
    clean_source, clean_offset = deidentify_with_span(
        source, organization, offset[0], offset[1]
    )
    clean_start, clean_end = clean_offset
    context_start = max(0, clean_start - context_chars)
    context_end = min(len(clean_source), clean_end + context_chars)
    context = clean_source[context_start:context_end]
    clean_match = clean_source[clean_start:clean_end]
    # A defensive fallback covers malformed/custom engine offsets without ever
    # copying the unsanitized match into the queue.
    if not clean_match:
        clean_match, _ = deidentify_with_span(
            matched_text, organization, 0, len(matched_text)
        )

    review_id = _sha256("\0".join((str(rule["id"]), content_hash, context)))
    row = {
        "review_id": review_id,
        "rule_id": str(rule["id"]),
        "layer": str(rule["layer"]),
        "dimension": str(rule["dimension"]),
        "context": context,
        "matched_text": clean_match,
        "section": section,
        "label": "unreviewed",
        "allowed_labels": list(ALLOWED_LABELS),
    }
    assert set(row) == OUTPUT_FIELDS
    return row


def build_queue_rows(
    records: Sequence[dict[str, Any]],
    engine: FairpostEngine,
    *,
    rule_ids: Sequence[str] = (),
    per_rule: int = 20,
    context_chars: int = 120,
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
    dict[str, dict[str, int]],
]:
    per_rule, context_chars = _validate_queue_options(per_rule, context_chars)

    selected_ids, eligible = select_rules(engine, rule_ids)
    selected = set(selected_ids)
    candidates: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)

    for record in records:
        text = str(record["text"])
        content_hash = str(record["content_hash"]).casefold()
        organization_value = record.get("organization")
        organization = (
            organization_value if isinstance(organization_value, str) else None
        )
        source = normalize(text)
        result = engine.check(text)

        matches: list[Any] = [*result.findings, *result.questions]
        for match in matches:
            rule_id = str(match.id)
            if rule_id not in selected or not match.matched_text or match.offset is None:
                continue
            rule = eligible[rule_id]
            row = _candidate(
                rule=rule,
                content_hash=content_hash,
                source=source,
                organization=organization,
                matched_text=str(match.matched_text),
                offset=(int(match.offset[0]), int(match.offset[1])),
                section=match.section,
                context_chars=context_chars,
            )
            rank = _sha256("\0".join((rule_id, content_hash)))
            candidates[rule_id].append((rank, content_hash, row))

    output: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    sampling: dict[str, dict[str, int]] = {}
    for rule_id in selected_ids:
        unique_contexts: set[str] = set()
        chosen: list[dict[str, Any]] = []
        for _rank, _content_hash, row in sorted(
            candidates[rule_id], key=lambda item: (item[0], item[1], item[2]["review_id"])
        ):
            context = str(row["context"])
            if context in unique_contexts:
                continue
            unique_contexts.add(context)
            if len(chosen) < per_rule:
                chosen.append(row)
        counts[rule_id] = len(chosen)
        candidate_matches = len(candidates[rule_id])
        unique_context_count = len(unique_contexts)
        sampling[rule_id] = {
            "candidate_matches": candidate_matches,
            "unique_contexts": unique_context_count,
            "selected_rows": len(chosen),
            "collapsed_duplicate_contexts": candidate_matches
            - unique_context_count,
            "truncated_unique_contexts": unique_context_count - len(chosen),
        }
        output.extend(chosen)
    return output, counts, sampling


def immutable_rows_sha256(rows: Sequence[dict[str, Any]]) -> str:
    """Hash every immutable queue field while allowing only label edits."""

    immutable_rows = [
        {key: row[key] for key in sorted(row) if key != "label"}
        for row in sorted(rows, key=lambda item: str(item["review_id"]))
    ]
    payload = json.dumps(
        immutable_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def build_queue_manifest(
    rows: Sequence[dict[str, Any]],
    *,
    engine: FairpostEngine,
    input_sha256: str,
    selected_rule_ids: Sequence[str],
    per_rule: int,
    context_chars: int,
    rule_sampling: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """Build a text-free manifest used to verify later label-only edits."""

    return {
        "schema_version": "private-review-queue-manifest-v1",
        "ruleset_version": engine.ruleset.version,
        "matching_version": engine.ruleset.matching_version,
        "input_sha256": input_sha256,
        "selected_rule_ids": sorted(str(rule_id) for rule_id in selected_rule_ids),
        "per_rule": per_rule,
        "context_chars": context_chars,
        "row_count": len(rows),
        "rule_sampling": {
            rule_id: dict(rule_sampling[rule_id])
            for rule_id in sorted(rule_sampling)
        },
        "immutable_rows_sha256": immutable_rows_sha256(rows),
    }


def _stage_text(target: Path, payload: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.fairpost-temp-{uuid4().hex}")
    staged.write_text(payload, encoding="utf-8", newline="\n")
    return staged


def _publish_files(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Publish queue and manifest as one recoverable generation."""

    token = uuid4().hex
    backups: dict[Path, Path | None] = {}
    try:
        for _source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
        for _source, target in pairs:
            backup: Path | None = None
            if target.exists():
                backup = target.with_name(f".{target.name}.fairpost-backup-{token}")
                os.replace(target, backup)
            backups[target] = backup
        for source, target in pairs:
            os.replace(source, target)
    except OSError as exc:
        rollback_failed = False
        for _source, target in reversed(pairs):
            if target not in backups:
                continue
            backup = backups[target]
            try:
                if target.exists():
                    target.unlink()
                if backup is not None and backup.exists():
                    os.replace(backup, target)
            except OSError:
                rollback_failed = True
        message = "검토 큐와 manifest를 게시할 수 없습니다"
        if rollback_failed:
            message += ": rollback 실패"
        raise ValueError(message) from exc
    else:
        for backup in backups.values():
            if backup is not None:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass


def build_review_queue(
    input_path: Path,
    output_path: Path,
    *,
    rule_ids: Sequence[str] = (),
    per_rule: int = 20,
    context_chars: int = 120,
    engine: FairpostEngine | None = None,
) -> dict[str, int]:
    per_rule, context_chars = _validate_queue_options(per_rule, context_chars)
    input_path = Path(input_path)
    output_path = Path(output_path)
    validate_paths(input_path, output_path)

    checker = engine if engine is not None else FairpostEngine()
    # Validate rule IDs before opening even an otherwise valid train input.
    selected_rule_ids, _eligible = select_rules(checker, rule_ids)
    # Capture once and parse exactly those bytes. Reopening by path would permit
    # an A -> B -> A replacement to separate the recorded hash from analyzed rows.
    input_bytes = input_path.read_bytes()
    records = load_private_train_records_bytes(input_bytes, source_path=input_path)
    rows, counts, sampling = build_queue_rows(
        records,
        checker,
        rule_ids=rule_ids,
        per_rule=per_rule,
        context_chars=context_chars,
    )

    input_sha256 = _sha256_bytes(input_bytes)
    manifest = build_queue_manifest(
        rows,
        engine=checker,
        input_sha256=input_sha256,
        selected_rule_ids=selected_rule_ids,
        per_rule=per_rule,
        context_chars=context_chars,
        rule_sampling=sampling,
    )
    queue_payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    manifest_path = queue_manifest_path(output_path)
    staged_pairs: list[tuple[Path, Path]] = []
    try:
        staged_pairs.append((_stage_text(output_path, queue_payload), output_path))
        staged_pairs.append((_stage_text(manifest_path, manifest_payload), manifest_path))
        _publish_files(staged_pairs)
    finally:
        for staged_path, _target in staged_pairs:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass
    return counts


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "민간 train-only 스냅샷에서 규칙 정밀도 사람 검토용 로컬 JSONL "
            "큐를 만듭니다. 개인정보 경고: 문맥은 조직명·이메일·전화·담당자명을 "
            "다시 비식별화하지만 재식별 위험이 남을 수 있으므로 결과를 로컬에서만 "
            "취급하고 외부에 공유하지 마십시오. 네트워크 전송은 수행하지 않습니다."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rule-id", action="append", default=[])
    parser.add_argument("--per-rule", type=int, default=20)
    parser.add_argument("--context-chars", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        counts = build_review_queue(
            args.input,
            args.output,
            rule_ids=args.rule_id,
            per_rule=args.per_rule,
            context_chars=args.context_chars,
        )
    except (OSError, UnicodeError, ValueError):
        print("error: 민간 검토 큐를 생성할 수 없습니다", file=sys.stderr)
        return 1
    # Stdout is intentionally restricted to aggregate per-rule queue counts.
    print(
        json.dumps(
            counts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
