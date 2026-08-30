from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collect_corpus import deidentify  # noqa: E402


SOURCE_CATEGORIES = frozenset({"company-career-page", "work24", "licensed-feed"})
REQUIRED_FIELDS = (
    "source_category",
    "source_url",
    "published_at",
    "organization",
    "text",
)
FORBIDDEN_PATH_PARTS = frozenset(
    {"holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"}
)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _path_variants(path: Path) -> tuple[Path, ...]:
    """Return lexical and resolved variants without requiring the path to exist."""
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve(strict=False)
    except OSError:
        return (expanded,)
    return (expanded, resolved)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def _same_existing_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def reject_non_train_path(path: Path, *, argument: str) -> None:
    """Reject paths explicitly named like evaluation partitions."""
    for variant in _path_variants(path):
        for part in variant.parts:
            lowered = part.casefold()
            if lowered in FORBIDDEN_PATH_PARTS:
                raise ValueError(
                    f"{argument} path uses a forbidden evaluation partition name: {lowered}"
                )
            stem = Path(part).stem.casefold()
            if stem in FORBIDDEN_PATH_PARTS:
                raise ValueError(
                    f"{argument} path uses a forbidden evaluation partition name: {stem}"
                )


def validate_paths(
    input_path: Path,
    output_dir: Path,
    summary_path: Path,
    exclude_manifests: Sequence[Path],
) -> None:
    """Perform every path-policy check before any input is opened."""
    reject_non_train_path(input_path, argument="--input")
    reject_non_train_path(output_dir, argument="--output-dir")
    reject_non_train_path(summary_path, argument="--summary")
    for path in exclude_manifests:
        reject_non_train_path(path, argument="--exclude-manifest")

    train_dir = (output_dir / "train").resolve(strict=False)
    records_path = train_dir / "records.jsonl"
    manifest_path = train_dir / "manifest.json"
    summary_resolved = summary_path.resolve(strict=False)
    output_resolved = output_dir.resolve(strict=False)
    input_resolved = input_path.resolve(strict=False)
    write_paths = {
        "snapshot records": records_path,
        "snapshot manifest": manifest_path,
        "--summary": summary_resolved,
    }

    keys: dict[str, str] = {}
    for label, path in write_paths.items():
        key = _path_key(path)
        if key in keys:
            raise ValueError(f"출력 경로 충돌: {keys[key]}, {label}")
        if path.exists() and path.is_dir():
            raise ValueError(f"{label} must be a file path")
        keys[key] = label

    if summary_resolved == train_dir or train_dir in summary_resolved.parents:
        raise ValueError("--summary는 output-dir/train 밖에 있어야 합니다")
    if input_resolved == output_resolved or _path_key(input_path) in keys:
        raise ValueError("--input must differ from every output path")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("--output-dir must be a directory path")

    read_paths = {
        "--input": input_path,
        **{
            f"--exclude-manifest[{index}]": path
            for index, path in enumerate(exclude_manifests)
        },
    }
    for read_label, read_path in read_paths.items():
        if _path_key(read_path) in keys:
            raise ValueError(f"{read_label} must differ from every output path")
        for write_label, write_path in write_paths.items():
            if _same_existing_file(read_path, write_path):
                raise ValueError(f"{read_label} must differ from {write_label}")

    write_items = list(write_paths.items())
    for index, (left_label, left_path) in enumerate(write_items):
        for right_label, right_path in write_items[index + 1 :]:
            if _same_existing_file(left_path, right_path):
                raise ValueError(f"출력 경로 충돌: {left_label}, {right_label}")


def _required_string(value: dict[str, Any], field: str, location: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{location}: {field} must be a non-empty string")
    return raw.strip()


def _validate_date(raw: str, location: str) -> str:
    if not DATE_RE.fullmatch(raw):
        raise ValueError(f"{location}: published_at must use YYYY-MM-DD")
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{location}: published_at must be a valid calendar date") from exc
    return raw


def _validate_https_url(raw: str, location: str) -> tuple[str, str]:
    if any(character.isspace() or ord(character) < 32 for character in raw):
        raise ValueError(f"{location}: source_url must be a valid HTTPS URL")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{location}: source_url must be a valid HTTPS URL") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{location}: source_url must be a valid HTTPS URL")

    hostname = parsed.hostname.casefold()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    canonical_netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    canonical = urlunsplit(
        SplitResult(
            scheme="https",
            netloc=canonical_netloc,
            path=parsed.path or "/",
            query=parsed.query,
            fragment="",
        )
    )
    return raw, canonical


def _clean_text(text: str, organization: str) -> str:
    clean = deidentify(text, organization)
    if organization.casefold() in clean.casefold():
        clean = re.sub(
            re.escape(organization),
            "[ORGANIZATION]",
            clean,
            flags=re.IGNORECASE,
        )
    return clean


def _record_from_value(value: Any, location: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{location}: each JSONL row must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if field not in value]
    if missing:
        raise ValueError(f"{location}: missing required fields: {', '.join(missing)}")

    source = _required_string(value, "source_category", location)
    if source not in SOURCE_CATEGORIES:
        raise ValueError(f"{location}: source_category is not approved")
    _source_url, canonical_url = _validate_https_url(
        _required_string(value, "source_url", location), location
    )
    published_at = _validate_date(
        _required_string(value, "published_at", location), location
    )
    organization = _required_string(value, "organization", location)
    text = _required_string(value, "text", location)
    if "split" in value and value.get("split") != "train":
        raise ValueError(f"{location}: split must be train when present")
    clean_text = _clean_text(text, organization)
    content_hash = _sha256(clean_text)
    identity_material = "\0".join((source, canonical_url, published_at))
    public_url = urlunsplit(urlsplit(canonical_url)._replace(query="", fragment=""))

    return {
        "id": _sha256(identity_material),
        "split": "train",
        "sector": "private",
        "source": source,
        "source_url": public_url,
        "published_at": published_at,
        "content_hash": content_hash,
        "text": clean_text,
        "_canonical_url": canonical_url,
    }


def load_input(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            location = f"{path}:{line_number}"
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{location}: invalid JSON") from exc
            records.append(_record_from_value(value, location))
    return records


def load_excluded_hashes(paths: Iterable[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: manifest must be valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("content_hashes"), list):
            raise ValueError(f"{path}: manifest must contain a content_hashes list")
        for item in payload["content_hashes"]:
            if not isinstance(item, str) or not SHA256_RE.fullmatch(item):
                raise ValueError(f"{path}: content_hashes must contain SHA-256 digests")
            excluded.add(item.casefold())
    return excluded


def _deduplicate(
    candidates: list[dict[str, str]], excluded_hashes: set[str]
) -> tuple[list[dict[str, str]], dict[str, int]]:
    excluded_count = sum(
        record["content_hash"] in excluded_hashes for record in candidates
    )
    remaining = [
        record
        for record in candidates
        if record["content_hash"] not in excluded_hashes
    ]
    remaining.sort(
        key=lambda record: (
            record["_canonical_url"],
            record["source"],
            record["published_at"],
            record["content_hash"],
            record["id"],
            record["source_url"],
        )
    )

    unique_urls: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    duplicate_urls = 0
    for record in remaining:
        canonical_url = record["_canonical_url"]
        if canonical_url in seen_urls:
            duplicate_urls += 1
            continue
        seen_urls.add(canonical_url)
        unique_urls.append(record)

    unique_hashes: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    duplicate_hashes = 0
    for record in unique_urls:
        content_hash = record["content_hash"]
        if content_hash in seen_hashes:
            duplicate_hashes += 1
            continue
        seen_hashes.add(content_hash)
        unique_hashes.append(record)

    for record in unique_hashes:
        del record["_canonical_url"]
    unique_hashes.sort(key=lambda record: record["id"])
    return unique_hashes, {
        "input": len(candidates),
        "excluded_content_hash": excluded_count,
        "duplicate_url": duplicate_urls,
        "duplicate_content_hash": duplicate_hashes,
        "written": len(unique_hashes),
    }


def _jsonl_bytes(records: Sequence[dict[str, str]]) -> bytes:
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _source_counts(records: Sequence[dict[str, str]]) -> dict[str, int]:
    counts = Counter(record["source"] for record in records)
    return {source: counts[source] for source in sorted(counts)}


def _manifest(
    records: Sequence[dict[str, str]], records_sha256: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "split": "train",
        "sector": "private",
        "count": len(records),
        "ids": [record["id"] for record in records],
        "content_hashes": sorted(record["content_hash"] for record in records),
        "source_categories": _source_counts(records),
        "records_sha256": records_sha256,
    }


def _summary(
    records: Sequence[dict[str, str]],
    counts: dict[str, int],
    snapshot_hash: str,
) -> dict[str, Any]:
    published_dates = sorted(record["published_at"] for record in records)
    return {
        "source_categories": _source_counts(records),
        "date_range": {
            "from": published_dates[0] if published_dates else None,
            "to": published_dates[-1] if published_dates else None,
        },
        "counts": counts,
        "snapshot_hash": snapshot_hash,
        "privacy": {
            "train_only": True,
            "sector_private_only": True,
            "shared_deidentify_applied": True,
            "declared_organization_removed": True,
            "raw_text_omitted": True,
            "source_url_omitted": True,
            "record_ids_omitted": True,
            "pii_omitted": True,
        },
    }


def _stage_bytes(target: Path, payload: bytes) -> Path:
    staged_path = target.with_name(f".{target.name}.fairpost-stage-{uuid4().hex}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged_path.write_bytes(payload)
    return staged_path


def _stage_text(target: Path, payload: str) -> Path:
    staged_path = target.with_name(f".{target.name}.fairpost-stage-{uuid4().hex}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged_path.write_text(payload, encoding="utf-8", newline="\n")
    return staged_path


def _publish_files(pairs: Sequence[tuple[Path, Path]]) -> None:
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
        message = "snapshot outputs could not be published"
        if rollback_failed:
            message += ": rollback also failed"
        raise ValueError(message) from exc
    else:
        for backup in backups.values():
            if backup is not None:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass


def build_snapshot(
    input_path: Path,
    output_dir: Path,
    summary_path: Path,
    *,
    exclude_manifests: Sequence[Path] = (),
) -> dict[str, Any]:
    """Build a deterministic private-sector, train-only monitoring snapshot."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    summary_path = Path(summary_path)
    exclude_paths = tuple(Path(path) for path in exclude_manifests)

    validate_paths(input_path, output_dir, summary_path, exclude_paths)
    candidates = load_input(input_path)
    excluded_hashes = load_excluded_hashes(exclude_paths)
    records, counts = _deduplicate(candidates, excluded_hashes)
    records_bytes = _jsonl_bytes(records)
    snapshot_hash = _sha256(records_bytes)
    manifest = _manifest(records, snapshot_hash)
    summary = _summary(records, counts, snapshot_hash)

    train_dir = output_dir / "train"
    records_path = train_dir / "records.jsonl"
    manifest_path = train_dir / "manifest.json"
    manifest_text = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    summary_text = (
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )

    staged_pairs: list[tuple[Path, Path]] = []
    try:
        staged_pairs.append((_stage_bytes(records_path, records_bytes), records_path))
        staged_pairs.append((_stage_text(manifest_path, manifest_text), manifest_path))
        staged_pairs.append((_stage_text(summary_path, summary_text), summary_path))
        _publish_files(staged_pairs)
    finally:
        for staged_path, _target in staged_pairs:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "De-identify public private-sector hiring JSONL into a deterministic "
            "train-only monitoring snapshot."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--exclude-manifest",
        type=Path,
        action="append",
        default=[],
        help="Existing manifest whose content_hashes should be excluded.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = build_snapshot(
            args.input,
            args.output_dir,
            args.summary,
            exclude_manifests=args.exclude_manifest,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"private train-only monitoring snapshot created: written={summary['counts']['written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
