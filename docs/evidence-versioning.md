# 릴리스 증거 버전 관리

FairPost는 규칙셋만 맞다고 해서 릴리스 증거가 자동으로 최신이 되지 않는다.
코드, 테스트, 코퍼스 감사, 배포 감사, 외부 MCP 클라이언트 감사, 패키지 산출물이
서로 다른 시점의 상태를 가리킬 수 있으므로 현재성 점검이 필요하다.

## 로컬 증거 게이트

```powershell
python tools\check_evidence_versions.py --scope local
```

이 명령은 `reports/*.json` 중 로컬 후보 증거를 현재 `ruleset_version`,
`matching_version`, 스키마 기준으로 점검한다. 2026-10-03부터는 보고서가 기록한
`runtime_source_fingerprint`와 검증 입력 지문도 현재 값과 비교하므로, 규칙이 그대로여도
코드가 바뀌면 그 보고서는 stale로 판정된다. 하나라도 stale이면 종료 코드 `1`을
반환한다. 파일명에 날짜ㆍ용도 접미사가 붙은 스냅샷 사본(예:
`build_artifact-8h-candidate-2026-09-12.json`)은 stale이어도 표시만 하고 실패시키지
않는다.

릴리스 판단에 직접 쓰는 보고서는 이름별 스키마를 강제한다.

| 보고서 | 현재 스키마 |
|---|---|
| `distribution_audit.json` | `fairpost-distribution-audit-v2` |
| `web_engine_parity.json` | `fairpost-web-engine-parity-v1` |
| `work24_access_audit.json` | `fairpost-work24-access-audit-v1` |
| `prd_corpus_summary.json` | `fairpost-prd-corpus-summary-v1` |
| `human_labeling_handoff.json` | `fairpost-human-labeling-handoff-v1` |
| `mcp_client_audit.json` | `fairpost-mcp-client-audit-v2` |
| `vercel_deployment_audit.json` | `fairpost-vercel-deployment-audit-v3` |
| `corpus_diversity_audit.json` | `private-corpus-diversity-audit-v1` |
| `evaluation.json` | `3` |
| `build_artifact.json` | `fairpost-build-artifact-v2` |

과거 연구용 보고서를 현재 릴리스 증거로 쓰지 않으려면 해당 JSON에
`"evidence_status": "historical"`를 기록한다.

## 운영 증거 게이트

```powershell
python tools\check_evidence_versions.py --scope all
```

`all` 범위는 `build_artifact.json`과 `vercel_deployment_audit.json`까지 함께
점검한다. 운영 감사가 stale이면 먼저 `tools/verify_vercel_deployment.py`로 현재
배포를 다시 검증해야 한다.

`runtime_source_fingerprint`는 Python 엔진ㆍMCPㆍVercel 진입점뿐 아니라 루트
랜딩, 정적 웹 번들, `pyproject.toml`과 배포 설정ㆍ제외 정책을 함께 묶는다.
Vercel은 `vercel.json`을 빌드 과정에서 내부 형식으로 정규화하고 `.vercelignore`는
배포하지 않는다. 따라서 두 파일의 보안 의미를 해시되는 `build_identity.py` 정책
상수로 고정하고 실제 파일 내용과 일치하는지 회귀 테스트로 강제한다. 텍스트 파일의
LFㆍCRLF 차이는 정규화하므로 같은 Git 내용은 Windows와 Vercel Linux에서 같은
지문을 만들고, 사용자에게 보이는 문구나 웹 동작이 달라지면 지문도 달라진다.
운영 health에는 원문 없이 파일별 정규화 SHA-256 manifest도 제공해 패키징 경계가
달라질 때 어느 공개 소스 파일이 누락ㆍ변경됐는지 추적할 수 있다.

## 후보 릴리스 재생성 순서

2026-10-03 기준으로 후보 릴리스 증거를 다시 묶을 때 권장 순서는 다음과 같다.

1. `python -m build --outdir dist`
2. `python tools\verify_distribution.py` — `reports/distribution_audit.json`을 현재
   런타임 지문으로 갱신한다.
3. `python -m pytest --junitxml <임시 경로>\pytest.xml`
4. `python tools\check_evidence_versions.py --scope local --output <임시 경로>\evidence.json`
5. `python tools\build_release_report.py --junitxml <임시 경로>\pytest.xml --evidence-version-audit <임시 경로>\evidence.json --candidate-report --allow-stale-deployment --output <출력 경로>`
6. 운영 재배포 후 `tools/verify_vercel_deployment.py`로 배포 감사를 갱신하고
   `python tools\check_evidence_versions.py --scope all`

`reports/evidence_version_audit.json`은 `distribution_source_fingerprint`에 포함된다.
따라서 2번 뒤에 그 파일을 다시 쓰면 `distribution_audit.json`이 즉시 stale가 된다.
새 점검기는 배포본 감사의 런타임 지문도 보므로 패키지 감사를 먼저 갱신하고, 로컬
증거 점검 결과는 임시 경로에 쓴다. 외부 MCP 클라이언트 감사처럼 이전 런타임에서
수행한 감사는 다시 수행하기 전까지 `"evidence_status": "historical"`로 둔다.

## 규칙ㆍ코드 변경 뒤 한 번에 재생성 (2026-10-10)

질문ㆍ슬롯ㆍ엔진을 고치면 규칙셋이나 런타임 지문이 바뀌어 위 게이트가 바로 실패한다.
그중 일부는 비공개 코퍼스(`.corpus*`, Git 제외)가 있어야 다시 만들 수 있어 CI가 고칠 수
없다. 코퍼스가 있는 PC에서 다음 명령 하나로 정해진 순서대로 갱신한다.

```powershell
python tools\refresh_evidence.py --plan   # 무엇을 다시 만들지 먼저 확인
python tools\refresh_evidence.py
```

1. 생성 도구가 등록된 코퍼스 기반 보고서를 다시 만든다. stale이거나 `historical`로
   표시된 것이 대상이며, `--all`이면 모두 다시 만든다.

   | 보고서 | 도구 | 기본 입력 |
   |---|---|---|
   | `web_engine_parity.json` | `verify_web_parity.py` (Node.js 필요) | `.corpus-final/train/records.jsonl` |
   | `engine_performance.json` | `benchmark_engine.py` (기존 설정 재사용) | `.corpus-prd/train/records.jsonl` |
   | `question_relevance_audit.json` | `audit_question_relevance.py` | `.corpus-prd/train/records.jsonl` |
   | `corpus_rule_coverage.json` | `analyze_corpus.py` | `.corpus/train/records.jsonl` |

   새 보고서는 임시 파일에 먼저 쓰고, 입력 건수와 입력 SHA-256이 기존 증거와 같을 때만
   `reports/`에 반영한다. 코퍼스가 다르면 기존 보고서를 그대로 두고 알려 준다. 입력 경로는
   `--input 보고서이름=경로`로 바꾸고, 코퍼스를 의도적으로 바꿨다면
   `--accept-input-change`를 붙인다.
2. 모든 보고서를 쓴 뒤 sdistㆍwheel을 새로 빌드해 `distribution_audit.json`을 갱신한다.
3. 마지막으로 게이트가 통과하면 `evidence_version_audit.json`을 쓴다. 통과하지 못하면
   남은 stale 보고서를 보여 주고 종료 코드 `1`을 반환한다.

사람ㆍ배포ㆍ브라우저가 필요한 보고서(`human_labeling_handoff.json`,
`production_rollout.json`, `web_visual_audit.json`, `youth_job_rule_coverage.json`)는
이 도구가 다시 쓰지 않고 할 일만 안내한다. 다시 만들기 전까지 현재 증거로 쓰지 않으려면
이유와 함께 표시한다.

```powershell
python tools\refresh_evidence.py --mark-historical production_rollout.json --reason "재배포 전 기록"
```

`historical` 표시는 게이트를 통과시키지만 릴리스를 통과시키지는 않는다. 웹ㆍPython
비교 증거가 `historical`이거나 이전 규칙셋 기준이면 `build_release_report.py`가
`current_web_parity` 차단 사유를 추가하고, 사람 평가는 현재 규칙셋의 최종 평가가 있어야
한다.

2026-10-10 기록: 6ff67c6이 규칙셋을 `…-0fa75d41155c`로 바꾼 뒤 위 7개 보고서를 이 방식으로
`historical`로 표시했다. 코퍼스가 있는 PC에서 `python tools\refresh_evidence.py`를 실행하면
앞의 세 보고서가 현재 증거로 돌아온다.

## strict 릴리스

후보 CI에서는 `--candidate-report`와 필요 시
`--allow-stale-deployment`를 사용할 수 있다. 반대로 strict 릴리스는
`build_release_report.py` 기본 동작을 유지하고, 사람 홀드아웃 평가, 코퍼스
다양성, Work24 접근 증거, 릴리스 태그까지 모두 충족한 뒤에만 주장한다.
