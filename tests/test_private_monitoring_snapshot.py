from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import build_private_monitoring_snapshot as snapshot


ROOT = Path(__file__).resolve().parents[1]


def posting(
    *,
    source_category: str = "company-career-page",
    source_url: str = "https://careers.example.com/jobs/1",
    published_at: str = "2026-07-01",
    organization: str = "Example Corp",
    text: str = "Backend engineer hiring",
) -> dict[str, str]:
    return {
        "source_category": source_category,
        "source_url": source_url,
        "published_at": published_at,
        "organization": organization,
        "text": text,
    }


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_records(output_dir: Path) -> list[dict[str, str]]:
    return [
        json.loads(line)
        for line in (output_dir / "train" / "records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def build(
    tmp_path: Path,
    rows: list[object],
    *,
    name: str = "run",
    exclude_manifests: tuple[Path, ...] = (),
) -> tuple[Path, Path, dict[str, object]]:
    input_path = tmp_path / f"{name}-input.jsonl"
    output_dir = tmp_path / f"{name}-output"
    summary_path = tmp_path / f"{name}-summary.json"
    write_jsonl(input_path, rows)
    result = snapshot.build_snapshot(
        input_path,
        output_dir,
        summary_path,
        exclude_manifests=exclude_manifests,
    )
    return output_dir, summary_path, result


def test_deterministic_for_repeated_and_reordered_input(tmp_path: Path) -> None:
    rows = [
        posting(
            source_category="work24",
            source_url="https://www.work24.go.kr/job/2",
            published_at="2026-07-03",
            text="Frontend engineer hiring",
        ),
        posting(
            source_category="licensed-feed",
            source_url="https://feed.example.net/jobs/1",
            published_at="2026-07-02",
            text="Data analyst hiring",
        ),
    ]
    first_dir, first_summary, _ = build(tmp_path, rows, name="first")
    second_dir, second_summary, _ = build(
        tmp_path, list(reversed(rows)), name="second"
    )

    for relative in (Path("train/records.jsonl"), Path("train/manifest.json")):
        assert (first_dir / relative).read_bytes() == (second_dir / relative).read_bytes()
    assert first_summary.read_bytes() == second_summary.read_bytes()


def test_deidentifies_text_and_writes_only_private_train_records(
    tmp_path: Path,
) -> None:
    secret_org = "Secret Tech"
    secret_name = "Kim Recruiter"
    secret_email = "recruit@example.com"
    secret_phone = "02-1234-5678"
    raw_text = f"{secret_org} / {secret_name} / {secret_email} / {secret_phone}"
    output_dir, _, _ = build(
        tmp_path,
        [posting(organization=secret_org, text=raw_text)],
    )

    assert sorted(path.name for path in output_dir.iterdir()) == ["train"]
    record = read_records(output_dir)[0]
    assert record["split"] == "train"
    assert record["sector"] == "private"
    assert record["source"] == "company-career-page"
    assert set(record) == {
        "id",
        "split",
        "sector",
        "source",
        "source_url",
        "published_at",
        "content_hash",
        "text",
    }
    for secret in (secret_org, secret_email, secret_phone):
        assert secret not in record["text"]
    assert record["content_hash"] == hashlib.sha256(
        record["text"].encode("utf-8")
    ).hexdigest()
    assert len(record["id"]) == 64

    manifest = json.loads(
        (output_dir / "train" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["split"] == "train"
    assert manifest["sector"] == "private"
    assert manifest["ids"] == [record["id"]]
    assert manifest["content_hashes"] == [record["content_hash"]]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_url", "http://careers.example.com/jobs/1"),
        ("source_url", "not-a-url"),
        ("published_at", "2026-02-30"),
        ("published_at", "2026/07/01"),
        ("source_category", "scraped-board"),
        ("organization", "  "),
        ("text", ""),
        ("source_url", ""),
    ],
)
def test_rejects_invalid_required_values_before_writing(
    tmp_path: Path, field: str, value: str
) -> None:
    row = posting()
    row[field] = value
    input_path = tmp_path / "invalid-input.jsonl"
    output_dir = tmp_path / "invalid-output"
    summary_path = tmp_path / "invalid-summary.json"
    write_jsonl(input_path, [row])

    with pytest.raises(ValueError):
        snapshot.build_snapshot(input_path, output_dir, summary_path)
    assert not output_dir.exists()
    assert not summary_path.exists()


@pytest.mark.parametrize("field", snapshot.REQUIRED_FIELDS)
def test_rejects_missing_required_fields(tmp_path: Path, field: str) -> None:
    row = posting()
    del row[field]
    input_path = tmp_path / "missing-input.jsonl"
    write_jsonl(input_path, [row])

    with pytest.raises(ValueError, match="missing required fields"):
        snapshot.build_snapshot(
            input_path,
            tmp_path / "missing-output",
            tmp_path / "missing-summary.json",
        )


@pytest.mark.parametrize("split", ["holdout", "dev", "evaluation", "test"])
def test_rejects_non_train_input_metadata(tmp_path: Path, split: str) -> None:
    row = posting()
    row["split"] = split
    input_path = tmp_path / "renamed-input.jsonl"
    write_jsonl(input_path, [row])

    with pytest.raises(ValueError, match="split.*train"):
        snapshot.build_snapshot(
            input_path,
            tmp_path / "safe-output",
            tmp_path / "safe-summary.json",
        )


def test_accepts_explicit_train_input_metadata(tmp_path: Path) -> None:
    row = posting()
    row["split"] = "train"
    output_dir, _, _ = build(tmp_path, [row])
    assert read_records(output_dir)[0]["split"] == "train"


@pytest.mark.parametrize(
    "forbidden", ["holdout", "hold-out", "test", "tests", "dev", "evaluation", "eval"]
)
def test_rejects_non_train_input_path_before_attempting_read(
    tmp_path: Path, forbidden: str
) -> None:
    missing_input = tmp_path / forbidden / "does-not-exist.jsonl"
    with pytest.raises(ValueError, match="forbidden evaluation partition name"):
        snapshot.build_snapshot(
            missing_input,
            tmp_path / "safe-output",
            tmp_path / "safe-summary.json",
        )


def test_rejects_non_train_output_or_summary_before_read(tmp_path: Path) -> None:
    missing_input = tmp_path / "does-not-exist.jsonl"
    with pytest.raises(ValueError, match="forbidden evaluation partition name"):
        snapshot.build_snapshot(
            missing_input,
            tmp_path / "holdout",
            tmp_path / "safe-summary.json",
        )
    with pytest.raises(ValueError, match="forbidden evaluation partition name"):
        snapshot.build_snapshot(
            missing_input,
            tmp_path / "safe-output",
            tmp_path / "evaluation" / "summary.json",
        )


@pytest.mark.parametrize("reserved", ["records.jsonl", "manifest.json"])
def test_rejects_summary_collision_with_snapshot_files_before_read(
    tmp_path: Path, reserved: str
) -> None:
    output_dir = tmp_path / "snapshot"
    with pytest.raises(
        ValueError, match="출력 경로 충돌|train 밖"
    ):
        snapshot.build_snapshot(
            tmp_path / "missing-input.jsonl",
            output_dir,
            output_dir / "train" / reserved,
        )


def test_rejects_input_or_exclude_collision_with_outputs_before_read(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "snapshot"
    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"

    with pytest.raises(ValueError, match="--input.*differ from every output path"):
        snapshot.build_snapshot(
            records_path,
            output_dir,
            tmp_path / "summary.json",
        )
    with pytest.raises(
        ValueError, match="--exclude-manifest.*differ from every output path"
    ):
        snapshot.build_snapshot(
            tmp_path / "missing-input.jsonl",
            output_dir,
            tmp_path / "summary.json",
            exclude_manifests=(manifest_path,),
        )


@pytest.mark.parametrize(
    ("target_factory", "expected"),
    [
        (lambda output_dir, summary_path: output_dir / "train" / "records.jsonl", "snapshot records"),
        (lambda output_dir, summary_path: summary_path, "--summary"),
    ],
)
def test_rejects_existing_input_hardlink_alias_without_overwriting_input(
    tmp_path: Path,
    target_factory: object,
    expected: str,
) -> None:
    input_path = tmp_path / "input.jsonl"
    output_dir = tmp_path / "snapshot"
    summary_path = tmp_path / "summary.json"
    write_jsonl(input_path, [posting()])
    target = target_factory(output_dir, summary_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(input_path, target)
    before = input_path.read_bytes()

    with pytest.raises(ValueError, match=rf"--input.*{re_escape(expected)}"):
        snapshot.build_snapshot(input_path, output_dir, summary_path)

    assert input_path.read_bytes() == before == target.read_bytes()


def test_rejects_existing_exclude_manifest_hardlink_alias_without_overwriting_source(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.jsonl"
    output_dir = tmp_path / "snapshot"
    summary_path = tmp_path / "summary.json"
    exclude_manifest = tmp_path / "exclude.json"
    write_jsonl(input_path, [posting()])
    exclude_manifest.write_text('{"content_hashes":[]}', encoding="utf-8")
    manifest_target = output_dir / "train" / "manifest.json"
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    os.link(exclude_manifest, manifest_target)
    before = exclude_manifest.read_bytes()

    with pytest.raises(ValueError, match=r"--exclude-manifest\[0\].*snapshot manifest"):
        snapshot.build_snapshot(
            input_path,
            output_dir,
            summary_path,
            exclude_manifests=(exclude_manifest,),
        )

    assert exclude_manifest.read_bytes() == before == manifest_target.read_bytes()


def test_deterministically_removes_duplicate_urls_and_content(tmp_path: Path) -> None:
    rows = [
        posting(
            source_url="https://careers.example.com/job/a#details",
            published_at="2026-07-02",
            text="Different body",
        ),
        posting(
            source_url="https://CAREERS.example.com:443/job/a",
            published_at="2026-07-01",
            text="Chosen body",
        ),
        posting(
            source_url="https://careers.example.com/job/b",
            published_at="2026-07-03",
            text="Chosen body",
        ),
    ]
    output_dir, _, summary = build(tmp_path, rows)
    reversed_dir, reversed_summary_path, _ = build(
        tmp_path, list(reversed(rows)), name="duplicates-reversed"
    )

    records = read_records(output_dir)
    assert len(records) == 1
    assert records[0]["text"] == "Chosen body"
    assert summary["counts"] == {
        "input": 3,
        "excluded_content_hash": 0,
        "duplicate_url": 1,
        "duplicate_content_hash": 1,
        "written": 1,
    }
    assert (output_dir / "train" / "records.jsonl").read_bytes() == (
        reversed_dir / "train" / "records.jsonl"
    ).read_bytes()
    assert (output_dir / "train" / "manifest.json").read_bytes() == (
        reversed_dir / "train" / "manifest.json"
    ).read_bytes()
    assert json.loads(reversed_summary_path.read_text(encoding="utf-8")) == summary


def test_strips_query_and_fragment_from_persisted_source_url(tmp_path: Path) -> None:
    secret = "signed-secret-123"
    output_dir, _, _ = build(
        tmp_path,
        [
            posting(
                source_url=(
                    "https://careers.example.com/job/1?token=" + secret + "#details"
                )
            )
        ],
    )

    record = read_records(output_dir)[0]
    assert record["source_url"] == "https://careers.example.com/job/1"
    assert secret not in (output_dir / "train" / "records.jsonl").read_text(
        encoding="utf-8"
    )


def test_repeated_exclude_manifests_remove_existing_content_hashes(
    tmp_path: Path,
) -> None:
    rows = [
        posting(source_url="https://careers.example.com/1", text="First posting"),
        posting(source_url="https://careers.example.com/2", text="Second posting"),
        posting(source_url="https://careers.example.com/3", text="Third posting"),
    ]
    hashes = [
        hashlib.sha256(
            snapshot._clean_text(row["text"], row["organization"]).encode("utf-8")
        ).hexdigest()
        for row in rows
    ]
    first_manifest = tmp_path / "first-manifest.json"
    second_manifest = tmp_path / "second-manifest.json"
    first_manifest.write_text(
        json.dumps({"content_hashes": [hashes[0]]}), encoding="utf-8"
    )
    second_manifest.write_text(
        json.dumps({"content_hashes": [hashes[1]]}), encoding="utf-8"
    )

    output_dir, _, summary = build(
        tmp_path,
        rows,
        exclude_manifests=(first_manifest, second_manifest),
    )
    records = read_records(output_dir)
    assert [record["content_hash"] for record in records] == [hashes[2]]
    assert summary["counts"]["excluded_content_hash"] == 2


def test_anonymous_summary_contains_only_aggregate_allowed_data(
    tmp_path: Path,
) -> None:
    secrets = {
        "organization": "Private Holdings",
        "name": "Hidden Name",
        "email": "private.person@example.com",
        "phone": "010-9876-5432",
        "url": "https://careers.example.com/secret-posting-777",
        "raw": "unique internal sentence",
    }
    row = posting(
        organization=secrets["organization"],
        source_url=secrets["url"],
        text=(
            f"{secrets['raw']} {secrets['organization']} {secrets['name']} "
            f"{secrets['email']} {secrets['phone']}"
        ),
    )
    output_dir, summary_path, summary = build(tmp_path, [row])
    record = read_records(output_dir)[0]
    serialized = summary_path.read_text(encoding="utf-8")

    assert set(summary) == {
        "source_categories",
        "date_range",
        "counts",
        "snapshot_hash",
        "privacy",
    }
    for secret in (*secrets.values(), record["id"], record["content_hash"]):
        assert secret not in serialized
    assert summary["privacy"] == {
        "train_only": True,
        "sector_private_only": True,
        "shared_deidentify_applied": True,
        "declared_organization_removed": True,
        "raw_text_omitted": True,
        "source_url_omitted": True,
        "record_ids_omitted": True,
        "pii_omitted": True,
    }


def test_publish_failure_preserves_existing_snapshot_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input.jsonl"
    output_dir = tmp_path / "snapshot"
    summary_path = tmp_path / "summary.json"
    write_jsonl(input_path, [posting(text="new posting")])

    records_path = output_dir / "train" / "records.jsonl"
    manifest_path = output_dir / "train" / "manifest.json"
    targets = (records_path, manifest_path, summary_path)
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"old-{index}", encoding="utf-8")

    original_replace = snapshot.os.replace
    calls = 0

    def fail_second_publish(source: object, target: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("simulated publish failure")
        original_replace(source, target)

    monkeypatch.setattr(snapshot.os, "replace", fail_second_publish)

    with pytest.raises(ValueError, match="could not be published"):
        snapshot.build_snapshot(input_path, output_dir, summary_path)

    assert [target.read_text(encoding="utf-8") for target in targets] == [
        "old-0",
        "old-1",
        "old-2",
    ]
    assert not list(tmp_path.rglob("*.fairpost-backup-*"))
    assert not list(tmp_path.rglob("*.fairpost-stage-*"))


def test_cli_builds_snapshot_with_repeated_excludes(tmp_path: Path) -> None:
    input_path = tmp_path / "cli-input.jsonl"
    output_dir = tmp_path / "cli-output"
    summary_path = tmp_path / "cli-summary.json"
    manifest = tmp_path / "empty-manifest.json"
    write_jsonl(input_path, [posting()])
    manifest.write_text('{"content_hashes": []}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "build_private_monitoring_snapshot.py"),
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--summary",
            str(summary_path),
            "--exclude-manifest",
            str(manifest),
            "--exclude-manifest",
            str(manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert (output_dir / "train" / "records.jsonl").is_file()
    assert (output_dir / "train" / "manifest.json").is_file()
    assert summary_path.is_file()
    assert not (output_dir / "holdout").exists()


def re_escape(value: str) -> str:
    import re

    return re.escape(value)
