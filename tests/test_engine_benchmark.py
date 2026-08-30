from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.benchmark_engine import (
    build_report,
    load_train_texts,
    measure_engine,
    validate_output_path,
)


class FakeEngine:
    def __init__(self) -> None:
        self.ruleset = SimpleNamespace(
            version="rules-test-version",
            matching_version="matching-test-version",
        )
        self.checked: list[str] = []

    def check(self, text: str) -> None:
        self.checked.append(text)


class StepClock:
    def __init__(self, step: float) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def _write_records(path: Path, records: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.mark.parametrize("split", ["holdout", "test", "dev", "evaluation"])
def test_rejects_non_training_split_paths(tmp_path: Path, split: str) -> None:
    path = tmp_path / split / "records.jsonl"
    _write_records(path, [{"text": "posting"}])

    with pytest.raises(ValueError, match="reject holdout/test/dev/evaluation"):
        load_train_texts(path)


def test_requires_explicit_train_path(tmp_path: Path) -> None:
    path = tmp_path / "corpus" / "records.jsonl"
    _write_records(path, [{"text": "posting"}])

    with pytest.raises(ValueError, match="explicit train"):
        load_train_texts(path)


def test_measurement_uses_injected_monotonic_clock() -> None:
    engine = FakeEngine()
    metrics = measure_engine(
        engine,
        ["first", "second"],
        warmup=1,
        repeats=2,
        clock=StepClock(0.01),
    )

    assert engine.checked == [
        "first",
        "second",
        "first",
        "second",
        "first",
        "second",
    ]
    assert metrics == {
        "elapsed_seconds": 0.04,
        "postings_per_second": 100.0,
        "latency_ms": {
            "samples": 4,
            "min": 10.0,
            "mean": 10.0,
            "p50": 10.0,
            "p95": 10.0,
            "p99": 10.0,
            "max": 10.0,
        },
    }


def test_report_is_versioned_and_does_not_emit_sensitive_record_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-corpus" / "train" / "records.jsonl"
    sensitive_values = {
        "text": "SECRET POSTING BODY",
        "id": "SECRET-RECORD-ID",
        "organization": "SECRET ORGANIZATION",
    }
    _write_records(path, [sensitive_values, {"text": "second posting"}])

    report = build_report(
        path,
        warmup=0,
        repeats=1,
        max_records=1,
        clock=StepClock(0.005),
        generated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        engine=FakeEngine(),
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["ruleset_version"] == "rules-test-version"
    assert report["matching_version"] == "matching-test-version"
    assert report["input"]["split"] == "train_only"
    assert report["input"]["records"] == 1
    assert len(report["input"]["sha256"]) == 64
    assert report["configuration"]["measured_postings"] == 1
    assert report["privacy"] == {
        "contains_posting_text": False,
        "contains_record_ids": False,
        "contains_organization_data": False,
        "contains_per_record_timings": False,
    }
    assert report["production_sla_claim"] is False
    assert "SECRET POSTING BODY" not in serialized
    assert "SECRET-RECORD-ID" not in serialized
    assert "SECRET ORGANIZATION" not in serialized
    assert str(path) not in serialized


def test_max_records_must_be_positive(tmp_path: Path) -> None:
    path = tmp_path / "corpus" / "train" / "records.jsonl"
    _write_records(path, [{"text": "posting"}])

    with pytest.raises(ValueError, match="positive"):
        load_train_texts(path, max_records=0)


def test_output_must_not_alias_training_input(tmp_path: Path) -> None:
    path = tmp_path / "train" / "records.jsonl"
    _write_records(path, [{"text": "posting"}])

    with pytest.raises(ValueError, match="must not overwrite"):
        validate_output_path(path, path)
