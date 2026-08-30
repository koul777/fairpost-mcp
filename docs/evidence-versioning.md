# 릴리스 증거 버전 관리

FairPost는 규칙셋만 맞다고 해서 릴리스 증거가 자동으로 최신이 되지 않는다.
코드, 테스트, 코퍼스 감사, 배포 감사, 외부 MCP 클라이언트 감사, 패키지 산출물이
서로 다른 시점의 상태를 가리킬 수 있으므로 현재성 점검이 필요하다.

## 로컬 증거 게이트

```powershell
python tools\check_evidence_versions.py --scope local
```

이 명령은 `reports/*.json` 중 로컬 후보 증거를 현재 `ruleset_version`,
`matching_version`, 스키마 기준으로 점검한다. 하나라도 stale이면 종료 코드 `1`을
반환한다.

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
랜딩, 정적 웹 번들, `vercel.json`, `pyproject.toml`을 함께 묶는다. 텍스트 파일의
LFㆍCRLF 차이는 정규화하므로 같은 Git 내용은 Windows와 Vercel Linux에서 같은
지문을 만들고, 사용자에게 보이는 문구나 웹 동작이 달라지면 지문도 달라진다.

## 후보 릴리스 재생성 순서

2026-08-31 기준으로 후보 릴리스 증거를 다시 묶을 때 권장 순서는 다음과 같다.

1. `python tools\check_evidence_versions.py --scope local`
2. `python -m build --outdir dist`
3. `python tools\verify_distribution.py`
4. `python tools\build_release_report.py --junitxml reports\pytest-full.xml --candidate-report`
5. `python tools\check_evidence_versions.py --scope all`

핵심 이유는 `reports/evidence_version_audit.json`이
`distribution_source_fingerprint`에 포함되기 때문이다. 따라서
`verify_distribution.py` 뒤에 local evidence audit를 다시 쓰면
`distribution_audit.json`이 즉시 stale가 된다. local evidence audit를 먼저
고정한 뒤 패키지를 다시 빌드해야 한다.

## strict 릴리스

후보 CI에서는 `--candidate-report`와 필요 시
`--allow-stale-deployment`를 사용할 수 있다. 반대로 strict 릴리스는
`build_release_report.py` 기본 동작을 유지하고, 사람 홀드아웃 평가, 코퍼스
다양성, Work24 접근 증거, 릴리스 태그까지 모두 충족한 뒤에만 주장한다.
