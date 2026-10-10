from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.publication_benchmark import (
    guard_path, load_inputs, measure, run_benchmark, sha256,
)


class FakeEngine:
    def __init__(self, fail_text: str | None = None) -> None:
        self.ruleset = SimpleNamespace(version="test-rules", matching_version="test-matching")
        self.texts: list[str] = []
        self.fail_text = fail_text

    def check(self, text: str) -> SimpleNamespace:
        self.texts.append(text)
        if text == self.fail_text:
            raise RuntimeError("synthetic operational failure")
        result = {"findings": [{"id": "L1"}], "questions": [{"id": "Q1"}],
                  "slots": [{"slot": "deadline", "found": True},
                            {"slot": "appeal", "found": False}]}
        return SimpleNamespace(to_dict=lambda: result)


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += .01
        return self.value


def write_input(root: Path, *, phase: str = "development", texts: tuple[str, ...] = ("원문  공백\nOriginal heading\n본문",)) -> tuple[Path, Path]:
    folder = root / phase
    (folder / "snapshots").mkdir(parents=True)
    records = []
    for i, text in enumerate(texts):
        name = f"snapshots/post-{i}.txt"
        (folder / name).write_bytes(text.encode("utf-8"))
        records.append({"id": f"post-{i}", "source_url": f"https://example.org/jobs/{i}",
                        "snapshot": name, "content_hash": sha256(text.encode("utf-8")),
                        "text": text, "title": "Original title", "sector": "private",
                        "occupation": "research", "employer": "Fixture employer"})
    record_bytes = ("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)).encode("utf-8")
    path = folder / "records.jsonl"
    path.write_bytes(record_bytes)
    manifest = folder / "split-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "phase": phase,
        "records_sha256": sha256(record_bytes),
        "records": [{"id": r["id"], "content_hash": r["content_hash"]} for r in records]}), encoding="utf-8")
    return path, manifest


def rewrite_records(path: Path, manifest: Path, records: list[dict]) -> None:
    raw = ("".join(json.dumps(r) + "\n" for r in records)).encode("utf-8")
    path.write_bytes(raw)
    split = json.loads(manifest.read_text(encoding="utf-8"))
    split["records_sha256"] = sha256(raw)
    manifest.write_text(json.dumps(split), encoding="utf-8")


@pytest.mark.parametrize("name", ["HOLDOUT", "private-holdout-body.jsonl", "sealed-reserved", ".corpus-prd"])
def test_forbidden_path_is_rejected_before_any_read(tmp_path: Path, monkeypatch, name: str) -> None:
    def fail_read(*args, **kwargs):
        pytest.fail("forbidden input was opened")
    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(ValueError, match="forbidden"):
        load_inputs(tmp_path / name / "records.jsonl", tmp_path / "manifest.json",
                    final_validation=True, private_root=tmp_path)


def test_resolved_alias_cannot_escape_read_boundary(tmp_path: Path, monkeypatch) -> None:
    supplied = tmp_path / "development" / "alias.jsonl"
    original_resolve = Path.resolve
    def fake_resolve(path, *args, **kwargs):
        if path == supplied:
            return tmp_path / "HOLDOUT" / "records.jsonl"
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(ValueError, match="forbidden"):
        guard_path(supplied, final_validation=True)


def test_preserves_original_text_and_verifies_snapshots(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    rows, integrity = load_inputs(path, manifest, private_root=tmp_path)
    assert rows[0]["text"] == "원문  공백\nOriginal heading\n본문"
    assert integrity["records_sha256"] == sha256(path.read_bytes())
    assert integrity["split_manifest_sha256"] == sha256(manifest.read_bytes())
    engine = FakeEngine()
    measure(rows, engine, clock=StepClock())
    assert engine.texts == [rows[0]["text"]]


def test_requires_coordinator_split_before_engine_creation(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    split = json.loads(manifest.read_text(encoding="utf-8"))
    split["phase"] = "collection_only_pending_coordinator_split"
    manifest.write_text(json.dumps(split), encoding="utf-8")
    def forbidden_engine():
        pytest.fail("engine was constructed before split validation")
    with pytest.raises(ValueError, match="phase"):
        run_benchmark(path, manifest, tmp_path / "output", private_root=tmp_path,
                      engine_factory=forbidden_engine, fingerprint=lambda: {})


def test_modified_records_bytes_are_rejected(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="records bytes"):
        load_inputs(path, manifest, private_root=tmp_path)


def test_body_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["text"] += " changed"
    rewrite_records(path, manifest, [row])
    with pytest.raises(ValueError, match="content_hash mismatch"):
        load_inputs(path, manifest, private_root=tmp_path)


def test_snapshot_tamper_is_rejected(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    (path.parent / "snapshots/post-0.txt").write_bytes(b"altered")
    with pytest.raises(ValueError, match="snapshot bytes"):
        load_inputs(path, manifest, private_root=tmp_path)


def test_snapshot_traversal_is_rejected(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row["snapshot"] = "../outside.txt"
    rewrite_records(path, manifest, [row])
    with pytest.raises(ValueError, match="relative contained"):
        load_inputs(path, manifest, private_root=tmp_path)


def test_duplicate_body_is_rejected(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path, texts=("same text", "same text"))
    with pytest.raises(ValueError, match="duplicate record"):
        load_inputs(path, manifest, private_root=tmp_path)


def test_manifest_must_bind_exact_id_set(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    split = json.loads(manifest.read_text(encoding="utf-8"))
    split["records"].append({"id": "missing", "content_hash": "a" * 64})
    manifest.write_text(json.dumps(split), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly cover"):
        load_inputs(path, manifest, private_root=tmp_path)


@pytest.mark.parametrize("field", ["expected_findings", "labels", "predictions", "attestation"])
def test_refuses_label_or_prediction_inputs(tmp_path: Path, field: str) -> None:
    path, manifest = write_input(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    row[field] = []
    rewrite_records(path, manifest, [row])
    with pytest.raises(ValueError, match="labels or predictions"):
        load_inputs(path, manifest, private_root=tmp_path)


def test_explicit_validation_is_separate_from_human_final(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path, phase="validation")
    with pytest.raises(ValueError, match="--final-validation"):
        load_inputs(path, manifest, private_root=tmp_path)
    report = run_benchmark(path, manifest, tmp_path / "validation-output",
                          final_validation=True, private_root=tmp_path,
                          engine_factory=FakeEngine, fingerprint=lambda: {"file": "a" * 64}, clock=StepClock())
    assert report["scope"] == "exploratory_validation_benchmark"
    assert report["human_gold_evaluation"] is False
    assert report["accuracy_claim"] is False


def test_final_flag_does_not_promote_development_input(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    with pytest.raises(ValueError, match="phase must be validation"):
        load_inputs(path, manifest, final_validation=True, private_root=tmp_path)


def test_counts_errors_and_success_denominators(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path, texts=("ok", "fail"))
    records, _ = load_inputs(path, manifest, private_root=tmp_path)
    results, aggregate = measure(records, FakeEngine(fail_text="fail"), clock=StepClock())
    assert aggregate["attempted"] == 2
    assert aggregate["succeeded"] == 1
    assert aggregate["errors"] == 1
    assert aggregate["coverage"]["successful_postings"] == 1
    assert aggregate["coverage"]["finding_posting_counts"] == {"L1": 1}
    assert aggregate["coverage"]["slot_observation_counts"] == {"appeal": 1, "deadline": 1}
    assert aggregate["coverage"]["slot_found_posting_counts"] == {"deadline": 1}
    assert aggregate["latency_ms"]["p95"] == pytest.approx(10)
    assert results[1]["error"]["type"] == "RuntimeError"


def test_clock_regression_aborts_measurement() -> None:
    engine = FakeEngine()
    ticks = iter([2.0, 1.0])
    with pytest.raises(RuntimeError, match="monotonic"):
        measure([{"id": "p", "source_url": "https://example.org", "content_hash": "a" * 64,
                  "text": "fixture", "sector": "private", "occupation": "tech", "employer": "fixture"}],
                engine, clock=lambda: next(ticks))


def test_source_change_prevents_report_writes(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    fingerprints = iter([{"core/engine.py": "a" * 64}, {"core/engine.py": "b" * 64}])
    target = tmp_path / "output"
    with pytest.raises(RuntimeError, match="source changed"):
        run_benchmark(path, manifest, target, private_root=tmp_path,
                      engine_factory=FakeEngine, fingerprint=lambda: next(fingerprints), clock=StepClock())
    assert not target.exists()


def test_output_is_private_and_existing_results_are_preserved(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    target = tmp_path / "output"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    with pytest.raises(ValueError, match="never overwritten"):
        run_benchmark(path, manifest, target, private_root=tmp_path)
    assert marker.read_text(encoding="utf-8") == "existing"
    with pytest.raises(ValueError, match="private publication run"):
        run_benchmark(path, manifest, tmp_path.parent / "public-report", private_root=tmp_path)


def test_results_are_hashed_and_no_accuracy_metrics_are_invented(tmp_path: Path) -> None:
    path, manifest = write_input(tmp_path)
    target = tmp_path / "output"
    report = run_benchmark(path, manifest, target, private_root=tmp_path,
                          engine_factory=FakeEngine, fingerprint=lambda: {"core/engine.py": "a" * 64}, clock=StepClock())
    assert report["integrity"]["results_sha256"] == sha256((target / "results.jsonl").read_bytes())
    assert report["accuracy_claim"] is False
    assert report["human_gold_evaluation"] is False
    assert report["production_sla_claim"] is False
    assert not {"precision", "recall", "accuracy", "fairness_score", "target_gate"} & report.keys()
    assert report["ruleset_version"] == "test-rules"
    result = json.loads((target / "results.jsonl").read_text(encoding="utf-8").strip())
    assert result["content_hash"] == sha256("원문  공백\nOriginal heading\n본문".encode("utf-8"))
    assert "text" not in result
