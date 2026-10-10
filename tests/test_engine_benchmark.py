from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.refresh_evidence as refresh
from tools.benchmark_engine import (
    build_report,
    load_train_texts,
    measure_engine,
    summarize_repeats,
    summarize_timings,
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


class DurationClock:
    """A clock whose consecutive start/finish readings span the scripted durations.

    Durations are powers of two so every sum and rate below is exact.
    """

    def __init__(self, durations: list[float]) -> None:
        self.durations = iter(durations)
        self.now = 0.0
        self.reading_start = True

    def __call__(self) -> float:
        if not self.reading_start:
            self.now += next(self.durations)
        self.reading_start = not self.reading_start
        return self.now


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
        "pooled_postings_per_second": 100.0,
        "repeat_postings_per_second": [100.0, 100.0],
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


def test_headline_throughput_is_the_median_of_the_repeats() -> None:
    # Two postings per repeat. Repeat 3 stalls (4 s per call), repeat 5 is slower.
    clock = DurationClock([0.25, 0.25, 0.25, 0.25, 4.0, 4.0, 0.25, 0.25, 0.5, 0.5])
    metrics = measure_engine(
        FakeEngine(), ["first", "second"], warmup=0, repeats=5, clock=clock
    )

    assert metrics["repeat_postings_per_second"] == [4.0, 4.0, 0.25, 4.0, 2.0]
    assert metrics["postings_per_second"] == 4.0
    # The stall still shows in the pooled rate and in the latency tail.
    assert metrics["elapsed_seconds"] == 10.5
    assert metrics["pooled_postings_per_second"] == round(10 / 10.5, 3)
    assert metrics["latency_ms"]["samples"] == 10
    assert metrics["latency_ms"]["p50"] == 250.0
    assert metrics["latency_ms"]["p99"] == 4000.0
    assert metrics["latency_ms"]["max"] == 4000.0


def test_headline_throughput_of_an_even_number_of_repeats_is_the_middle_mean() -> None:
    clock = DurationClock([0.25, 0.25, 0.5, 0.5])
    metrics = measure_engine(
        FakeEngine(), ["first", "second"], warmup=0, repeats=2, clock=clock
    )

    assert metrics["repeat_postings_per_second"] == [4.0, 2.0]
    assert metrics["postings_per_second"] == 3.0


def test_repeat_throughputs_follow_repeat_order_not_speed() -> None:
    metrics = summarize_repeats([[0.5, 0.5], [0.25, 0.25], [1.0, 1.0]])

    assert metrics["repeat_postings_per_second"] == [2.0, 4.0, 1.0]
    assert metrics["postings_per_second"] == 2.0


def test_ungrouped_timings_are_one_repeat() -> None:
    metrics = summarize_timings([0.25, 0.25, 0.5])

    assert metrics["repeat_postings_per_second"] == [3.0]
    assert metrics["postings_per_second"] == 3.0
    assert metrics["pooled_postings_per_second"] == 3.0
    assert metrics["latency_ms"]["samples"] == 3


@pytest.mark.parametrize(
    ("repeat_timings", "message"),
    [
        ([], "at least one"),
        ([[0.25], []], "at least one"),
        ([[0.25, -0.25]], "negative"),
        ([[0.0, 0.0]], "greater than zero"),
        # A repeat that measured no time has no throughput, even if the others do.
        ([[0.25, 0.25], [0.0]], "greater than zero"),
    ],
)
def test_summary_rejects_unusable_timings(
    repeat_timings: list[list[float]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        summarize_repeats(repeat_timings)


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
    assert report["configuration"]["throughput_aggregation"] == "median_of_repeats"
    assert report["metrics"]["repeat_postings_per_second"] == [200.0]
    assert report["metrics"]["postings_per_second"] == 200.0
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


def test_report_keeps_what_the_evidence_refresh_reads(tmp_path: Path) -> None:
    path = tmp_path / "corpus" / "train" / "records.jsonl"
    _write_records(path, [{"text": "first posting"}, {"text": "second posting"}])

    report = build_report(
        path,
        warmup=1,
        repeats=5,
        max_records=2,
        clock=StepClock(0.005),
        generated_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        engine=FakeEngine(),
    )

    # tools/refresh_evidence.py regenerates the report with the committed
    # configuration (integers only) and compares input.records / input.sha256.
    reused = dict(refresh.GENERATORS["engine_performance.json"].reuse_configuration)
    assert set(reused) == {"max_records", "repeats", "warmup_passes"}
    for key in reused:
        value = report["configuration"][key]
        assert isinstance(value, int) and not isinstance(value, bool), key
    assert report["configuration"]["repeats"] == 5
    assert refresh._input_identity(report) == (2, report["input"]["sha256"])
    assert len(report["metrics"]["repeat_postings_per_second"]) == 5
    assert json.loads(json.dumps(report)) == report
    # Aggregates only: no per-record timings.
    assert report["privacy"]["contains_per_record_timings"] is False
    assert set(report["metrics"]) == {
        "elapsed_seconds",
        "latency_ms",
        "pooled_postings_per_second",
        "postings_per_second",
        "repeat_postings_per_second",
    }


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
