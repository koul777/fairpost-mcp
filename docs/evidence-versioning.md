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

## strict 릴리스

후보 CI에서는 `--candidate-report`와 필요 시
`--allow-stale-deployment`를 사용할 수 있다. 반대로 strict 릴리스는
`build_release_report.py` 기본 동작을 유지하고, 사람 홀드아웃 평가, 코퍼스
다양성, Work24 접근 증거, 릴리스 태그까지 모두 충족한 뒤에만 주장한다.
