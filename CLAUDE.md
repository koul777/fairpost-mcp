# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

FairPost is a deterministic reviewer for Korean job postings (채용공고). It flags wording to re-check against statutes (`law` findings), reports procedures/information it could not find in the posting (slots), and produces follow-up questions for HR reviewers. The same input and ruleset version always give the same output in the same order.

Product invariants to preserve:
- The default check never calls an LLM or external API. Optional "AI·현행 법령 보강" (assisted review via Korean Law MCP + Claude/GPT/Gemini) runs only when the server has keys configured and the user explicitly clicks 보강 실행.
- No scores, grades, or pass/fail verdicts. "확인되지 않음" means "not found in the posting", never "absent".
- Only statute articles are the direct basis for `law` findings. NCS guidance, FAQs and best practices may back questions and controls only.
- User-facing strings (CLI, MCP, web, errors) are Korean.

## Commands

Python ≥ 3.11. Node.js 22 is needed for the Python/JS parity tests. Run everything from the repo root; `tests/conftest.py` imports `tools.release_inputs`.

```powershell
python -m pip install -e ".[dev]"

python -m pytest                                    # full suite (addopts = -q)
python -m pytest tests/test_engine.py               # one file
python -m pytest tests/test_engine.py::test_name    # one test
python -m pytest -k "AGE"                           # by keyword

# Data and bundle gates (CI runs these before pytest)
python tools/validate_data.py
python tools/build_statutes.py
python tools/export_web_bundle.py --check           # drop --check to regenerate web/data.js
python tools/export_posting_templates.py --check    # drop --check to regenerate web/posting-templates.js

python tools/verify_web_parity.py                   # Python vs JS engine on a corpus (needs node and .corpus*)
python tools/verify_distribution.py                 # sdist/wheel audit, writes reports/distribution_audit.json
python tools/check_evidence_versions.py --scope local

# Running the app
fairpost check .\posting.txt [--review-packet --pretty]
fairpost web --open-browser                         # http://127.0.0.1:8000/web/
python -m mcp_server.local_runtime [web|mcp|stdio]
```

Test-suite gotchas:
- Tests marked `requires_node` skip when `node` is missing. With `FAIRPOST_REQUIRE_NODE=1` (set in CI) they fail instead.
- A few tests in `tests/test_statute_audit_workflow.py` skip on Windows or when `bash`/`jq` is missing.
- `conftest.py` fingerprints `api/ cli/ core/ data/ mcp_server/ tests/ tools/ web/` plus `pyproject.toml`, `.mcp.json`, `MANIFEST.in` and `vercel.json` at session start. The run fails if any of them change mid-run, so don't edit those files while pytest is running.
- Searching from the repo root hits tens of thousands of untracked files under `tmp/`, `.tmp/`, `.assay/`, `.vercel/`, `.claude/worktrees/` (old build and extract copies). Scope searches to the tracked directories.

## Architecture

**Data is the single source of truth.** `data/rules/law.yaml` holds `layer: law` rules (finding + statute basis + alternatives + `related_questions`). `data/rules/questions.yaml` holds `layer: question` cards, `data/slots.yaml` the absence slots, and `data/statutes/*.yaml` the official article snapshots with SHA-256. `data/guidance/` holds NCS and organization-type guidance, and `data/rules/rejected.yaml` records rejected candidates with reasons. `core/loader.py` validates all of it into a `Ruleset` with two hashes:
- `version`: rules + slots + statutes + `MORPH_VERSION`.
- `matching_version`: triggers + slots + `MORPH_VERSION` (`core/morph.py`) + `SECTION_VERSION` (`core/extractor.py`) + `MATCH_ENGINE_VERSION` (`core/loader.py`). Bump the relevant constant when matching behavior changes.

**Engine flow** (`core/engine.py` `FairpostEngine.check`):
1. `morph.normalize`: NFKC, whitespace, zero-width removal and honorific rewrites. Offsets and quotes in results still point at the original input.
2. `extractor.split_sections` + `extract_slots`.
3. Per rule: `presence` triggers match `patterns` (plain strings, or a `re:` prefix for regex). Candidates are filtered by `exclude` term windows, `section_scope`, `context_groups` and `source_candidate_allowed`. `absence` triggers fire when the slot is not found.
4. Law findings pull in their `related_questions`. A question whose own patterns matched but were rejected by its exclusions is *not* pulled back in by a finding link. Priority: 1 linked to a finding, 2 presence, 3 absence, 4 `review_scope: common`.

**Twin JS engine.** `web/engine.js` is a hand-maintained port of the Python normalize/section/slot/match logic used by the build-free static web app (`web/index.html` loads `data.js`, `posting-templates.js`, `engine.js`, `app.js`). `web/data.js` and `web/posting-templates.js` are **generated**: never hand-edit them, regenerate with the export tools. Any change to `core/morph.py`, `core/extractor.py` or matching in `core/engine.py` must be mirrored in `web/engine.js`. Parity tests run the JS through `tests/js_runner.cjs` under Node and compare it with Python.

**Interfaces.**
- `cli/main.py`: the `fairpost` entry point (`check`, `web`, `purge-answers`).
- `mcp_server/server.py` defines three FastMCP instances:
  - `mcp`: all 10 tools, loopback-only, stores answers and role-review packets in `~/.fairpost/`.
  - `public_mcp`: exactly `check_job_posting`, `check_job_posting_structured`, `next_review_question`. CI asserts this list.
  - `claude_mcp`: a single plain-text check.
- `mcp_server/remote.py` is the Starlette ASGI app for Vercel (`api/index.py` imports `app`). It serves `/api/mcp`, `/api/claude-mcp`, `/api/health` and `/api/assisted-review` behind `RemoteSecurityMiddleware` (Bearer auth, anonymous rate limit, fail-closed origin checks).
- `mcp_server/local_runtime.py` composes the static web app + full MCP + local assisted review on 127.0.0.1.
- `assisted_review.py` handles AI providers and identifier masking. `review.py` handles the Korean Law MCP and HR review packet. `storage.py` holds local answers and packets.
- Network deployments must never expose `save_answer`/`get_saved_answers` or accept arbitrary `org_id`, because a shared token cannot prove org ownership.

**Deployment.** `vercel.json` rewrites the `/api/*` paths to `api/index.py` and redirects `/book` → `/web/?entry=book`. That address is printed as a QR code in a book, so the redirect must stay non-permanent. `mcp_server/build_identity.py` mirrors the security-relevant parts of `vercel.json` and `.vercelignore` as hashed policy constants, and tests enforce that they match. Update both together. For the deploy and verify procedure, see `docs/vercel-deployment.md` (`tools/verify_vercel_deployment.py`).

## Release evidence (read before changing rules, slots, questions or engine code)

`reports/*.json` are bound to `ruleset_version`, `matching_version` and the `runtime_source_fingerprint` (from `build_identity.py`: engine, MCP, Vercel entry, web bundle, pyproject, deploy config). In CI, `tools/check_evidence_versions.py --scope local` fails on stale reports, so a rule or engine change usually requires regenerating evidence:
- Corpus-bound reports need the private, gitignored `.corpus*` directories. On a machine that has them, run `python tools/refresh_evidence.py --plan` then `python tools/refresh_evidence.py`. Reports that cannot be rebuilt can be marked `--mark-historical <report> --reason "..."`.
- Order matters. `distribution_audit.json` hashes `reports/*.json`, so rebuild it after every other report. `evidence_version_audit.json` comes last. Full procedure: `docs/evidence-versioning.md`.
- Changing which questions activate requires a matching entry in `reports/question_relevance_manual_review.json` (checked by `tests/test_question_relevance_audit.py`).
- New tracked files containing phone-number or identifier-like synthetic strings must be added to `SYNTHETIC_PRIVACY_EXAMPLES` in `tools/verify_distribution.py`, or the distribution audit fails.

## Rule-authoring conventions (from CONTRIBUTING.md)

- Expression detection favors precision. Absence detection favors recall. Fix false positives with generalized `exclude` terms using the smallest workable `window`, and add a regression test.
- Every rule carries `provenance` (method, corpus hits/split, `reviewed_at`, `reviewed_by`) and `book_ref`. Verify statute articles and effective dates against 국가법령정보센터. Turn ambiguous candidates into question cards or add them to `rejected.yaml` with a reason.
- Never read sealed holdout data while developing rules. Corpus and audit tools guard against `holdout` paths (calibration uses train data only). See `docs/evaluation-protocol.md`.
- Institution-specific rules go in a local copy of `data/local_rules.example.yaml` (gitignored `data/local_rules.yaml`). The loader accepts only `basis.type: consensus` there.
- Never commit posting originals, personal data, or per-organization rankings. Only collection code and anonymized aggregates are public.
