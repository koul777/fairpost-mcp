# PRD v0.3 추적표

기준 문서: `fairpost-PRD (2).md`, 2026-07-26.

상태 정의:

- **충족**: 현재 파일 또는 자동 검증 증거가 요구사항을 직접 입증한다.
- **부분 충족**: 기능은 있으나 요구된 데이터ㆍ운영 증거가 부족하다.
- **미충족**: 필요한 외부 입력 또는 구현이 아직 없다.

## 목표

| 목표 | 상태 | 현재 증거 |
|---|---|---|
| G1 법령 표현 정밀도 ≥ 0.90 | 미충족 | 평가기는 있으나 90개 공공 및 90개 민간 홀드아웃의 사람 라벨과 측정 보고서가 없음 |
| G2 부재 탐지 재현율 ≥ 0.85 | 미충족 | 11개 슬롯과 평가기는 있으나 사람 라벨과 측정 보고서가 없음 |
| G3 질문 카드 15개 이상 | 충족 | `data/rules/questions.yaml` 42개, `tools/validate_data.py`, 공고별ㆍ공통 범위와 관련성 감사 |
| G4 결정론 | 충족 | `test_deterministic_for_100_runs` |
| G5 설치ㆍ키ㆍ비용 없는 웹 | 충족 | `web/index.html`, 네트워크 차단 및 CSP 테스트 |

## 아키텍처와 런타임

| 요구사항 | 상태 | 현재 증거 |
|---|---|---|
| core 인터페이스 독립 및 tools 역참조 금지 | 충족 | `core/`, `test_core_does_not_import_tools` |
| 규칙 기반ㆍ런타임 LLM/외부 API 없음 | 충족 | 네트워크 차단 코어 테스트, 정적 웹 CSP |
| 11개 슬롯 | 충족 | `data/slots.yaml`, 안정 순서 테스트 |
| 3블록 CheckResult와 고정 면책문 | 충족 | `core/schema.py`, `core/engine.py` |
| 공백ㆍ특수문자ㆍ어미 정규화와 원문 offsetㆍsection | 충족 | NFKCㆍ비표준 공백ㆍ제로폭ㆍ한국어 어미 및 CRLFㆍUnicode 패리티 테스트 |
| 로컬 규칙 확장 | 충족 | `FAIRPOST_LOCAL_RULES_PATH`, statute 근거 거부 테스트 |
| MCP 도구 3종 | 충족 | `check_job_posting`, `save_answer`, `get_saved_answers` 프로토콜 테스트 |
| 기본 MCP Streamable HTTP | 충족 | `fairpost-mcp`, `test_streamable_http_is_default_and_calls_all_tools` |
| Claude 계열 클라이언트 HTTP 설정 | 부분 충족 | `.mcp.json`에 Vercel 운영ㆍ로컬 HTTP를 등록하고 SDKㆍInspector 원격 호출 성공. Claude Code 프로젝트 승인은 대기 중 |
| 사용자 로컬 답변 저장 | 충족 | `LocalAnswerStore`, 격리 경로 및 HTTP 왕복 테스트. Vercel 저장은 Upstash 미연결로 비활성 |
| 정적 웹ㆍMCP 결과 동등성 | 충족 | Python/JavaScript 패리티 테스트 |

PRD의 엄격한 개인정보 경계를 충족하는 HTTP 주소는
`http://127.0.0.1:8000/mcp`다. Vercel 운영 주소
`https://fairpost-mcp.vercel.app/api/mcp`는 사용자가 별도로 요청한
확장 모드다. 원문을 영속 저장하지 않지만 AI 제공자와 Vercel로 전송되므로
PRD 4.5의 완전한 기기 내 처리로 간주하지 않고 화면ㆍ운영 문서에 고지한다.

## 사전과 법령

| 요구사항 | 상태 | 현재 증거 |
|---|---|---|
| 법령 규칙 15개 이상 | 충족 | `data/rules/law.yaml` 19개 |
| 질문 카드 15개 이상 | 충족 | `data/rules/questions.yaml` 42개 |
| rejected/deferred 기록 | 충족 | `data/rules/rejected.yaml`, 데이터 검증기 |
| 6개 대상 법령 스냅샷 | 충족 | `data/statutes/*.yaml` |
| 원문ㆍ시행일ㆍ해시 검증 | 충족 | 공식 법령 API 대조 보고서, 스냅샷 테스트 |
| Korean Law MCP 수집 경로 | 부분 충족 | 연결된 규정 MCP는 기관 내부규정 범위여서 국가법령을 반환하지 않음. 법제처 국가법령정보 Open API 직접 조회로 대체했으며 `retrieved_via: national-law-open-api`로 차이를 보존 |
| 월별 개정 확인 CI | 충족 | 최소 기준보다 강화한 매일 대조, 영향 규칙 ID 보고, 사람 검토 후 병합 |
| 능력중심 채용 가이드 추적성 | 충족 | research 근거 메타데이터와 `ability-based-hiring-guide-map.md` |

## 코퍼스와 평가

| 요구사항 | 상태 | 현재 증거 |
|---|---|---|
| 공공 300건 | 충족 | 잡알리오ㆍ클린아이ㆍ나라일터 각 100건 집계 |
| 민간 300건 | 충족 | 진천군 공개 민간 구인정보 3,000건 중 고정 분할 안에서 300건을 결정론적으로 선택 |
| 부문×직군×고용형태 층화 | 충족 | PRD 정식 600건은 현장 239ㆍ사무 306ㆍ기술 38ㆍ연구 17건. 확장 3,300건은 현장 2,173ㆍ사무 915ㆍ기술 167ㆍ연구 45건 |
| 70/30 고정 분할 | 충족 | PRD 정식 학습 420건/홀드아웃 180건, 확장 학습 2,310건/홀드아웃 990건, 해시 중복 0 |
| 수집 중 비식별화 | 충족 | 이메일ㆍ전화ㆍ담당자ㆍ조직명 비식별화 테스트 |
| 원문 미배포 | 충족 | `.corpus*/` Git 제외, 익명 집계만 `reports/`에 존재 |
| 후보 추출ㆍ정규화 도구 | 충족 | `mine_candidates.py`, `normalize_candidates.py` |
| 질문 관련성 감사 | 충족 | train-only 420건 익명 집계, 공통 체크리스트와 누락 슬롯 중복 분리, 명백한 문맥 오발동 104개 감소. `docs/question-relevance-audit.md` |
| 봉인 홀드아웃 평가 | 부분 충족 | 오염 차단, 정식 180건 로컬 라벨링 화면과 완전 라벨 강제는 구현. `reports/human_labeling_handoff.json` 생성, 사람 정답 데이터 없음 |

## 수용 기준

AC-1~AC-18의 자동화 증거는 `docs/acceptance.md`에 연결되어 있다. 현재
162개 테스트가 통과한다. 다만 수용 기준 통과가 G1ㆍG2 성능 목표를 대신
증명하지는 않는다.

## 완료를 위해 남은 증거

1. 봉인한 최종 홀드아웃의 사람 라벨
2. `tools/evaluate.py --enforce-targets` 통과 보고서
3. PRD에 지정된 Work24 민간 공고 출처를 충족할 기업회원 API 권한 또는 출처 변경 승인
4. Git 저장소ㆍ법령 감사 Actions 성공 실행ㆍ릴리스 태그
5. Claude Code 프로젝트 MCP 승인과 실제 Claude 호출
6. Vercel 답변 저장을 운영할 경우 Upstash Redis
