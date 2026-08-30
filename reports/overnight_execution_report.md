# FairPost Overnight Execution Report (historical snapshot)

Date: 2026-08-30
Session start: 2026-08-30 14:36:36 KST
Session target end: 2026-08-30 22:36:36 KST
Workspace: `C:\workspace\fairmcp`
Branch: `agent/question-relevance-audit`
Status: eight-hour execution and validation complete; commit and push authorized by duration gate

Evidence status: historical. This report preserves the exact state of the 2026-08-30
eight-hour session and is not the current release record. The 2026-08-31 six-hour
Claude MCP × Codex re-audit added a fifth local tool, a third general remote tool, and
new deployment evidence. Use `reports/build_artifact.json`,
`reports/mcp_client_audit.json`, `reports/vercel_deployment_audit.json`, and
`docs/ai-agent-review-2026-08-31.md` for the current state.

## Objective

Execute the FairPost hardening and release-evidence plan through a sustained eight-hour
session, validate the candidate end to end, and commit and push only after the full
duration has elapsed.

## Product Direction Agreed

- FairPost is deterministic review support for hiring postings, not a fairness score,
  legal conclusion, pass/fail judge, ranking system, or autonomous decision maker.
- The primary path is on-device static web, CLI, and loopback MCP.
- Remote MCP remains read-only and minimum-privilege.
- Train-only public or private analysis is not evidence for the G1/G2 release claims.
- Human labels and a sealed holdout are required before strict v1.0 readiness can be
  claimed.
- The current deliverable is a hardened v0.3 candidate, not a strict v1.0 release.

The 30/60/90-day execution order is in `docs/roadmap.md`. Claude and Codex's
independent objections, consensus, and scope decisions are in
`docs/ai-agent-review-2026-08-30.md`.

## Agent Reviews

- Claude Code Sonnet performed a read-only product and architecture review.
- Independent agents reviewed product quality, CI, packaging, remote MCP security,
  privacy boundaries, and the final pull-request delta.
- The final code and security reviews found no P0 or P1 issue.
- Bandit 1.9.4 found 0 High, 1 Medium, and 13 Low findings. The Medium finding is the
  intentional `0.0.0.0` bind used only under Vercel; the public route remains protected
  by the remote read-only profile and authentication middleware.

## Material Changes

- Expanded and hardened the deterministic rule/question pipeline while preserving
  Python/static-web parity.
- Bound release evidence to current JUnit, validation-source, distribution-source,
  runtime, deployment, and external-client fingerprints.
- Kept four-tool answer storage on loopback and limited remote MCP to the two read-only
  tools `check_job_posting` and `next_review_question`.
- Added fail-closed request, response, redirect, credential, package-size, privacy, and
  source-equivalence checks.
- Added sealed-holdout attestation/receipt integrity controls and separated train review
  evidence from G1/G2 release claims.
- Added private train monitoring, drift, review-queue, offline review UI, and aggregate
  quality-gate tooling without packaging private inputs.
- Added bounded corpus downloads with declared and streamed oversize rejection.
- Restored the documented CLI contract: `fairpost check <file>` and
  `fairpost check -` now work while legacy `fairpost <file>` remains supported.
- Made stdin decoding honor `--encoding` at the raw-byte boundary and documented the
  UTF-8 PowerShell pipeline setup so Korean input is not lost on Windows PowerShell 5.
- Upgraded CI to pytest 9 and pinned bootstrap pip 26.2.1 and setuptools 84.0.0
  before dependency installation; `pip-audit` 2.10.1 remains a fail-closed CI gate.
- Ensured the sdist includes the private fairness regression fixture required to run its
  own test suite.

## Final Validation Evidence

- Full repository suite: 863 passed; current JUnit fingerprint is recorded in
  `reports/build_artifact.json`.
- Extracted final sdist suite: 863 passed independently on Python 3.11 and Python 3.14.
- Installed wheel smoke: Python 3.13 import, CLI `check -`, rule match, and dependency
  consistency passed.
- Static web parity: 2,310 train records, 0 mismatches.
- Rules/data validation: 71 rules, including 52 question cards and 6 statute snapshots.
- Evidence versions: 16 current reports, 0 stale; 16 historical reports remain explicitly
  marked historical.
- Distribution: passed with 195 sdist members and 36 wheel members; no credentials,
  sensitive packaged data, real posting text, forbidden paths, missing required files,
  or source mismatches.
- Runtime wheel dependency audit: no known vulnerabilities; `pip check` found no broken
  requirements.
- Reproducibility: two fixed-epoch builds produced byte-identical wheels and 178-file
  sdists with no content differences.
- Static checks: ruff, compileall, `git diff --check`, actionlint 1.7.12, dictionary
  validation, and web bundle consistency passed.
- Unicode stress: 1,200 seeded mixed-whitespace/zero-width inputs produced identical
  result pairs and valid original-text finding offsets without exceptions.
- Cross-engine Unicode stress: 500 synthetic Python/static-web comparisons passed after
  fixing BOM-prefixed slot evidence trimming and adding a permanent regression test.
- Engine benchmark over 420 train postings: 12,600 measured executions, 61.307
  postings/second, 16.311 ms mean, and 37.171 ms p95. This is not an operational SLA.
- Production verification: Bearer-protected read-only MCP at
  `https://fairmcp.vercel.app/api/mcp`, exact two-tool surface, real tool call passed,
  anonymous requests rejected, and deployed/local runtime fingerprints matched. A final
  live recheck of deployment `dpl_cUcfq9BHLii585uadhh3i32swww3` at 21:44 KST passed
  without recording the token or raw posting; the deployed static engine bytes also
  match the local candidate.

Current fingerprints:

- ruleset: `2026.07-local-v5-nfkc-whitespace-zero-width-regex-source-offset-7c381c142ac1`
- matching: `match-1e34d81e35e57b3f`
- runtime: `runtime-80e0b75fc38d7997605a3846116e24934227610f4e5e4fa9d1239ad9807fb594`
- distribution source: `distribution-c00dceeda2e8999ae9b88eea51011fdc153fca1641b02e8c9dfb72b01803005c`

## Findings Resolved During Execution

1. Concurrent source edits initially made a passing JUnit run stale; the suite was rerun
   after source freeze and its validation fingerprint now matches.
2. Evidence generation order can invalidate a distribution fingerprint. The final order
   is evidence inventory, test, package build, distribution audit, then candidate report.
3. A deployment recheck without the known deployment ID weakened client binding; the
   production audit and Inspector evidence were restored to the exact deployment ID and
   SHA-256 binding.
4. The sdist originally omitted a JSON regression fixture. Manifest and verifier rules now
   require it, and extracted-sdist tests pass on both supported Python boundaries.
5. The README's `fairpost check -` example did not match the CLI implementation. The CLI
   now supports that documented form and retains the older direct-file form.
6. Clean Python environments exposed advisories in bootstrap pip 25.0.1 and setuptools
   65.5.0. CI now upgrades to pip 26.2.1 and setuptools 84.0.0 before installing
   dependencies; the exact Python 3.11 CI audit then passed.
7. Seeded cross-engine fuzzing found one BOM-prefixed slot-evidence mismatch. The web
   engine now mirrors Python whitespace trimming while preserving the original evidence,
   and all 500 synthetic comparisons pass.
8. Windows PowerShell 5 uses an ASCII native pipeline by default and Python can select a
   different text encoding. The CLI now decodes raw stdin bytes with `--encoding`, the
   README fixes the UTF-8 pipeline boundary, and an independent regression test protects
   this path.
9. The distribution source fingerprint treated several packaged text extensions as
   binary, so Git-index CRLF bytes could disagree with the validated worktree after a
   commit. Canonicalization now covers CJS, JSONL, manifest, license, and ignore text;
   the exported staged tree and worktree produce the same fingerprint.
10. Python's AST dump shape differed across 3.11, 3.12, and 3.14, making runtime and
    distribution evidence interpreter-dependent. Distribution evidence now hashes
    normalized source text, while runtime identity normalizes supported AST versions to
    one shape. All three Python versions produce identical current fingerprints.
11. The first pushed CI run caught a stale ruleset and matching version in the pilot
    feedback example. The example now carries the current versions, the exact CI command
    passes locally, and package/distribution evidence is rebound to the corrected source.

## Release Posture

The v0.3 candidate evidence path is healthy, but strict v1.0 release readiness remains
blocked by exactly four external/human conditions:

1. `human_holdout_labels`: sealed 180-record holdout human labels and final G1/G2 result
2. `private_corpus_diversity`: approved independent private-source diversity
3. `work24_source_access`: authorized Work24 source access or approved PRD source change
4. `release_tag`: a release tag on the finally approved commit

No score, legal judgment, production SLA, G1/G2 pass, or v1 readiness claim is made from
the current train-only and synthetic evidence.

## Session Close

- The eight-hour gate passed at or after 2026-08-30 22:36:36 KST.
- Final staged secret/privacy/diff and fingerprint binding checks passed.
- The candidate is ready for commit, push, and GitHub Actions monitoring.
