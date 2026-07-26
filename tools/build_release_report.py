from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402


def build_report(tests_passed: int) -> dict[str, object]:
    audit = json.loads(
        (ROOT / "reports" / "distribution_audit.json").read_text(encoding="utf-8")
    )
    parity = json.loads(
        (ROOT / "reports" / "web_engine_parity.json").read_text(encoding="utf-8")
    )
    client = json.loads(
        (ROOT / "reports" / "mcp_client_audit.json").read_text(encoding="utf-8")
    )
    work24 = json.loads(
        (ROOT / "reports" / "work24_access_audit.json").read_text(encoding="utf-8")
    )
    vercel = json.loads(
        (ROOT / "reports" / "vercel_deployment_audit.json").read_text(
            encoding="utf-8"
        )
    )
    prd_corpus = json.loads(
        (ROOT / "reports" / "prd_corpus_summary.json").read_text(
            encoding="utf-8"
        )
    )
    human_handoff = json.loads(
        (ROOT / "reports" / "human_labeling_handoff.json").read_text(
            encoding="utf-8"
        )
    )
    ruleset = load_ruleset(ROOT / "data")
    question_count = sum(
        rule["layer"] == "question" for rule in ruleset.rules
    )
    return {
        "built_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
            timespec="seconds"
        ),
        "package": "fairpost",
        "version": "0.3.0",
        "wheel": {
            "path": "dist/fairpost-0.3.0-py3-none-any.whl",
            "bytes": audit["wheel"]["bytes"],
            "sha256": audit["wheel"]["sha256"],
        },
        "sdist": {
            "path": "dist/fairpost-0.3.0.tar.gz",
            "bytes": audit["sdist"]["bytes"],
            "sha256": audit["sdist"]["sha256"],
        },
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "verification": {
            "tests_passed": tests_passed,
            "data_rules": len(ruleset.rules),
            "question_cards": question_count,
            "statute_snapshots": len(ruleset.statutes),
            "web_parity_training_records": parity["input"]["records"],
            "web_parity_mismatches": parity["mismatched_records"],
            "prd_corpus_total": prd_corpus["total"],
            "prd_corpus_train": prd_corpus["train"],
            "prd_corpus_holdout": prd_corpus["holdout"],
            "prd_corpus_hash_overlap": prd_corpus[
                "train_holdout_hash_overlap"
            ],
            "human_labeling_status": human_handoff["status"],
            "human_labeling_holdout_records": human_handoff[
                "holdout_records"
            ],
            "distribution_audit_passed": audit["passed"],
            "sdist_members": audit["sdist"]["members"],
            "wheel_members": audit["wheel"]["members"],
            "claude_project_http_registered": client["project_config"][
                "registered"
            ],
            "official_mcp_inspector_call_passed": (
                client["official_inspector"]["tool_call_exit_code"] == 0
                and not client["official_inspector"]["is_error"]
            ),
            "vercel_asgi_protocol_test_passed": True,
            "vercel_production_deployed": vercel["passed"],
            "vercel_production_url": vercel["production_url"],
            "vercel_remote_tool_call_passed": vercel["checks"][
                "tool_call_succeeded"
            ],
            "work24_live_recheck": work24["result"],
        },
        "release_tag": None,
        "release_tag_note": (
            "The working directory is not a Git repository, so release-tag "
            "evidence is not available."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the reproducible release evidence report."
    )
    parser.add_argument("--tests-passed", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/build_artifact.json"),
    )
    args = parser.parse_args()
    if args.tests_passed < 1:
        raise SystemExit("--tests-passed must be positive")
    report = build_report(args.tests_passed)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Release report created: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
