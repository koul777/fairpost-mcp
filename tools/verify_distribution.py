from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Iterable
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SDIST_REQUIRED = {
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "LICENSE-DATA",
    "pyproject.toml",
    ".mcp.json",
    ".vercelignore",
    "vercel.json",
    "index.html",
    "api/index.py",
    "core/engine.py",
    "mcp_server/server.py",
    "mcp_server/remote.py",
    "web/index.html",
    "data/rules/law.yaml",
    "tools/collect_corpus.py",
    "tools/build_prd_corpus.py",
    "tools/build_annotation_ui.py",
    "tools/build_human_labeling_handoff.py",
    "tools/mine_candidates.py",
    "tools/normalize_candidates.py",
    "tools/build_statutes.py",
    "tools/evaluate.py",
    "tools/verify_web_parity.py",
    "tools/verify_distribution.py",
    "tools/verify_vercel_deployment.py",
    "tools/js_batch_runner.cjs",
    "docs/completion-audit.md",
    "docs/ncs-fairness-research-bundle.json",
    "docs/evaluation.md",
    "docs/mcp-clients.md",
    "docs/question-relevance-audit.md",
    "reports/final_corpus_summary.json",
    "reports/prd_corpus_summary.json",
    "reports/human_labeling_handoff.json",
    "reports/mcp_client_audit.json",
    "reports/question_relevance_audit.json",
    "reports/question_relevance_manual_review.json",
    "reports/vercel_deployment_audit.json",
    ".github/workflows/ci.yml",
    ".github/workflows/statute-snapshot-audit.yml",
}
WHEEL_REQUIRED = {
    "core/engine.py",
    "core/loader.py",
    "cli/main.py",
    "mcp_server/server.py",
}
FORBIDDEN_NAMES = {
    ".env",
    "answers.json",
    "annotations.jsonl",
    "labeler.html",
    "llm_tasks.jsonl",
    "private_open_candidate_batches.jsonl",
    "build_artifact.json",
    "distribution_audit.json",
}
FORBIDDEN_PARTS = {
    "__pycache__",
    ".corpus",
    "tmp",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_sdist_root(names: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for name in names:
        path = PurePosixPath(name)
        if len(path.parts) < 2:
            continue
        normalized.add(PurePosixPath(*path.parts[1:]).as_posix())
    return normalized


def _forbidden(names: set[str]) -> list[str]:
    violations: list[str] = []
    for name in sorted(names):
        path = PurePosixPath(name)
        lowered_parts = {part.casefold() for part in path.parts}
        if path.name.casefold() in FORBIDDEN_NAMES:
            violations.append(name)
            continue
        if any(
            part.startswith(".corpus") or part in FORBIDDEN_PARTS
            for part in lowered_parts
        ):
            violations.append(name)
            continue
        if name.startswith("reports/") and path.suffix.casefold() == ".jsonl":
            violations.append(name)
    return violations


def inspect_sdist(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        names = _strip_sdist_root(member.name for member in archive.getmembers())
    missing = sorted(SDIST_REQUIRED - names)
    forbidden = _forbidden(names)
    return {
        "path": str(path).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "members": len(names),
        "missing_required": missing,
        "forbidden_members": forbidden,
        "passed": not missing and not forbidden,
    }


def inspect_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    missing = sorted(WHEEL_REQUIRED - names)
    data_required = {
        "data/slots.yaml",
        "data/rules/law.yaml",
        "data/rules/questions.yaml",
        "data/statutes/recruitment-procedure-act.yaml",
        "web/index.html",
        "web/data.js",
        "web/engine.js",
    }
    for suffix in data_required:
        if not any(name.endswith(f"/share/fairpost/{suffix}") for name in names):
            missing.append(f"*/share/fairpost/{suffix}")
    forbidden = _forbidden(names)
    return {
        "path": str(path).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "members": len(names),
        "missing_required": sorted(missing),
        "forbidden_members": forbidden,
        "passed": not missing and not forbidden,
    }


def _latest(pattern: str) -> Path:
    candidates = sorted(
        (path for path in ROOT.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not candidates:
        raise FileNotFoundError(f"배포물을 찾을 수 없습니다: {pattern}")
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "sdist와 wheel에 PRD 필수 소스ㆍ런타임 파일이 있고 "
            "키ㆍ공고 원문ㆍ라벨 파일이 없는지 검사합니다."
        )
    )
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "distribution_audit.json",
    )
    args = parser.parse_args()
    sdist = args.sdist or _latest("dist/fairpost-*.tar.gz")
    wheel = args.wheel or _latest("dist/fairpost-*.whl")
    report = {
        "contains_posting_text": False,
        "contains_credentials": False,
        "sdist": inspect_sdist(sdist),
        "wheel": inspect_wheel(wheel),
    }
    report["passed"] = bool(
        report["sdist"]["passed"] and report["wheel"]["passed"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"sdist {report['sdist']['members']}개, "
        f"wheel {report['wheel']['members']}개 파일 검사: "
        f"{'통과' if report['passed'] else '실패'}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
