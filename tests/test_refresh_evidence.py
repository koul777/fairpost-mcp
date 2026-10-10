from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

import tools.check_evidence_versions as evidence
import tools.refresh_evidence as refresh


ROOT = Path(__file__).resolve().parents[1]


class StubRuleset:
    version = "rules-current"
    matching_version = "match-current"


@pytest.fixture
def reports(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(evidence, "load_ruleset", lambda path: StubRuleset())
    monkeypatch.setattr(
        evidence, "current_runtime_fingerprint", lambda ruleset: "runtime-current"
    )
    monkeypatch.setattr(
        evidence, "current_validation_fingerprint", lambda: "validation-current"
    )
    path = tmp_path / "reports"
    path.mkdir()
    return path


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stale(records: int = 3, **extra) -> dict:
    return {
        "ruleset_version": "rules-old",
        "matching_version": "match-old",
        "input": {"records": records, "sha256": f"sha-{records}"},
        **extra,
    }


def _fake_generator(records: int = 3, calls: list | None = None):
    """Runner that writes a current report wherever ``--output`` points."""

    def run(command: list[str]) -> int:
        if calls is not None:
            calls.append(command)
        output = Path(command[command.index("--output") + 1])
        _write(
            output,
            {
                "ruleset_version": "rules-current",
                "matching_version": "match-current",
                "input": {"records": records, "sha256": f"sha-{records}"},
            },
        )
        return 0

    return run


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus" / "records.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"text": "x"}\n', encoding="utf-8")
    return path


def test_select_targets_refreshes_stale_and_historical_reports_with_generators() -> None:
    targets, remaining = refresh.select_targets(
        {
            "stale_paths": [
                "reports/web_engine_parity.json",
                "reports/production_rollout.json",
                "reports/distribution_audit.json",
                "reports/evidence_version_audit.json",
            ],
            "historical_paths_skipped": [
                "reports/engine_performance.json",
                "reports/youth_job_rule_coverage.json",
            ],
        },
        regenerate_all=False,
    )

    assert targets == ["web_engine_parity.json", "engine_performance.json"]
    # Historical reports without a generator stay as recorded; only reports
    # still claiming to be current need a person.
    assert remaining == ["production_rollout.json"]

    everything, _ = refresh.select_targets({}, regenerate_all=True)
    assert everything == list(refresh.GENERATORS)


def test_refresh_replaces_report_built_from_the_same_corpus(
    reports: Path, tmp_path: Path
) -> None:
    _write(reports / "question_relevance_audit.json", _stale())

    summary = refresh.refresh(
        reports_dir=reports,
        inputs={"question_relevance_audit.json": _corpus(tmp_path)},
        regenerate_all=False,
        accept_input_change=False,
        skip_distribution=True,
        plan_only=False,
        run=_fake_generator(),
        node_available=True,
    )

    assert [step["status"] for step in summary["steps"]] == ["refreshed", "skipped"]
    assert summary["final_passed"] is True
    assert summary["evidence_report_written"] is True
    refreshed = json.loads((reports / "question_relevance_audit.json").read_text(encoding="utf-8"))
    assert refreshed["ruleset_version"] == "rules-current"
    written = json.loads((reports / "evidence_version_audit.json").read_text(encoding="utf-8"))
    assert written["passed"] is True


def test_refresh_keeps_committed_report_when_the_corpus_changed(
    reports: Path, tmp_path: Path
) -> None:
    committed = _stale(records=3)
    _write(reports / "question_relevance_audit.json", committed)

    summary = refresh.refresh(
        reports_dir=reports,
        inputs={"question_relevance_audit.json": _corpus(tmp_path)},
        regenerate_all=False,
        accept_input_change=False,
        skip_distribution=True,
        plan_only=False,
        run=_fake_generator(records=2),
        node_available=True,
    )

    assert summary["steps"][0]["status"] == "kept"
    assert "3→2" in summary["steps"][0]["detail"]
    assert json.loads(
        (reports / "question_relevance_audit.json").read_text(encoding="utf-8")
    ) == committed
    assert summary["final_passed"] is False
    assert not (reports / "evidence_version_audit.json").exists()

    accepted = refresh.refresh(
        reports_dir=reports,
        inputs={"question_relevance_audit.json": _corpus(tmp_path)},
        regenerate_all=False,
        accept_input_change=True,
        skip_distribution=True,
        plan_only=False,
        run=_fake_generator(records=2),
        node_available=True,
    )
    assert accepted["steps"][0]["status"] == "refreshed"
    assert accepted["final_passed"] is True


def test_refresh_skips_missing_corpus_and_missing_node(reports: Path, tmp_path: Path) -> None:
    _write(reports / "question_relevance_audit.json", _stale())
    _write(reports / "web_engine_parity.json", _stale())

    def never(command: list[str]) -> int:
        raise AssertionError(f"nothing should run: {command}")

    summary = refresh.refresh(
        reports_dir=reports,
        inputs={
            "question_relevance_audit.json": tmp_path / "absent.jsonl",
            "web_engine_parity.json": _corpus(tmp_path),
        },
        regenerate_all=False,
        accept_input_change=False,
        skip_distribution=True,
        plan_only=False,
        run=never,
        node_available=False,
    )

    by_report = {step["report"]: step for step in summary["steps"]}
    assert by_report["question_relevance_audit.json"]["status"] == "skipped"
    assert "입력 없음" in by_report["question_relevance_audit.json"]["detail"]
    assert by_report["web_engine_parity.json"]["status"] == "skipped"
    assert "Node.js" in by_report["web_engine_parity.json"]["detail"]
    assert summary["final_passed"] is False


def test_refresh_reuses_the_committed_benchmark_configuration(
    reports: Path, tmp_path: Path
) -> None:
    _write(
        reports / "engine_performance.json",
        _stale(configuration={"max_records": 500, "repeats": 5, "warmup_passes": 1}),
    )
    calls: list[list[str]] = []

    refresh.refresh(
        reports_dir=reports,
        inputs={"engine_performance.json": _corpus(tmp_path)},
        regenerate_all=False,
        accept_input_change=False,
        skip_distribution=True,
        plan_only=False,
        run=_fake_generator(calls=calls),
        node_available=True,
    )

    (command,) = calls
    assert command[command.index("--max-records") + 1] == "500"
    assert command[command.index("--repeats") + 1] == "5"
    assert command[command.index("--warmup") + 1] == "1"


def test_plan_lists_work_and_owner_actions_without_running(reports: Path) -> None:
    _write(reports / "web_engine_parity.json", _stale())
    _write(reports / "production_rollout.json", _stale())
    _write(
        reports / "distribution_audit.json",
        {"runtime_source_fingerprint": "runtime-old"},
    )

    def never(command: list[str]) -> int:
        raise AssertionError(f"plan must not run: {command}")

    summary = refresh.refresh(
        reports_dir=reports,
        inputs={},
        regenerate_all=False,
        accept_input_change=False,
        skip_distribution=False,
        plan_only=True,
        run=never,
        node_available=True,
    )

    assert [(step["report"], step["status"]) for step in summary["steps"]] == [
        ("web_engine_parity.json", "planned"),
        ("distribution_audit.json", "planned"),
        ("production_rollout.json", "needs_owner"),
    ]
    assert "재배포" in summary["steps"][-1]["detail"]
    assert summary["final_passed"] is None
    assert not (reports / "evidence_version_audit.json").exists()


def test_distribution_audit_uses_only_the_new_artifacts_in_dist(
    reports: Path, tmp_path: Path
) -> None:
    pytest.importorskip("build")
    dist = tmp_path / "dist"
    dist.mkdir()
    old = dist / "fairpost-0.2.0.tar.gz"
    old.write_text("old", encoding="utf-8")
    os.utime(old, (1, 1))
    calls: list[list[str]] = []

    def run(command: list[str]) -> int:
        calls.append(command)
        if "build" in command:
            (dist / "fairpost-0.3.0.tar.gz").write_text("s", encoding="utf-8")
            (dist / "fairpost-0.3.0-py3-none-any.whl").write_text("w", encoding="utf-8")
        return 0

    step = refresh.refresh_distribution(reports_dir=reports, run=run, dist_dir=dist)

    assert step.status == "refreshed"
    verify = calls[-1]
    assert verify[verify.index("--sdist") + 1] == str(dist / "fairpost-0.3.0.tar.gz")
    assert verify[verify.index("--wheel") + 1] == str(
        dist / "fairpost-0.3.0-py3-none-any.whl"
    )
    assert calls[0][calls[0].index("--outdir") + 1] == str(dist)


def test_mark_historical_keeps_key_order_and_takes_report_out_of_the_gate(
    reports: Path,
) -> None:
    path = reports / "production_rollout.json"
    _write(path, {"schema_version": "x", "evidence_status": "current", **_stale()})

    refresh.mark_historical(path, "10/4 배포 기록", marked_at="2026-10-10T12:00:00+09:00")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload)[:2] == ["schema_version", "evidence_status"]
    assert payload["evidence_status"] == "historical"
    assert payload["historical_reason"] == "10/4 배포 기록"
    assert payload["historical_marked_at"] == "2026-10-10T12:00:00+09:00"
    audit = evidence.audit(reports, scope="local", output=reports / "evidence_version_audit.json")
    assert audit["historical_paths_skipped"] == ["reports/production_rollout.json"]


@pytest.mark.parametrize(
    ("name", "reason", "message"),
    [
        ("evidence_version_audit.json", "r", "다시 만들 수 있으므로"),
        ("distribution_audit.json", "r", "다시 만들 수 있으므로"),
        ("build_artifact-v0.4-candidate-2026-10-03.json", "r", "스냅샷"),
        ("production_rollout.json", "  ", "--reason"),
    ],
)
def test_mark_historical_refuses_regenerable_snapshot_or_unexplained_marks(
    reports: Path, name: str, reason: str, message: str
) -> None:
    _write(reports / name, _stale())
    with pytest.raises(ValueError, match=message):
        refresh.mark_historical(reports / name, reason, marked_at="t")


def _argparse_flags(script: Path) -> set[str]:
    flags: set[str] = set()
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            flags.update(
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return flags


@pytest.mark.parametrize("name", sorted(refresh.GENERATORS))
def test_registered_generators_accept_the_flags_the_refresh_passes(name: str) -> None:
    generator = refresh.GENERATORS[name]
    flags = _argparse_flags(ROOT / "tools" / generator.script)
    expected = {"--input", "--output", *(flag for _, flag in generator.reuse_configuration)}
    assert expected <= flags
    assert (ROOT / "reports" / name).exists()


def test_owner_actions_and_generators_do_not_overlap() -> None:
    assert not set(refresh.OWNER_ACTIONS) & set(refresh.GENERATORS)
