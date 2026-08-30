from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
from typing import Iterable, Sequence
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402
from mcp_server.build_identity import (  # noqa: E402
    RUNTIME_SOURCE_FILES,
    runtime_source_fingerprint,
)
SDIST_REQUIRED = {
    "MANIFEST.in",
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "LICENSE-DATA",
    "pyproject.toml",
    ".mcp.json",
    ".vercelignore",
    "vercel.json",
    "index.html",
    "favicon.svg",
    "api/index.py",
    "core/engine.py",
    "mcp_server/server.py",
    "mcp_server/remote.py",
    "mcp_server/build_identity.py",
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
    "tools/audit_question_relevance.py",
    "tools/verify_distribution.py",
    "tools/build_release_report.py",
    "tools/verify_vercel_deployment.py",
    "tools/release_inputs.py",
    "tools/check_evidence_versions.py",
    "tools/benchmark_engine.py",
    "tools/summarize_pilot_feedback.py",
    "tools/audit_corpus_diversity.py",
    "tools/build_private_monitoring_snapshot.py",
    "tools/audit_private_fairness.py",
    "tools/run_private_monitoring.py",
    "tools/check_private_fairness_drift.py",
    "tools/build_private_review_queue.py",
    "tools/build_private_review_ui.py",
    "tools/build_private_review_sampling_audit.py",
    "tools/summarize_private_review.py",
    "tools/run_private_fairness_cycle.py",
    "tools/js_batch_runner.cjs",
    "tests/fixtures/private_fairness_cases.json",
    "docs/completion-audit.md",
    "docs/acceptance.md",
    "docs/prd-traceability.md",
    "docs/ncs-fairness-research-bundle.json",
    "docs/evaluation.md",
    "docs/mcp-clients.md",
    "docs/question-relevance-audit.md",
    "docs/private-fairness-monitoring.md",
    "docs/private-fairness-research-bundle.json",
    "docs/evaluation-protocol.md",
    "docs/evidence-versioning.md",
    "docs/pilot-protocol.md",
    "docs/corpus-diversity-gate.md",
    "docs/performance.md",
    "docs/roadmap.md",
    "examples/private_monitoring_input.example.jsonl",
    "examples/pilot_feedback.example.jsonl",
    "reports/final_corpus_summary.json",
    "reports/prd_corpus_summary.json",
    "reports/human_labeling_handoff.json",
    "reports/mcp_client_audit.json",
    "reports/question_relevance_audit.json",
    "reports/question_relevance_manual_review.json",
    "reports/private_fairness_audit.json",
    "reports/private_fairness_case_matrix.json",
    "reports/private_review_model_triage.json",
    "reports/private_review_sampling_audit.json",
    "reports/private_age_context_audit.json",
    "reports/private_family_evidence_context_audit.json",
    "reports/private_result_notice_context_audit.json",
    "reports/private_gender_context_audit.json",
    "reports/private_return_context_audit.json",
    "reports/private_nationality_context_audit.json",
    "reports/private_religion_context_audit.json",
    "reports/private_marital_context_audit.json",
    "reports/private_criminal_record_context_audit.json",
    "reports/private_residence_vehicle_context_audit.json",
    "reports/private_military_proxy_context_audit.json",
    "reports/private_preference_process_context_audit.json",
    "reports/private_health_job_relevance_context_audit.json",
    "reports/corpus_diversity_audit.json",
    "reports/evidence_version_audit.json",
    "reports/engine_performance.json",
    "reports/vercel_deployment_audit.json",
    ".github/workflows/ci.yml",
    ".github/workflows/statute-snapshot-audit.yml",
}
SDIST_SOURCE_PATTERNS = (
    "api/**/*.py",
    "cli/**/*.py",
    "core/**/*.py",
    "mcp_server/**/*.py",
    "data/**/*.yaml",
    "web/**/*.html",
    "web/**/*.css",
    "web/**/*.js",
    "tools/**/*.py",
    "tools/**/*.cjs",
    "tests/**/*.py",
    "tests/**/*.cjs",
    "tests/**/*.json",
    "docs/**/*.md",
    "docs/**/*.json",
    "reports/**/*.json",
    ".github/**/*.yml",
    ".github/**/*.yaml",
)
WHEEL_REQUIRED = {
    "core/engine.py",
    "core/loader.py",
    "cli/main.py",
    "mcp_server/server.py",
    "mcp_server/build_identity.py",
}
PACKAGE_SOURCE_DIRS = ("api", "cli", "core", "mcp_server", "data", "web")
TEXT_SOURCE_SUFFIXES = {
    ".bat",
    ".cjs",
    ".css",
    ".html",
    ".in",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_SOURCE_NAMES = {".vercelignore", "LICENSE", "LICENSE-DATA"}
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
    ".private-review",
    "tmp",
}
MAX_SECURITY_SCAN_BYTES = 64 * 1024 * 1024
SYNTHETIC_POSTING_MEMBERS = {
    "examples/private_monitoring_input.example.jsonl",
}
SYNTHETIC_PRIVACY_EXAMPLES = {
    "web/app.js": (
        b"02-1234-5678",
        b"recruit@example.com",
    ),
}
REPORT_SENSITIVE_KEYS = {
    "company",
    "company_name",
    "email",
    "organization",
    "organization_name",
    "phone",
    "posting_text",
    "raw_text",
    "record_id",
    "snippet",
    "source_url",
    "text",
}
PRIVATE_RECORD_ID_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9_-])(?:jincheon-jobs|job-alio|cleaneye|narailter):[A-Za-z0-9_-]+"
)
CREDENTIAL_PATTERNS = {
    "aws_access_key": re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    "github_token": re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,255}"),
    "api_key": re.compile(
        rb"(?<![A-Za-z0-9])(?:sk|rk|pk)-(?:proj-)?[A-Za-z0-9_-]{20,255}"
    ),
    "bearer_token": re.compile(
        rb"Bearer[ \t]+(?!\$\{)(?!<)[A-Za-z0-9][A-Za-z0-9._~-]{19,255}"
    ),
    "pem_private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "jwt": re.compile(
        rb"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    "credentialed_dsn": re.compile(
        rb"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"
        rb"[^\s:/]+:[^\s@/]+@"
    ),
}
ASSET_PRIVACY_PATTERNS = {
    "email": re.compile(
        rb"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
        rb"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
    ),
    "korean_phone": re.compile(
        rb"(?<![0-9])0(?:2|[3-6][1-5]|70|10)[ -]?[0-9]{3,4}[ -]?[0-9]{4}(?![0-9])"
    ),
    "resident_registration_number": re.compile(
        rb"(?<![0-9])[0-9]{6}-[1-4][0-9]{6}(?![0-9])"
    ),
    "private_record_id": PRIVATE_RECORD_ID_PATTERN,
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


def _package_source_files(root_path: Path = ROOT) -> set[str]:
    files: set[str] = set(RUNTIME_SOURCE_FILES)
    for directory in PACKAGE_SOURCE_DIRS:
        root = root_path / directory
        if not root.is_dir():
            continue
        files.update(
            path.relative_to(root_path).as_posix()
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return files


def _sdist_source_files(root_path: Path = ROOT) -> set[str]:
    files = set(SDIST_REQUIRED)
    for pattern in SDIST_SOURCE_PATTERNS:
        for path in root_path.glob(pattern):
            if not path.is_file() or path.name.casefold() in FORBIDDEN_NAMES:
                continue
            relative_path = path.relative_to(root_path)
            if any(
                part.casefold() in FORBIDDEN_PARTS
                or part.casefold().startswith(".corpus")
                for part in relative_path.parts
            ):
                continue
            files.add(relative_path.as_posix())
    files.update(
        relative
        for relative in (
            "examples/private_monitoring_input.example.jsonl",
            "examples/pilot_feedback.example.jsonl",
        )
        if (root_path / relative).is_file()
    )
    return files


def _canonical_source_payload(relative: str, payload: bytes) -> bytes:
    path = Path(relative)
    if (
        path.suffix.casefold() not in TEXT_SOURCE_SUFFIXES
        and path.name not in TEXT_SOURCE_NAMES
    ):
        return payload
    try:
        text = payload.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        return payload
    return text.encode("utf-8")


def distribution_source_fingerprint(root_path: Path = ROOT) -> str:
    files = _sdist_source_files(root_path) | _package_source_files(root_path)
    digest = hashlib.sha256(b"fairpost-distribution-source-v2\0")
    for relative in sorted(files):
        path = root_path / relative
        if not path.is_file():
            continue
        payload = _canonical_source_payload(relative, path.read_bytes())
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, byteorder="big", signed=False))
        digest.update(payload)
        digest.update(b"\0")
    return f"distribution-{digest.hexdigest()}"


def _sdist_source_mismatches(
    archive: tarfile.TarFile,
    members_by_path: dict[str, tarfile.TarInfo],
) -> list[str]:
    mismatches: list[str] = []
    for relative in sorted(_sdist_source_files() | _package_source_files()):
        source = ROOT / relative
        if not source.is_file():
            continue
        member = members_by_path.get(relative)
        extracted = archive.extractfile(member) if member is not None else None
        if extracted is None or _canonical_source_payload(
            relative, extracted.read()
        ) != _canonical_source_payload(relative, source.read_bytes()):
            mismatches.append(relative)
    return mismatches


def _wheel_member_name(relative: str, names: set[str]) -> str | None:
    if relative.startswith(("data/", "web/")):
        suffix = f"/share/fairpost/{relative}"
        return next((name for name in names if name.endswith(suffix)), None)
    return relative if relative in names else None


def _wheel_source_mismatches(
    archive: zipfile.ZipFile,
    names: set[str],
) -> list[str]:
    mismatches: list[str] = []
    wheel_files = {
        relative
        for relative in _package_source_files()
        if relative.startswith(("cli/", "core/", "mcp_server/", "data/", "web/"))
    }
    for relative in sorted(wheel_files):
        source = ROOT / relative
        member_name = _wheel_member_name(relative, names)
        if member_name is None or _canonical_source_payload(
            relative, archive.read(member_name)
        ) != _canonical_source_payload(relative, source.read_bytes()):
            mismatches.append(relative)
    return mismatches


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


def _credential_kinds(payload: bytes) -> list[str]:
    return sorted(
        name for name, pattern in CREDENTIAL_PATTERNS.items() if pattern.search(payload)
    )


def _privacy_kinds(payload: bytes) -> list[str]:
    return sorted(
        name
        for name, pattern in ASSET_PRIVACY_PATTERNS.items()
        if pattern.search(payload)
    )


def _privacy_scan_payload(relative: str, payload: bytes) -> bytes:
    """Remove only reviewed, exact synthetic literals from a packaged asset."""

    for literal in SYNTHETIC_PRIVACY_EXAMPLES.get(relative, ()):
        payload = payload.replace(literal, b"<reviewed-synthetic-example>")
    return payload


def _is_privacy_asset(relative: str) -> bool:
    path = PurePosixPath(relative)
    if not path.parts:
        return False
    return (
        path.parts[0] in {"data", "docs", "examples", "reports", "web"}
        or relative in {"README.md", "index.html"}
    ) and path.suffix.casefold() in TEXT_SOURCE_SUFFIXES | {".jsonl"}


def _tar_credential_findings(archive: tarfile.TarFile) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for member in archive.getmembers():
        if not member.isfile():
            continue
        if member.size > MAX_SECURITY_SCAN_BYTES:
            findings.append(
                {"member": member.name, "kinds": ["oversized_unscanned_member"]}
            )
            continue
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        kinds = _credential_kinds(extracted.read())
        if kinds:
            findings.append({"member": member.name, "kinds": kinds})
    return findings


def _zip_credential_findings(archive: zipfile.ZipFile) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        if info.file_size > MAX_SECURITY_SCAN_BYTES:
            findings.append(
                {"member": info.filename, "kinds": ["oversized_unscanned_member"]}
            )
            continue
        kinds = _credential_kinds(archive.read(info))
        if kinds:
            findings.append({"member": info.filename, "kinds": kinds})
    return findings


def _sensitive_json_pointers(value: object, pointer: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{key}"
            if key.casefold() in REPORT_SENSITIVE_KEYS and item not in (
                None,
                "",
                False,
                [],
                {},
            ):
                findings.append(child)
            findings.extend(_sensitive_json_pointers(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_sensitive_json_pointers(item, f"{pointer}/{index}"))
    return findings


def _tar_report_privacy_findings(
    archive: tarfile.TarFile,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if (
            not member.isfile()
            or member.size > MAX_SECURITY_SCAN_BYTES
            or "reports" not in path.parts
            or path.suffix.casefold() != ".json"
        ):
            continue
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        payload = extracted.read()
        kinds: list[str] = []
        pointers: list[str] = []
        if PRIVATE_RECORD_ID_PATTERN.search(payload):
            kinds.append("private_record_id")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            kinds.append("invalid_report_json")
        else:
            pointers = _sensitive_json_pointers(value)
            if pointers:
                kinds.append("posting_or_identity_field")
        if kinds:
            findings.append(
                {
                    "member": member.name,
                    "kinds": kinds,
                    "json_pointers": pointers,
                }
            )
    return findings


def _tar_asset_privacy_findings(
    archive: tarfile.TarFile,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if not member.isfile() or len(path.parts) < 2:
            continue
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        if not _is_privacy_asset(relative):
            continue
        if member.size > MAX_SECURITY_SCAN_BYTES:
            findings.append(
                {"member": member.name, "kinds": ["oversized_unscanned_member"]}
            )
            continue
        extracted = archive.extractfile(member)
        if extracted is None:
            findings.append(
                {"member": member.name, "kinds": ["unreadable_asset"]}
            )
            continue
        kinds = _privacy_kinds(_privacy_scan_payload(relative, extracted.read()))
        if kinds:
            findings.append({"member": member.name, "kinds": kinds})
    return findings


def _zip_asset_privacy_findings(
    archive: zipfile.ZipFile,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    marker = "/share/fairpost/"
    for info in archive.infolist():
        if info.is_dir() or marker not in info.filename:
            continue
        relative = info.filename.split(marker, 1)[1]
        if not _is_privacy_asset(relative):
            continue
        if info.file_size > MAX_SECURITY_SCAN_BYTES:
            findings.append(
                {"member": info.filename, "kinds": ["oversized_unscanned_member"]}
            )
            continue
        kinds = _privacy_kinds(
            _privacy_scan_payload(relative, archive.read(info.filename))
        )
        if kinds:
            findings.append({"member": info.filename, "kinds": kinds})
    return findings


def inspect_sdist(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        archive_members = archive.getmembers()
        names = _strip_sdist_root(member.name for member in archive_members)
        members_by_path = {
            PurePosixPath(*PurePosixPath(member.name).parts[1:]).as_posix(): member
            for member in archive_members
            if member.isfile() and len(PurePosixPath(member.name).parts) >= 2
        }
        credential_findings = _tar_credential_findings(archive)
        privacy_findings = _tar_report_privacy_findings(archive)
        asset_privacy_findings = _tar_asset_privacy_findings(archive)
        source_mismatches = _sdist_source_mismatches(archive, members_by_path)
    missing = sorted(SDIST_REQUIRED - names)
    forbidden = _forbidden(names)
    return {
        "path": str(path).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "members": len(names),
        "missing_required": missing,
        "forbidden_members": forbidden,
        "credential_findings": credential_findings,
        "report_privacy_findings": privacy_findings,
        "asset_privacy_findings": asset_privacy_findings,
        "source_mismatches": source_mismatches,
        "source_equivalent": not source_mismatches,
        "synthetic_posting_members": sorted(SYNTHETIC_POSTING_MEMBERS & names),
        "passed": (
            not missing
            and not forbidden
            and not credential_findings
            and not privacy_findings
            and not asset_privacy_findings
            and not source_mismatches
        ),
    }


def inspect_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        credential_findings = _zip_credential_findings(archive)
        asset_privacy_findings = _zip_asset_privacy_findings(archive)
        source_mismatches = _wheel_source_mismatches(archive, names)
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
        "credential_findings": credential_findings,
        "asset_privacy_findings": asset_privacy_findings,
        "source_mismatches": source_mismatches,
        "source_equivalent": not source_mismatches,
        "passed": (
            not missing
            and not forbidden
            and not credential_findings
            and not asset_privacy_findings
            and not source_mismatches
        ),
    }


def _latest(pattern: str) -> Path:
    candidates = sorted(
        (path for path in ROOT.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not candidates:
        raise FileNotFoundError(f"배포물을 찾을 수 없습니다: {pattern}")
    return candidates[-1]


def _paths_alias(left: Path, right: Path) -> bool:
    """Return whether two paths name the same entry or existing file."""

    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _validate_paths(sdist: Path, wheel: Path, output: Path) -> None:
    labeled = (("sdist", sdist), ("wheel", wheel), ("output", output))
    for index, (left_name, left_path) in enumerate(labeled):
        for right_name, right_path in labeled[index + 1 :]:
            if _paths_alias(left_path, right_path):
                raise ValueError(
                    f"{left_name}와 {right_name} 경로는 같거나 같은 파일을 가리킬 수 없습니다"
                )


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
        try:
            staged.unlink(missing_ok=True)
        finally:
            raise


def main(argv: Sequence[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
    sdist = args.sdist or _latest("dist/fairpost-*.tar.gz")
    wheel = args.wheel or _latest("dist/fairpost-*.whl")
    try:
        _validate_paths(sdist, wheel, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    sdist_report = inspect_sdist(sdist)
    wheel_report = inspect_wheel(wheel)
    ruleset = load_ruleset(ROOT / "data")
    report = {
        "schema_version": "fairpost-distribution-audit-v2",
        "distribution_source_fingerprint": distribution_source_fingerprint(ROOT),
        "runtime_source_fingerprint": runtime_source_fingerprint(
            ruleset_version=ruleset.version,
            matching_version=ruleset.matching_version,
            root=ROOT,
        ),
        "contains_posting_text": bool(sdist_report["synthetic_posting_members"]),
        "contains_real_posting_text": bool(
            sdist_report["report_privacy_findings"]
        ),
        "posting_text_scope": (
            "synthetic_examples_only"
            if sdist_report["synthetic_posting_members"]
            and not sdist_report["report_privacy_findings"]
            else "none"
        ),
        "contains_credentials": bool(
            sdist_report["credential_findings"]
            or wheel_report["credential_findings"]
        ),
        "contains_sensitive_packaged_data": bool(
            sdist_report["report_privacy_findings"]
            or sdist_report["asset_privacy_findings"]
            or wheel_report["asset_privacy_findings"]
        ),
        "sdist": sdist_report,
        "wheel": wheel_report,
    }
    report["passed"] = bool(
        report["sdist"]["passed"] and report["wheel"]["passed"]
    )
    _atomic_write_text(
        args.output,
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    print(
        f"sdist {report['sdist']['members']}개, "
        f"wheel {report['wheel']['members']}개 파일 검사: "
        f"{'통과' if report['passed'] else '실패'}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
