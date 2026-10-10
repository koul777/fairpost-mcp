"""Refresh stale local release evidence after a rule or code change.

The evidence gate (``tools/check_evidence_versions.py``) fails as soon as a
committed report was produced for an older ruleset, matching version or
runtime source. Several of those reports can only be rebuilt where the private
corpora live (``.corpus*`` is ignored by Git), so CI cannot repair them.

This tool runs the rebuild on the maintainer's machine in the order that
``docs/evidence-versioning.md`` requires:

1. corpus-bound reports that have a generator. Each is written to a temporary
   file first and copied into ``reports/`` only when it was built from the same
   corpus as the committed report (same record count and input SHA-256), so a
   refresh never silently swaps the evidence base;
2. a fresh sdist and wheel and ``reports/distribution_audit.json``, after every
   other report, because the distribution fingerprint hashes ``reports/*.json``;
3. ``reports/evidence_version_audit.json``, last, and only when the gate passes.

Reports that need a person, a deployment or a browser session are never
rewritten. The summary lists them with what to do. ``--mark-historical`` records
that such a report predates the current runtime, which is the documented way to
stop claiming it as current evidence until it is produced again.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), name="KST")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_evidence_versions import (  # noqa: E402
    _atomic_write,
    audit as evidence_audit,
    snapshot_of,
)


EVIDENCE_REPORT = "evidence_version_audit.json"
DISTRIBUTION_REPORT = "distribution_audit.json"


@dataclass(frozen=True)
class Generator:
    script: str
    default_input: str
    needs_node: bool = False
    # (key in the committed report's ``configuration``, CLI flag) pairs that are
    # reused so the refreshed report measures the same thing as before.
    reuse_configuration: tuple[tuple[str, str], ...] = ()


GENERATORS: dict[str, Generator] = {
    "web_engine_parity.json": Generator(
        "verify_web_parity.py",
        ".corpus-final/train/records.jsonl",
        needs_node=True,
    ),
    "engine_performance.json": Generator(
        "benchmark_engine.py",
        ".corpus-prd/train/records.jsonl",
        reuse_configuration=(
            ("max_records", "--max-records"),
            ("repeats", "--repeats"),
            ("warmup_passes", "--warmup"),
        ),
    ),
    "question_relevance_audit.json": Generator(
        "audit_question_relevance.py",
        ".corpus-prd/train/records.jsonl",
    ),
    "corpus_rule_coverage.json": Generator(
        "analyze_corpus.py",
        ".corpus/train/records.jsonl",
    ),
}

# Reports this tool must not regenerate on its own, with what a person does.
OWNER_ACTIONS: dict[str, str] = {
    "human_labeling_handoff.json": (
        "라벨링 인계는 라벨링 화면과 규칙 버전을 함께 묶습니다. 검토자에게 보낼 화면을 "
        "현재 규칙으로 다시 만들기로 정한 뒤 tools/build_annotation_ui.py와 "
        "tools/build_human_labeling_handoff.py를 실행하세요."
    ),
    "production_rollout.json": (
        "운영 재배포 후 tools/verify_vercel_deployment.py 결과와 함께 다시 기록하세요."
    ),
    "web_visual_audit.json": (
        "현재 웹 화면으로 데스크톱ㆍ태블릿ㆍ모바일 시각 검증을 다시 수행해 기록하세요."
    ),
    "youth_job_rule_coverage.json": (
        "청년 채용 학습 세트로 규칙 적중 집계를 다시 만드세요(docs/youth-job-api.md)."
    ),
}
DEFAULT_OWNER_ACTION = (
    "생성 도구가 등록되지 않은 보고서입니다. 만든 절차로 다시 생성하거나, "
    "현재 증거로 쓰지 않으려면 --mark-historical로 표시하세요."
)

Runner = Callable[[list[str]], int]


@dataclass
class Step:
    report: str
    status: str  # refreshed | kept | skipped | failed | needs_owner | planned
    detail: str


def _run(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _input_identity(payload: dict[str, Any] | None) -> tuple[Any, Any] | None:
    data = payload.get("input") if payload else None
    if not isinstance(data, dict):
        return None
    identity = (data.get("records"), data.get("sha256"))
    return identity if any(value is not None for value in identity) else None


def select_targets(
    initial: dict[str, Any], *, regenerate_all: bool
) -> tuple[list[str], list[str]]:
    """Split the gate's non-current reports into generator targets and the rest.

    Historical reports that have a generator are refreshed too: the marker only
    says the committed copy predates the runtime, and a fresh build replaces it.
    """

    flagged = {Path(path).name for path in initial.get("stale_paths", [])}
    historical = {
        Path(path).name for path in initial.get("historical_paths_skipped", [])
    }
    wanted = set(GENERATORS) if regenerate_all else (flagged | historical)
    targets = [name for name in GENERATORS if name in wanted]
    remaining = sorted(
        name
        for name in flagged
        if name not in GENERATORS
        and name not in {EVIDENCE_REPORT, DISTRIBUTION_REPORT}
    )
    return targets, remaining


def refresh_report(
    name: str,
    *,
    reports_dir: Path,
    input_path: Path,
    work_dir: Path,
    accept_input_change: bool,
    run: Runner,
    node_available: bool,
) -> Step:
    generator = GENERATORS[name]
    if not input_path.is_file():
        return Step(name, "skipped", f"입력 없음: {input_path}")
    if generator.needs_node and not node_available:
        return Step(name, "skipped", "Node.js가 필요합니다(node 명령을 찾지 못함)")
    committed_path = reports_dir / name
    committed = _load(committed_path)
    staged = work_dir / name
    command = [
        sys.executable,
        str(ROOT / "tools" / generator.script),
        "--input",
        str(input_path),
        "--output",
        str(staged),
    ]
    configuration = committed.get("configuration") if committed else None
    if isinstance(configuration, dict):
        for key, flag in generator.reuse_configuration:
            value = configuration.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                command.extend([flag, str(value)])
    code = run(command)
    fresh = _load(staged)
    if code != 0 or fresh is None:
        return Step(name, "failed", f"{generator.script} 종료 코드 {code}")
    before, after = _input_identity(committed), _input_identity(fresh)
    if before is not None and after != before and not accept_input_change:
        return Step(
            name,
            "kept",
            (
                f"입력 코퍼스가 기존 증거와 다릅니다(건수 {before[0]}→{after[0] if after else None})."
                " 같은 코퍼스를 --input으로 지정하거나, 바꾼 것이 맞다면"
                " --accept-input-change로 다시 실행하세요."
            ),
        )
    _atomic_write(committed_path, staged.read_text(encoding="utf-8"))
    return Step(name, "refreshed", f"{generator.script} ← {input_path}")


def refresh_distribution(
    *, reports_dir: Path, run: Runner, dist_dir: Path | None = None
) -> Step:
    """Build into ``dist/`` as CI does, then audit exactly the new artifacts.

    The audit records artifact paths, so a temporary build directory would put
    a machine-specific path (on Windows, one with the user name) into a
    committed report and fail the release report's ``dist/`` check.
    """

    try:
        import build  # noqa: F401
    except ImportError:
        return Step(
            DISTRIBUTION_REPORT,
            "skipped",
            "python -m pip install build 후 다시 실행하세요",
        )
    dist = dist_dir or ROOT / "dist"
    started = time.time() - 1
    code = run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(dist)]
    )

    def built(pattern: str) -> list[Path]:
        return sorted(
            path for path in dist.glob(pattern) if path.stat().st_mtime >= started
        )

    sdists, wheels = built("fairpost*.tar.gz"), built("fairpost*.whl")
    if code != 0 or len(sdists) != 1 or len(wheels) != 1:
        return Step(
            DISTRIBUTION_REPORT,
            "failed",
            f"패키지 빌드 종료 코드 {code}, 새 sdist {len(sdists)}개ㆍwheel {len(wheels)}개",
        )
    code = run(
        [
            sys.executable,
            str(ROOT / "tools" / "verify_distribution.py"),
            "--sdist",
            str(sdists[0]),
            "--wheel",
            str(wheels[0]),
            "--output",
            str(reports_dir / DISTRIBUTION_REPORT),
        ]
    )
    if code != 0:
        return Step(DISTRIBUTION_REPORT, "failed", f"verify_distribution 종료 코드 {code}")
    return Step(DISTRIBUTION_REPORT, "refreshed", "새 sdistㆍwheel로 배포본 감사 갱신")


def mark_historical(path: Path, reason: str, *, marked_at: str) -> None:
    if path.name in {EVIDENCE_REPORT, DISTRIBUTION_REPORT}:
        raise ValueError(f"{path.name}은 이 도구로 다시 만들 수 있으므로 historical로 두지 않습니다")
    if snapshot_of(path.name) is not None:
        raise ValueError(f"{path.name}은 날짜 접미사 스냅샷이라 이미 현재 증거로 쓰이지 않습니다")
    if not reason.strip():
        raise ValueError("--reason이 필요합니다")
    payload = _load(path)
    if payload is None:
        raise ValueError(f"{path}를 JSON 객체로 읽지 못했습니다")
    payload["evidence_status"] = "historical"
    payload["historical_reason"] = reason.strip()
    payload["historical_marked_at"] = marked_at
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _parse_inputs(values: Sequence[str]) -> dict[str, Path]:
    inputs: dict[str, Path] = {}
    for value in values:
        name, separator, raw = value.partition("=")
        if not separator or name not in GENERATORS or not raw:
            raise ValueError(
                f"--input은 보고서이름=경로 형식이어야 합니다(대상: {', '.join(GENERATORS)}): {value}"
            )
        inputs[name] = Path(raw)
    return inputs


def refresh(
    *,
    reports_dir: Path,
    inputs: dict[str, Path],
    regenerate_all: bool,
    accept_input_change: bool,
    skip_distribution: bool,
    plan_only: bool,
    run: Runner = _run,
    node_available: bool | None = None,
    dist_dir: Path | None = None,
) -> dict[str, Any]:
    evidence_output = reports_dir / EVIDENCE_REPORT
    initial = evidence_audit(reports_dir, scope="local", output=evidence_output)
    targets, remaining = select_targets(initial, regenerate_all=regenerate_all)
    if node_available is None:
        node_available = shutil.which("node") is not None
    steps: list[Step] = []
    with tempfile.TemporaryDirectory(prefix="fairpost-refresh-") as scratch:
        work_dir = Path(scratch)
        for name in targets:
            input_path = inputs.get(name, ROOT / GENERATORS[name].default_input)
            if plan_only:
                steps.append(Step(name, "planned", f"{GENERATORS[name].script} ← {input_path}"))
                continue
            steps.append(
                refresh_report(
                    name,
                    reports_dir=reports_dir,
                    input_path=input_path,
                    work_dir=work_dir,
                    accept_input_change=accept_input_change,
                    run=run,
                    node_available=node_available,
                )
            )
        distribution_flagged = any(
            Path(path).name == DISTRIBUTION_REPORT
            for path in initial.get("stale_paths", [])
        )
        wrote_reports = any(step.status == "refreshed" for step in steps)
        if skip_distribution:
            if distribution_flagged or wrote_reports:
                steps.append(Step(DISTRIBUTION_REPORT, "skipped", "--skip-distribution"))
        elif distribution_flagged or wrote_reports or regenerate_all:
            if plan_only:
                steps.append(Step(DISTRIBUTION_REPORT, "planned", "sdistㆍwheel 빌드 후 verify_distribution.py"))
            else:
                steps.append(
                    refresh_distribution(
                        reports_dir=reports_dir, run=run, dist_dir=dist_dir
                    )
                )
    for name in remaining:
        steps.append(Step(name, "needs_owner", OWNER_ACTIONS.get(name, DEFAULT_OWNER_ACTION)))

    final: dict[str, Any] | None = None
    if not plan_only:
        final = evidence_audit(reports_dir, scope="local", output=evidence_output)
        if final["passed"]:
            _atomic_write(
                evidence_output,
                json.dumps(final, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            )
    return {
        "schema_version": "fairpost-evidence-refresh-v1",
        "checked_at": _now(),
        "plan_only": plan_only,
        "initial_stale": initial.get("stale_paths", []),
        "steps": [asdict(step) for step in steps],
        "final_passed": final["passed"] if final else None,
        "final_stale": final.get("stale_paths", []) if final else None,
        "evidence_report_written": bool(final and final["passed"]),
    }


LABELS = {
    "refreshed": "갱신",
    "kept": "유지",
    "skipped": "건너뜀",
    "failed": "실패",
    "needs_owner": "직접 처리",
    "planned": "예정",
}


def _print_summary(summary: dict[str, Any]) -> None:
    initial = summary["initial_stale"]
    print(f"시작 시 stale 보고서 {len(initial)}개")
    for step in summary["steps"]:
        print(f"  [{LABELS.get(step['status'], step['status'])}] {step['report']}: {step['detail']}")
    if summary["plan_only"]:
        print("계획만 표시했습니다. 실행하려면 --plan 없이 다시 실행하세요.")
        return
    if summary["final_passed"]:
        print(f"증거 게이트 통과. reports/{EVIDENCE_REPORT}를 갱신했습니다.")
    else:
        stale = summary["final_stale"] or []
        print(f"증거 게이트 미통과: 남은 stale {len(stale)}개")
        for path in stale:
            print(f"  - {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "규칙ㆍ코드 변경 뒤 stale이 된 로컬 증거 보고서를 비공개 코퍼스로 다시 만들고, "
            "배포본 감사와 증거 버전 감사를 순서대로 갱신합니다."
        )
    )
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--plan", action="store_true", help="실행하지 않고 대상만 표시")
    parser.add_argument(
        "--all",
        action="store_true",
        help="stale 여부와 관계없이 생성 도구가 있는 보고서를 모두 다시 만듦",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="REPORT=PATH",
        help="보고서별 입력 코퍼스 경로(기본값은 도구별 .corpus* 경로)",
    )
    parser.add_argument(
        "--accept-input-change",
        action="store_true",
        help="기존 증거와 다른 코퍼스로 만든 보고서도 반영",
    )
    parser.add_argument("--skip-distribution", action="store_true")
    parser.add_argument(
        "--mark-historical",
        action="append",
        default=[],
        metavar="REPORT",
        help="보고서를 historical로 표시하고 종료(--reason 필요)",
    )
    parser.add_argument("--reason", default="")
    parser.add_argument("--summary", type=Path, help="JSON 요약을 쓸 경로")
    args = parser.parse_args(argv)

    if args.mark_historical:
        marked_at = _now()
        try:
            for name in args.mark_historical:
                mark_historical(args.reports_dir / name, args.reason, marked_at=marked_at)
                print(f"historical 표시: reports/{name}")
        except ValueError as exc:
            parser.error(str(exc))
        return 0

    try:
        inputs = _parse_inputs(args.input)
    except ValueError as exc:
        parser.error(str(exc))
    summary = refresh(
        reports_dir=args.reports_dir,
        inputs=inputs,
        regenerate_all=args.all,
        accept_input_change=args.accept_input_change,
        skip_distribution=args.skip_distribution,
        plan_only=args.plan,
    )
    _print_summary(summary)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if summary["plan_only"]:
        return 0
    return 0 if summary["final_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
