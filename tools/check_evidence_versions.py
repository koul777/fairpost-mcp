from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), name="KST")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402


OPERATIONAL_REPORTS = {
    "build_artifact.json",
    "vercel_deployment_audit.json",
}
EXPECTED_REPORT_SCHEMAS = {
    "build_artifact.json": "fairpost-build-artifact-v2",
    "corpus_diversity_audit.json": "private-corpus-diversity-audit-v1",
    "distribution_audit.json": "fairpost-distribution-audit-v2",
    "evaluation.json": 3,
    "human_labeling_handoff.json": "fairpost-human-labeling-handoff-v1",
    "mcp_client_audit.json": "fairpost-mcp-client-audit-v2",
    "prd_corpus_summary.json": "fairpost-prd-corpus-summary-v1",
    "vercel_deployment_audit.json": "fairpost-vercel-deployment-audit-v3",
    "web_engine_parity.json": "fairpost-web-engine-parity-v1",
    "work24_access_audit.json": "fairpost-work24-access-audit-v1",
}


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


def validate_output_path(reports_dir: Path, output: Path) -> None:
    for path in reports_dir.glob("*.json"):
        if path.name == "evidence_version_audit.json":
            continue
        if _paths_alias(path, output):
            raise ValueError("증거 버전 감사 출력은 다른 증거 보고서를 덮어쓸 수 없습니다")
    if output.exists() and output.is_dir():
        raise ValueError("증거 버전 감사 출력은 파일 경로여야 합니다")


def _version_pair(name: str, payload: dict[str, Any]) -> tuple[Any, Any]:
    if name == "vercel_deployment_audit.json":
        health = payload.get("health")
        if not isinstance(health, dict):
            return None, None
        return health.get("ruleset_version"), health.get("matching_version")
    return payload.get("ruleset_version"), payload.get("matching_version")


def audit(reports_dir: Path, *, scope: str, output: Path) -> dict[str, Any]:
    ruleset = load_ruleset(ROOT / "data")
    rows: list[dict[str, Any]] = []
    skipped_historical: list[str] = []
    for path in sorted(reports_dir.glob("*.json")):
        if path.resolve(strict=False) == output.resolve(strict=False):
            continue
        operational = path.name in OPERATIONAL_REPORTS
        if scope == "local" and operational:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "scope": "operational" if operational else "local",
                    "status": "invalid_json",
                    "error": type(exc).__name__,
                }
            )
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("evidence_status") == "historical":
            skipped_historical.append(path.relative_to(ROOT).as_posix())
            continue
        version, matching_version = _version_pair(path.name, payload)
        expected_schema = EXPECTED_REPORT_SCHEMAS.get(path.name)
        schema_matches = (
            payload.get("schema_version") == expected_schema
            if expected_schema is not None
            else None
        )
        if (
            version is None
            and matching_version is None
            and expected_schema is None
        ):
            continue
        ruleset_matches = version == ruleset.version if version is not None else None
        matching_matches = (
            matching_version == ruleset.matching_version
            if matching_version is not None
            else None
        )
        current = (
            ruleset_matches is not False
            and matching_matches is not False
            and schema_matches is not False
        )
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "scope": "operational" if operational else "local",
                "status": (
                    "current"
                    if current
                    else "stale"
                ),
                "schema_version": payload.get("schema_version"),
                "expected_schema_version": expected_schema,
                "schema_version_matches": schema_matches,
                "ruleset_version": version,
                "matching_version": matching_version,
                "ruleset_version_matches": ruleset_matches,
                "matching_version_matches": matching_matches,
            }
        )
    stale = [row["path"] for row in rows if row["status"] != "current"]
    return {
        "schema_version": "fairpost-evidence-version-audit-v1",
        "checked_at": datetime.now(KST).isoformat(
            timespec="seconds"
        ),
        "scope": scope,
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "reports_checked": len(rows),
        "reports_current": len(rows) - len(stale),
        "reports_stale": len(stale),
        "stale_paths": stale,
        "historical_paths_skipped": skipped_historical,
        "reports": rows,
        "passed": not stale and bool(rows),
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "reports/*.json의 규칙셋ㆍ매칭 버전이 현재 엔진과 같은지 검사합니다."
        )
    )
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--scope", choices=("local", "all"), default="local")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "evidence_version_audit.json",
    )
    args = parser.parse_args(argv)
    try:
        validate_output_path(args.reports_dir, args.output)
        report = audit(args.reports_dir, scope=args.scope, output=args.output)
        _atomic_write(
            args.output,
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"증거 버전 {report['reports_checked']}개 검사: "
        f"현재 {report['reports_current']}개, stale {report['reports_stale']}개"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
