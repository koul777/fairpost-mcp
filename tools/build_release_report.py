from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from typing import Sequence

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), name="KST")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402
from mcp_server.build_identity import runtime_source_fingerprint  # noqa: E402
from tools.evaluate import evaluator_fingerprint  # noqa: E402
from tools.release_inputs import (  # noqa: E402
    validation_inputs,
    validation_source_fingerprint,
)
from tools.verify_distribution import distribution_source_fingerprint  # noqa: E402


def _require_report_schema(
    payload: object,
    expected: object,
    label: str,
) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("schema_version") != expected:
        raise ValueError(f"{label} has an unsupported schema_version")
    return payload


def _current_client_evidence_matches(
    client: object,
    *,
    ruleset_version: str,
    matching_version: str,
    runtime_source_fingerprint_value: str,
    deployment_id: object,
    vercel_audit_sha256: str,
    endpoint: str,
    authentication: object,
) -> bool:
    if not isinstance(client, dict):
        return False
    inspector = client.get("official_inspector")
    if not isinstance(inspector, dict):
        return False
    return bool(
        client.get("schema_version") == "fairpost-mcp-client-audit-v2"
        and client.get("evidence_status") == "current"
        and client.get("ruleset_version") == ruleset_version
        and client.get("matching_version") == matching_version
        and client.get("runtime_source_fingerprint")
        == runtime_source_fingerprint_value
        and client.get("deployment_id") == deployment_id
        and client.get("vercel_deployment_audit_sha256") == vercel_audit_sha256
        and client.get("endpoint") == endpoint
        and client.get("authentication") == authentication
        and client.get("contains_posting_text") is False
        and client.get("synthetic_input_only") is True
        and inspector.get("tools_list_exit_code") == 0
        and inspector.get("tool_call_exit_code") == 0
        and inspector.get("is_error") is False
        and inspector.get("tool_count") == 2
        and inspector.get("tools")
        == ["check_job_posting", "next_review_question"]
        and inspector.get("all_tools_read_only") is True
        and inspector.get("endpoint") == endpoint
        and inspector.get("version") == "2.4.0"
    )


def _release_tag_evidence() -> tuple[str | None, str]:
    try:
        repository = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None, "Git is unavailable, so the release tag could not be checked."
    if repository.returncode != 0 or repository.stdout.strip() != "true":
        return None, (
            "The workspace is not a Git repository, so no release tag evidence is available."
        )

    tags = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if tags.returncode != 0:
        return None, (
            "The repository is available, but tags for the current HEAD could not be listed."
        )
    tag_names = sorted(tag for tag in tags.stdout.splitlines() if tag.strip())
    if not tag_names:
        return None, "The repository is available, but the current HEAD has no release tag."
    if len(tag_names) != 1:
        return None, (
            "The current HEAD has multiple release tags, so release identity is ambiguous: "
            + ", ".join(tag_names)
        )
    return tag_names[0], f"Release tag found on the current HEAD: {tag_names[0]}"


def _parse_passing_test_count(payload: bytes) -> int:
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ValueError("--junitxml must be a readable JUnit XML report") from exc
    suites = list(root.iter("testsuite"))
    if not suites:
        raise ValueError("--junitxml does not contain a testsuite")
    if any(
        next(root.iter(tag), None) is not None
        for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("--junitxml must prove a complete passing test run")
    try:
        tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
        failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
        errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
        skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    except ValueError as exc:
        raise ValueError("--junitxml contains invalid test counters") from exc
    testcase_count = sum(1 for _case in root.iter("testcase"))
    if tests < 1 or tests != testcase_count or failures or errors or skipped:
        raise ValueError("--junitxml must prove a complete passing test run")
    return tests


def _load_passing_test_evidence(path: Path) -> tuple[int, int, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("--junitxml must be a readable JUnit XML report") from exc
    return (
        _parse_passing_test_count(payload),
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


def _verify_junit_freshness(path: Path) -> dict[str, object]:
    try:
        junit_root = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError, DefusedXmlException) as exc:
        raise ValueError("--junitxml must be a readable JUnit XML report") from exc
    recorded = [
        item.attrib.get("value")
        for item in junit_root.iter("property")
        if item.attrib.get("name") == "fairpost_validation_source_fingerprint"
    ]
    current = validation_source_fingerprint(ROOT)
    if recorded != [current]:
        raise ValueError(
            "--junitxml is not bound to current validation inputs; rerun the full "
            "test suite"
        )
    return {
        "fresh_for_current_inputs": True,
        "source_fingerprint": current,
        "validation_inputs_checked": len(validation_inputs(ROOT)),
    }


def _audited_artifact(details: dict[str, object], kind: str) -> dict[str, object]:
    raw_path = Path(str(details["path"]))
    path = raw_path if raw_path.is_absolute() else ROOT / raw_path
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to((ROOT / "dist").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{kind} artifact must be inside the project dist directory") from exc
    payload = resolved.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if len(payload) != details.get("bytes") or actual_sha256 != details.get("sha256"):
        raise ValueError(f"{kind} artifact no longer matches distribution_audit.json")
    return {
        "path": _display_artifact_path(resolved),
        "bytes": len(payload),
        "sha256": actual_sha256,
    }


def _load_passing_test_count(path: Path) -> int:
    """Compatibility helper used by focused validation tests."""
    return _load_passing_test_evidence(path)[0]


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _evidence_paths() -> tuple[Path, ...]:
    return tuple(
        ROOT / "reports" / name
        for name in (
            "distribution_audit.json",
            "web_engine_parity.json",
            "mcp_client_audit.json",
            "work24_access_audit.json",
            "vercel_deployment_audit.json",
            "prd_corpus_summary.json",
            "human_labeling_handoff.json",
            "evidence_version_audit.json",
            "corpus_diversity_audit.json",
            "evaluation.json",
        )
    )


def validate_paths(
    junitxml: Path,
    output: Path,
    additional_evidence: Sequence[Path] = (),
) -> None:
    for source in (junitxml, *_evidence_paths(), *additional_evidence):
        if _paths_alias(source, output):
            raise ValueError("--output must not overwrite release evidence inputs")
    if output.exists() and output.is_dir():
        raise ValueError("--output must be a file path")


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _project_metadata() -> tuple[str, str]:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml에 [project]가 필요합니다")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml에 project.name과 project.version이 필요합니다")
    return name, version


def _project_mcp_config() -> dict[str, object]:
    payload = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    remote = servers.get("fairpost") if isinstance(servers, dict) else None
    local = servers.get("fairpost-local") if isinstance(servers, dict) else None
    remote_headers = remote.get("headers") if isinstance(remote, dict) else None
    valid = bool(
        isinstance(remote, dict)
        and remote.get("type") == "http"
        and remote.get("url") == "https://fairmcp.vercel.app/api/mcp"
        and isinstance(remote_headers, dict)
        and remote_headers.get("Authorization")
        == "Bearer ${FAIRPOST_MCP_TOKEN}"
        and isinstance(local, dict)
        and local.get("type") == "http"
        and local.get("url") == "http://127.0.0.1:8000/mcp"
    )
    return {
        "valid": valid,
        "remote_url": remote.get("url") if isinstance(remote, dict) else None,
        "local_url": local.get("url") if isinstance(local, dict) else None,
        "authorization_uses_environment_reference": bool(
            isinstance(remote_headers, dict)
            and remote_headers.get("Authorization")
            == "Bearer ${FAIRPOST_MCP_TOKEN}"
        ),
    }


def _display_artifact_path(value: object) -> str:
    path = Path(str(value))
    try:
        return path.resolve(strict=False).relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def build_report(
    junitxml: Path,
    built_at: str | None = None,
    evidence_version_audit: Path | None = None,
    corpus_diversity_audit: Path | None = None,
    vercel_deployment_audit: Path | None = None,
    evaluation_report: Path | None = None,
) -> dict[str, object]:
    tests_passed, test_evidence_bytes, test_evidence_sha256 = (
        _load_passing_test_evidence(Path(junitxml))
    )
    test_freshness = _verify_junit_freshness(Path(junitxml))
    audit = json.loads(
        (ROOT / "reports" / "distribution_audit.json").read_text(encoding="utf-8")
    )
    _require_report_schema(
        audit,
        "fairpost-distribution-audit-v2",
        "distribution audit",
    )
    parity = json.loads(
        (ROOT / "reports" / "web_engine_parity.json").read_text(encoding="utf-8")
    )
    _require_report_schema(
        parity,
        "fairpost-web-engine-parity-v1",
        "web engine parity audit",
    )
    client = json.loads(
        (ROOT / "reports" / "mcp_client_audit.json").read_text(encoding="utf-8")
    )
    _require_report_schema(
        client,
        "fairpost-mcp-client-audit-v2",
        "MCP client audit",
    )
    work24 = json.loads(
        (ROOT / "reports" / "work24_access_audit.json").read_text(encoding="utf-8")
    )
    _require_report_schema(
        work24,
        "fairpost-work24-access-audit-v1",
        "Work24 access audit",
    )
    vercel_path = vercel_deployment_audit or (
        ROOT / "reports" / "vercel_deployment_audit.json"
    )
    vercel = json.loads(vercel_path.read_text(encoding="utf-8"))
    _require_report_schema(
        vercel,
        "fairpost-vercel-deployment-audit-v2",
        "Vercel deployment audit",
    )
    prd_corpus = json.loads(
        (ROOT / "reports" / "prd_corpus_summary.json").read_text(encoding="utf-8")
    )
    _require_report_schema(
        prd_corpus,
        "fairpost-prd-corpus-summary-v1",
        "PRD corpus summary",
    )
    human_handoff = json.loads(
        (ROOT / "reports" / "human_labeling_handoff.json").read_text(
            encoding="utf-8"
        )
    )
    _require_report_schema(
        human_handoff,
        "fairpost-human-labeling-handoff-v1",
        "human labeling handoff",
    )
    evidence_versions_path = evidence_version_audit or (
        ROOT / "reports" / "evidence_version_audit.json"
    )
    corpus_diversity_path = corpus_diversity_audit or (
        ROOT / "reports" / "corpus_diversity_audit.json"
    )
    evidence_versions = json.loads(
        evidence_versions_path.read_text(encoding="utf-8")
    )
    _require_report_schema(
        evidence_versions,
        "fairpost-evidence-version-audit-v1",
        "evidence version audit",
    )
    corpus_diversity = json.loads(
        corpus_diversity_path.read_text(encoding="utf-8")
    )
    _require_report_schema(
        corpus_diversity,
        "private-corpus-diversity-audit-v1",
        "corpus diversity audit",
    )
    evaluation_path = evaluation_report or (ROOT / "reports" / "evaluation.json")
    evaluation = (
        json.loads(evaluation_path.read_text(encoding="utf-8"))
        if evaluation_path.is_file()
        else None
    )
    if evaluation is not None:
        _require_report_schema(evaluation, 3, "evaluation report")
    package_name, package_version = _project_metadata()
    mcp_project_config = _project_mcp_config()
    ruleset = load_ruleset(ROOT / "data")
    local_runtime_fingerprint = runtime_source_fingerprint(
        ruleset_version=ruleset.version,
        matching_version=ruleset.matching_version,
        root=ROOT,
    )
    vercel_audit_sha256 = hashlib.sha256(vercel_path.read_bytes()).hexdigest()
    current_client_evidence = _current_client_evidence_matches(
        client,
        ruleset_version=ruleset.version,
        matching_version=ruleset.matching_version,
        runtime_source_fingerprint_value=local_runtime_fingerprint,
        deployment_id=vercel.get("deployment_id"),
        vercel_audit_sha256=vercel_audit_sha256,
        endpoint=f"{str(vercel.get('production_url', '')).rstrip('/')}/api/mcp",
        authentication=vercel.get("health", {}).get("authentication"),
    )
    distribution_matches_current_runtime = (
        audit.get("runtime_source_fingerprint") == local_runtime_fingerprint
    )
    current_distribution_source_fingerprint = distribution_source_fingerprint(ROOT)
    distribution_matches_current_source = (
        audit.get("distribution_source_fingerprint")
        == current_distribution_source_fingerprint
    )
    wheel_artifact = _audited_artifact(audit["wheel"], "wheel")
    sdist_artifact = _audited_artifact(audit["sdist"], "sdist")
    question_count = sum(rule["layer"] == "question" for rule in ruleset.rules)
    deployed_ruleset_version = vercel["health"].get("ruleset_version")
    deployed_matching_version = vercel["health"].get("matching_version")
    deployed_runtime_fingerprint = vercel["health"].get(
        "runtime_source_fingerprint"
    )
    deployment_matches_current_ruleset = (
        deployed_ruleset_version == ruleset.version
        and deployed_matching_version == ruleset.matching_version
        if deployed_ruleset_version is not None
        and deployed_matching_version is not None
        else None
    )
    release_tag, release_tag_note = _release_tag_evidence()
    current_evaluator_fingerprint = evaluator_fingerprint()
    blockers: list[dict[str, str]] = []
    human_evaluation_ready = bool(
        isinstance(evaluation, dict)
        and evaluation.get("ruleset_version") == ruleset.version
        and evaluation.get("matching_version") == ruleset.matching_version
        and evaluation.get("release_claim_eligible") is True
        and evaluation.get("evaluator_source_fingerprint")
        == current_evaluator_fingerprint
        and isinstance(evaluation.get("final_evaluation_receipt"), dict)
        and evaluation["final_evaluation_receipt"].get("status") == "bound"
        and isinstance(evaluation.get("target_gate"), dict)
        and evaluation["target_gate"].get("passed") is True
    )
    if not human_evaluation_ready:
        blockers.append(
            {
                "id": "human_holdout_labels",
                "reason": "봉인 홀드아웃 사람 라벨과 G1/G2 최종 평가가 없습니다.",
            }
        )
    if corpus_diversity.get("status") != "pass":
        blockers.append(
            {
                "id": "private_corpus_diversity",
                "reason": "민간 코퍼스 다양성 감사가 pass가 아닙니다.",
            }
        )
    if not (
        vercel.get("passed") is True
        and deployment_matches_current_ruleset is True
        and deployed_runtime_fingerprint == local_runtime_fingerprint
    ):
        blockers.append(
            {
                "id": "current_production_deployment",
                "reason": (
                    "운영 배포 증거가 현재 규칙·매칭·런타임 소스와 일치하지 않습니다."
                ),
            }
        )
    if not current_client_evidence:
        blockers.append(
            {
                "id": "current_external_client_evidence",
                "reason": "현재 커밋의 외부 MCP 클라이언트 증거가 없습니다.",
            }
        )
    if work24.get("result") not in {"success", "passed"}:
        blockers.append(
            {
                "id": "work24_source_access",
                "reason": "PRD Work24 출처 접근 증거가 충족되지 않았습니다.",
            }
        )
    if release_tag is None:
        blockers.append(
            {
                "id": "release_tag",
                "reason": "현재 HEAD에 단일 릴리스 태그가 없습니다.",
            }
        )
    report = {
        "schema_version": "fairpost-build-artifact-v2",
        "built_at": built_at
        or datetime.now(KST).isoformat(timespec="seconds"),
        "package": package_name,
        "version": package_version,
        "wheel": wheel_artifact,
        "sdist": sdist_artifact,
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "verification": {
            "tests_passed": tests_passed,
            "test_evidence_bytes": test_evidence_bytes,
            "test_evidence_sha256": test_evidence_sha256,
            "test_evidence_fresh_for_current_inputs": test_freshness[
                "fresh_for_current_inputs"
            ],
            "test_validation_source_fingerprint": test_freshness[
                "source_fingerprint"
            ],
            "test_validation_inputs_checked": test_freshness[
                "validation_inputs_checked"
            ],
            "data_rules": len(ruleset.rules),
            "question_cards": question_count,
            "statute_snapshots": len(ruleset.statutes),
            "web_parity_training_records": parity["input"]["records"],
            "web_parity_mismatches": parity["mismatched_records"],
            "prd_corpus_total": prd_corpus["total"],
            "prd_corpus_train": prd_corpus["train"],
            "prd_corpus_holdout": prd_corpus["holdout"],
            "prd_corpus_hash_overlap": prd_corpus["train_holdout_hash_overlap"],
            "human_labeling_status": human_handoff["status"],
            "human_labeling_holdout_records": human_handoff["holdout_records"],
            "human_final_evaluation_passed": human_evaluation_ready,
            "human_evaluation_evaluator_fingerprint_matches_current": bool(
                isinstance(evaluation, dict)
                and evaluation.get("evaluator_source_fingerprint")
                == current_evaluator_fingerprint
            ),
            "evidence_versions_passed": evidence_versions["passed"],
            "evidence_versions_reports_checked": evidence_versions[
                "reports_checked"
            ],
            "corpus_diversity_status": corpus_diversity["status"],
            "corpus_diversity_release_blocking": bool(
                corpus_diversity["release_policy"]["blocking"]
            ),
            "distribution_audit_passed": audit["passed"],
            "distribution_matches_current_runtime": (
                distribution_matches_current_runtime
            ),
            "distribution_matches_current_source": (
                distribution_matches_current_source
            ),
            "sdist_members": audit["sdist"]["members"],
            "wheel_members": audit["wheel"]["members"],
            "claude_project_http_registered": mcp_project_config["valid"],
            "mcp_project_config": mcp_project_config,
            "official_mcp_inspector_evidence_status": client.get(
                "evidence_status", "unclassified"
            ),
            "current_external_client_evidence_passed": current_client_evidence,
            "vercel_asgi_protocol_test_passed": (
                vercel["passed"]
                and vercel["health"]["status"] == "ok"
                and vercel["health"]["transport"] == "streamable-http"
                and vercel["checks"]["tool_call_succeeded"]
            ),
            "vercel_production_deployed": vercel["passed"],
            "vercel_production_url": vercel["production_url"],
            "vercel_deployed_ruleset_version": deployed_ruleset_version,
            "vercel_deployed_matching_version": deployed_matching_version,
            "vercel_deployment_matches_current_ruleset": (
                deployment_matches_current_ruleset
            ),
            "vercel_runtime_source_fingerprint": deployed_runtime_fingerprint,
            "vercel_runtime_source_fingerprint_matches_local": (
                deployed_runtime_fingerprint == local_runtime_fingerprint
            ),
            "vercel_remote_tool_call_passed": vercel["checks"][
                "tool_call_succeeded"
            ],
            "work24_live_recheck": work24["result"],
        },
        "release_tag": release_tag,
        "release_tag_note": release_tag_note,
        "release_readiness": {
            "status": "ready" if not blockers else "blocked",
            "strict_ready": not blockers,
            "blockers": blockers,
        },
        "historical_references": {
            "official_mcp_inspector": {
                "evidence_status": client.get("evidence_status", "unclassified"),
                "tool_call_passed": (
                    client["official_inspector"]["tool_call_exit_code"] == 0
                    and not client["official_inspector"]["is_error"]
                ),
            }
        },
    }
    return report


def validate_release_report(
    report: dict[str, object],
    *,
    allow_stale_deployment: bool = False,
    strict_release: bool = False,
) -> None:
    verification = report["verification"]
    if not isinstance(verification, dict):
        raise ValueError("release report verification section is invalid")
    if not verification["evidence_versions_passed"]:
        raise ValueError("release evidence version audit did not pass")
    if not verification["vercel_production_deployed"]:
        raise ValueError("Vercel production deployment audit did not pass")
    if not verification["vercel_asgi_protocol_test_passed"]:
        raise ValueError("Vercel production protocol audit did not pass")
    if not allow_stale_deployment:
        if not verification["vercel_deployment_matches_current_ruleset"]:
            raise ValueError("Vercel ruleset or matching version is stale")
        if not verification["vercel_runtime_source_fingerprint_matches_local"]:
            raise ValueError("Vercel runtime source fingerprint is stale")
    if not verification["distribution_audit_passed"]:
        raise ValueError("distribution audit did not pass")
    if not verification["distribution_matches_current_runtime"]:
        raise ValueError("distribution audit was built from stale runtime source")
    if not verification["distribution_matches_current_source"]:
        raise ValueError("distribution audit was built from stale project source")
    readiness = report["release_readiness"]
    if not isinstance(readiness, dict):
        raise ValueError("release readiness section is invalid")
    if strict_release and not readiness["strict_ready"]:
        blockers = readiness.get("blockers")
        blocker_ids = ", ".join(
            str(item["id"])
            for item in blockers
            if isinstance(item, dict) and "id" in item
        ) if isinstance(blockers, list) else "unknown"
        raise ValueError(f"strict release readiness is blocked: {blocker_ids}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the reproducible release evidence report."
    )
    parser.add_argument(
        "--junitxml",
        required=True,
        type=Path,
        help="JUnit XML from a complete passing pytest run.",
    )
    parser.add_argument(
        "--built-at",
        type=str,
        help="Optional ISO 8601 timestamp to embed in the report.",
    )
    parser.add_argument(
        "--evidence-version-audit",
        type=Path,
        default=ROOT / "reports" / "evidence_version_audit.json",
        help="Same-run evidence version audit JSON.",
    )
    parser.add_argument(
        "--corpus-diversity-audit",
        type=Path,
        default=ROOT / "reports" / "corpus_diversity_audit.json",
        help="Same-run corpus diversity audit JSON (informational for v0.3).",
    )
    parser.add_argument(
        "--vercel-deployment-audit",
        type=Path,
        default=ROOT / "reports" / "vercel_deployment_audit.json",
        help="Current production deployment verification JSON.",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        default=ROOT / "reports" / "evaluation.json",
        help="Optional sealed human-holdout evaluation JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/build_artifact.json"),
    )
    release_mode = parser.add_mutually_exclusive_group()
    release_mode.add_argument(
        "--strict-release",
        action="store_true",
        help=(
            "Explicitly select the default behavior: fail unless every documented "
            "v1 release-readiness condition passes."
        ),
    )
    release_mode.add_argument(
        "--candidate-report",
        action="store_true",
        help=(
            "Create a blocked candidate evidence report without claiming release "
            "readiness. Required by CI while human/external gates remain open."
        ),
    )
    parser.add_argument(
        "--allow-stale-deployment",
        action="store_true",
        help=(
            "Allow CI/PR evidence assembly when the committed production audit "
            "predates the candidate source. Never use this for a deployment release."
        ),
    )
    args = parser.parse_args(argv)
    if args.allow_stale_deployment and not args.candidate_report:
        parser.error("--allow-stale-deployment requires --candidate-report")
    if args.built_at is not None:
        try:
            datetime.fromisoformat(args.built_at)
        except ValueError as exc:
            raise SystemExit("--built-at must be a valid ISO 8601 timestamp") from exc
    try:
        validate_paths(
            args.junitxml,
            args.output,
            (
                args.evidence_version_audit,
                args.corpus_diversity_audit,
                args.vercel_deployment_audit,
                args.evaluation,
            ),
        )
        report = build_report(
            args.junitxml,
            built_at=args.built_at,
            evidence_version_audit=args.evidence_version_audit,
            corpus_diversity_audit=args.corpus_diversity_audit,
            vercel_deployment_audit=args.vercel_deployment_audit,
            evaluation_report=args.evaluation,
        )
        validate_release_report(
            report,
            allow_stale_deployment=args.allow_stale_deployment,
            strict_release=not args.candidate_report,
        )
        _atomic_write_text(
            args.output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Release report created: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
