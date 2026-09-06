<div align="center">

# FairPost MCP

[![FairPost MCP 홍보·시연 영상](docs/assets/fairpost-promo.gif)](docs/assets/fairpost-promo.mp4)

<sub>미리보기를 누르면 24초·1920×1080 MP4 시연 영상을 볼 수 있습니다.</sub>

**채용공고의 표현, 빠진 정보, 확인할 질문을 근거와 함께 정리하는 결정론적 리뷰 도구**

[▶ 24초 데모 보기](docs/assets/fairpost-promo.mp4) · [웹에서 체험하기](https://fairmcp.vercel.app/web/) · [로컬 실행](#지금-사용해-보기) · [MCP 연결](#mcp-연결)

</div>

FairPost는 채용공고문을 읽고 다음 세 가지를 한 번에 정리합니다.

- 관련 법령과 함께 다시 살펴볼 표현
- 공고문에서 확인되지 않은 절차·정보
- 채용담당자가 후속 검토할 질문

결과는 같은 입력에 항상 같은 순서로 나옵니다. 런타임에 LLM이나 외부 API를
호출하지 않으며 점수, 등급, 합격·통과 판정도 만들지 않습니다.

> FairPost의 결과는 검토 참고자료이며 공정성 여부에 대한 판정이나 법률
> 자문이 아닙니다. “확인되지 않음”은 해당 절차가 없다는 뜻이 아니라
> 공고문에서 발견되지 않았다는 뜻입니다.

## 24초 시연 영상

[전체 MP4 보기](docs/assets/fairpost-promo.mp4) ·
[포스터 보기](docs/assets/fairpost-promo-poster.jpg) ·
[영상 콘티와 제작 사양](docs/demo/storyboard.md)

| 시간 | 장면 | 보여 주는 내용 |
|---:|---|---|
| 0:00–0:03 | 브랜드 인트로 | “채용공고 검토, 근거부터 질문까지”라는 핵심 가치를 소개합니다. |
| 0:03–0:10 | 1. 로컬 우선 | 내장 샘플 공고를 불러와 브라우저 안에서 즉시 점검하는 과정을 보여 줍니다. |
| 0:10–0:21 | 2. 근거 기반 검토 | 매칭 표현과 법령 근거를 열어 보고, 누락 정보와 후속 질문까지 한 흐름으로 확인합니다. |
| 0:21–0:24 | 엔드 카드 | “판정이 아닌 수정과 확인을 위한 메모”라는 원칙과 웹 주소를 안내합니다. |

이 영상은 합성 목업이 아닌 **실제 배포 웹 UI를 조작한 시연**입니다.
웹 앱에 포함된 샘플 공고만 사용했으며 실제 지원자·기업의 개인정보는
포함하지 않습니다. 영상은 정적 웹의 로컬 처리와 결과 탐색을 보여 주며,
MCP 클라이언트 연결 화면 자체는 포함하지 않습니다.

## 지금 사용해 보기

| 원하는 방식 | 실행·접속 | 처리 위치와 용도 |
|---|---|---|
| 정적 웹 | [배포된 웹 앱](https://fairmcp.vercel.app/web/) 또는 `web/index.html` | 입력과 점검을 브라우저 안에서만 처리합니다. |
| 로컬 CLI | `fairpost check .\posting.txt` | 서버 없이 기기 안에서 JSON 결과를 만듭니다. |
| 로컬 CLI 검토 패킷 | `fairpost check .\posting.txt --review-packet` | NCS 통제와 선택적 현행 법령 조회 상태를 함께 만듭니다. |
| 로컬 MCP | `fairpost-mcp` → `http://127.0.0.1:8000/mcp` | AI 클라이언트에서 점검·HR 검토 패킷·질문·로컬 답변 저장 도구를 사용합니다. |
| 원격 MCP | `fairpost-remote` → `https://fairmcp.vercel.app/api/mcp` | Vercel에서 읽기 전용 점검을 수행하며 답변을 저장하지 않습니다. |

가장 빠른 방법은 [배포된 웹 앱](https://fairmcp.vercel.app/web/)을 여는 것입니다.
샘플 공고를 불러오거나 직접 문장을 붙여 넣은 뒤 **검토 메모 만들기**를 누르면 됩니다.
질문마다 담당자 답변을 적으면 진행률과 답변이 복사 메모에 함께 들어갑니다.
정적 웹에서는 입력ㆍ분석ㆍ답변이 브라우저 안에서만 처리됩니다. 답변은 현재 분석
메모리에만 남으며 재분석하거나 입력을 지우면 삭제됩니다.

Python 3.11 이상에서는 CLI를 사용할 수 있습니다.

```powershell
python -m pip install -e ".[dev]"
fairpost check .\posting.txt
fairpost check .\posting.txt --review-packet --pretty
```

표준 입력도 지원합니다.

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Get-Content .\posting.txt -Raw | fairpost check -
```

서버 없이 사용하려면 [web/index.html](web/index.html)을 브라우저에서 직접
열어도 됩니다.

## 핵심 특징

| 특징 | 동작 |
|---|---|
| 결정론적 엔진 | 동일한 입력과 규칙 버전은 동일한 결과와 순서를 반환합니다. |
| 근거 추적 | 규칙 ID, 원문 인용, offset, 관련 법령, 수정 대안을 함께 제공합니다. |
| 로컬 우선 | 정적 웹과 CLI는 공고문을 외부 서비스로 보내지 않습니다. |
| 사람 중심 | 판정 대신 수정할 표현과 질문을 제시하고, 담당자 답변을 근거와 함께 메모로 묶습니다. |
| 다중 인터페이스 | 정적 웹, CLI, 로컬 MCP, 읽기 전용 원격 MCP를 제공합니다. |

현재 기본 데이터에는 법령 표현 규칙 19개, 검토 질문 52개, 절차·정보 슬롯
11개, 법령 스냅샷 6종이 들어 있습니다. 정규화 이후에도 결과 offset과 인용문은
입력 원문을 그대로 가리킵니다.

## MCP 연결

### 로컬 MCP

로컬 서버는 기본적으로 외부에서 접근할 수 없는
`http://127.0.0.1:8000/mcp`에서 실행됩니다.

```powershell
fairpost-mcp
```

Claude Code 프로젝트에 등록하려면 다음 명령을 사용합니다. 저장소의
[.mcp.json](.mcp.json)에도 같은 설정이 들어 있습니다.

```powershell
claude mcp add --transport http --scope project fairpost http://127.0.0.1:8000/mcp
```

일반적인 Streamable HTTP MCP 클라이언트 설정은 다음과 같습니다.

```json
{
  "mcpServers": {
    "fairpost": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

로컬 MCP는 아래 여섯 도구를 제공합니다.

- `check_job_posting`: 전체 표현·누락·질문 검토
- `check_job_posting_structured`: finding→질문→근거ㆍ원문 위치를 버전 있는 기계 판독 구조로 반환
- `prepare_hr_review`: 점검 결과, 활성 NCS 통제와 선택적 Korean Law MCP 현행 조문 조회를 한 패킷으로 반환
- `next_review_question`: 아직 답하지 않은 질문 한 개와 진행률 반환
- `save_answer`: 유효한 질문 ID에 대한 조직별 답변 저장(같은 조직ㆍ질문의 기존 답변은 교체)
- `get_saved_answers`: 저장된 답변 조회

답변은 사용자 컴퓨터의 `~/.fairpost/answers.json`에 평문으로 저장되며 자동
만료되지 않습니다. 공용 PC나 여러 사람이 같은 OS 계정을 쓰는 환경에는 저장하지
마세요. 전체 답변은 `fairpost purge-answers`, 특정 조직 답변은
`fairpost purge-answers --org-id <조직 ID>`로 삭제할 수 있습니다. 마지막 답변을
지우면 저장 파일도 제거됩니다. 이 삭제 명령은 조직 ID나 답변 내용을 출력하지 않습니다.

### Vercel 원격 MCP

운영 엔드포인트는 `https://fairmcp.vercel.app/api/mcp`입니다. 프로젝트 설정에서는
로컬 `fairpost`와 구분되는 `fairpost-remote`라는 명시 선택 이름을 사용합니다. 기본 설정에서는
Bearer 인증이 필요하며, 원격 환경에는 `org_id`와 로컬 답변 저장 기능을 받지
않는 미리보기ㆍ분석용 읽기 도구만 노출합니다. 지속적인 답변 저장과 팀 검토는
루프백 로컬 MCP에서만 수행합니다. Claude Desktop용 제한 엔드포인트와 인증,
배포 절차는 [Vercel 배포 가이드](docs/vercel-deployment.md)를 참고하세요.

원격 MCP로 보낸 공고문은 Vercel 서버에서 처리됩니다. 클라우드 AI 클라이언트에
연결하면 해당 AI 제공자도 입력을 처리할 수 있습니다. 완전한 기기 내 처리가
필요하다면 정적 웹, CLI 또는 루프백 로컬 MCP를 사용하세요.

### Korean Law MCP와 함께 사용하기

FairPost는 재현 가능한 오프라인 점검을 위해 검증된 법령 스냅샷을 기본으로
사용합니다. 로컬 `prepare_hr_review` 도구와 CLI의 `--review-packet`은 Korean
Law MCP가 설정된 경우 finding에 연결된 `법령명·조문번호`만 그때그때 조회합니다.
공고문 원문, 매칭 문구와 `org_id`는 법령 MCP로 보내지 않습니다.

HTTP MCP를 사용할 때는 다음 환경변수를 지정합니다. 외부 주소는 HTTPS만
허용하고 로컬 `http://127.0.0.1`ㆍ`localhost`는 허용합니다.

```powershell
$env:FAIRPOST_KOREAN_LAW_MCP_URL = "https://<Korean-Law-MCP>/mcp"
$env:FAIRPOST_KOREAN_LAW_MCP_TOKEN = "<token>"  # 필요한 경우에만
fairpost-mcp
```

stdio 서버라면 실행 파일과 인자를 분리해서 지정합니다.

```powershell
$env:FAIRPOST_KOREAN_LAW_MCP_COMMAND = "<law-mcp-command>"
$env:FAIRPOST_KOREAN_LAW_MCP_ARGS = '["--stdio"]'
$env:FAIRPOST_KOREAN_LAW_MCP_ENV_ALLOWLIST = "LAW_OPEN_API_OC"  # 필요한 값만
fairpost-mcp
```

stdio 자식 프로세스에는 시스템 실행에 필요한 최소 환경변수와 위 allowlist에
명시한 값만 전달합니다. FairPost 원격 토큰과 답변 저장 설정은 기본적으로
전달하지 않습니다.

기본 도구 이름은 `search_law`와 `get_law_text`입니다. 다른 이름을 쓰는 서버는
`FAIRPOST_KOREAN_LAW_SEARCH_TOOL`, `FAIRPOST_KOREAN_LAW_TEXT_TOOL`로 바꿀 수
있습니다. 연결하지 않았거나 조회가 실패하면 스냅샷을 현행으로 가장하지 않고
`not_configured` 또는 `upstream_unavailable` 상태와 재조회 요청을 반환합니다.
조회 성공도 위법 판단이 아니라 현행 조문 원문 확보를 뜻합니다.

### 선택적 AI·현행 법령 보강 버튼

웹의 **AI·현행 법령 보강** 스위치는 기본적으로 꺼져 있습니다. 꺼진 상태에서는
기존 정적 규칙 엔진만 브라우저에서 실행되고 네트워크 요청을 만들지 않습니다.
스위치를 켜면 먼저 같은 배포의 `/api/assisted-review`에서 설정 상태를 확인하며,
AI API와 Korean Law MCP가 모두 준비된 경우에만 다음 검사부터 보강 검토를
실행합니다.

AI API는 OpenAI 호환 Chat Completions 요청 형식을 지원하는 HTTPS 엔드포인트로
설정합니다. 로컬 루프백 API만 HTTP와 무인증을 허용합니다.

```powershell
$env:FAIRPOST_AI_API_URL = "https://<AI-provider>/v1/chat/completions"
$env:FAIRPOST_AI_API_KEY = "<server-side-key>"
$env:FAIRPOST_AI_MODEL = "<model-name>"

# 위 Korean Law MCP HTTP 또는 stdio 설정도 함께 필요합니다.
```

API 키는 브라우저 코드나 응답에 포함하지 않습니다. 활성화된 요청의 공고문은
FairPost 서버에서 재검사하지만, Korean Law MCP에는 `법령명·조문번호`만 보내고
AI API에는 `탐지 문구·조회된 현행 조문·활성 NCS 통제`만 보냅니다. 원격 호출은
기본 5회/분으로 제한하며 `FAIRPOST_ASSISTED_REVIEW_REQUESTS_PER_MINUTE`로 조정할
수 있습니다. 법령 조회에 실패하면 AI가 법을 추측하지 않도록 AI API 호출도
중단합니다.

웹에서는 검사 전에 `공공기관/민간기업`과 `상시근로자 1~29명/30~299명/300명
이상`을 선택할 수 있습니다. 공공기관은 다시 `공기업/준정부기관/기타공공기관/
지방공기업·지방출자출연기관/국가·지방자치단체/그 밖의 공공부문`으로 구분합니다.
모든 질문 카드에 적용 구분, 조직·규모별 운영 맥락과 공식 근거 링크를 표시합니다.

공기업과 준정부기관에는 `공기업·준정부기관의 경영에 관한 지침`과
`공공기관의 혁신에 관한 지침`을 함께 검토하도록 표시합니다. 기타공공기관은
혁신 지침과 기관 특성을 중심으로 보고 경영 지침의 직접 적용을 자동 단정하지
않습니다. 지방공공기관과 국가·지방자치단체는 각각의 별도 법·인사 지침을 먼저
확인하게 합니다. 민간기업에는 공공기관 지침을 의무처럼 제시하지 않고 일반
고용·개인정보 법령, 취업규칙, 단체협약과 내부 인사규정을 우선합니다.

규모별로 책임자 1인, HR·현업 이중 검토, 다부서 위원회·정기 감사 순으로 통제
깊이를 조정합니다. 이 규모 구간은 운영 설계를 위한 것이며 법적 중소기업 분류나
법률 적용 여부를 자동 결정하지 않습니다. 적용 범위 카탈로그는
[`data/guidance/organization-applicability.yaml`](data/guidance/organization-applicability.yaml)에
기준일과 출처를 함께 관리합니다.

## 동작 구조

```text
채용공고 원문
   │
   ├─ 정규화 ── 원문 offset 매핑 유지
   │
   ├─ 법령 표현 규칙 19개 ── finding + 근거 + 수정 대안
   ├─ 절차·정보 슬롯 11개 ── 확인되지 않은 항목
   └─ 검토 질문 52개 ─────── 우선순위 + 발동 문맥
                                  │
                                  └─ Web / CLI / MCP
```

질문은 분배·절차·대인·정보의 네 공정성 차원으로 구성됩니다. 공고에서 직접
발동한 질문, finding과 연결된 질문, 누락 슬롯 질문, 공통 체크리스트를 구분해
담당자가 확인 이유를 추적할 수 있게 합니다.

## 검증 상태

현재 버전은 `0.3.0` 릴리스 후보입니다.

| 항목 | 현재 증거 |
|---|---:|
| 자동화 테스트 | 915 passed |
| 전체 데이터 규칙 | 71 |
| 질문 카드 | 52 |
| 배포 형태 | 정적 웹 + CLI + 로컬 MCP + Vercel 읽기 전용 MCP + 선택형 AI·현행 법령 보강 API |

```powershell
python -m pytest
python tools\validate_data.py
python tools\export_web_bundle.py --check
python tools\verify_web_parity.py
python tools\verify_distribution.py
```

사람이 확정한 홀드아웃 평가, 실제 채용담당자 파일럿, 허가된 독립 민간 출처,
공개 원격 MCP의 전역 남용 방어는 아직 v1.0 차단 조건으로 추적합니다. 자동화
테스트 통과를 법률 정확도나 현장 유용성 증명으로 해석하지 않습니다.

- [요구사항 추적표](docs/prd-traceability.md)
- [완료 감사와 남은 외부 조건](docs/completion-audit.md)
- [v1.0 로드맵](docs/roadmap.md)
- [평가 무결성 프로토콜](docs/evaluation-protocol.md)
- [질문 관련성 감사](docs/question-relevance-audit.md)
- [법령 유지관리 절차](docs/statute-maintenance.md)

## 데이터와 연구 워크플로

고용24, 잡알리오, 클린아이 잡플러스, 나라일터 등 공개·승인된 출처의 공고는
런타임 조회가 아니라 규칙 개발과 오프라인 평가용 코퍼스 구축에만 사용합니다.
원문 코퍼스와 비공개 검토 큐는 Git에서 제외되며 수집 과정에서 담당자 이름,
연락처, 기관·기업명을 비식별화합니다.

### 법령·공정채용 근거 현황

- **법령:** 국가법령정보센터 Open API로 수집한 6개 법령의 조문,
  시행일, 스냅샷 날짜, SHA-256 해시를 `data/statutes/`에 저장합니다.
  [`tools/build_statutes.py`](tools/build_statutes.py)가 공식 원문과 재검증합니다.
  2026-09-06 Korean Law MCP로 6개 법령ㆍ14개 연결 조문의 조회 가능성을
  [별도 감사](reports/korean_law_mcp_audit.json)했으며, 예정 개정은 원문 해시
  비교 전까지 자동 반영하지 않습니다.
- **NCS 공정채용:** [NCS 공정채용 누리집](https://www.ncs.go.kr/blind/index.do)의
  공정채용 소개·프로세스·평가샘플, 2023·2024·2025 가이드, 2025 모니터링
  주요 사례를 출처·페이지·확인일과 함께 전처리했습니다. 설치 패키지에도
  [NCS 통제 카탈로그](data/guidance/ncs-fair-hiring.yaml)를 포함하고
  `prepare_hr_review`가 실제로 활성화된 질문과 연결해 반환합니다.
- **엄격한 분리:** 법령 조문만 `law` finding의 직접 근거로 사용합니다.
  가이드·FAQ·우수사례는 법 위반 판정이 아닌 인사담당자의 `question` 근거로만
  사용합니다.
- **현재 경계:** 기존 연구 번들의 9개 주요 출처와 2026-09-06 재확인한 공식
  누리집ㆍ가이드ㆍ모니터링 출처를 연결했습니다. 당시 2026년 공지는 확인됐지만
  2026년판 가이드북ㆍ모니터링 사례집은 확인되지 않았습니다. 신규 게시물·첨부파일을 자동으로 주기 동기화하는
  수집 파이프라인은 아직 없으며, 추가 사례는 수동 검증 후 반영합니다.

이전 전처리·규칙 연결 기록은
[능력중심 채용 가이드 적용 맵](docs/ability-based-hiring-guide-map.md)과
[`ncs-fairness-research-bundle.json`](docs/ncs-fairness-research-bundle.json)에 보존합니다.

관련 문서:

- [코퍼스와 분할 정책](docs/corpus.md)
- [고용24 API](docs/work24-api.md)
- [민간 출처 정책](docs/private-job-sources.md)
- [비공개 공정성 모니터링](docs/private-fairness-monitoring.md)
- [증거 버전 관리](docs/evidence-versioning.md)

## 프로젝트 구조

```text
core/          결정론적 분석 엔진과 데이터 모델
data/          규칙, 질문, 슬롯, 법령 스냅샷
web/           빌드 없는 정적 웹 앱
cli/           fairpost CLI
mcp_server/    로컬·원격 MCP 서버와 저장소
api/           Vercel 함수 엔트리포인트
tools/         검증, 코퍼스, 평가, 릴리스 도구
tests/         엔진·웹·MCP·보안 회귀 테스트
docs/          설계 결정, 증거, 운영 문서
reports/       버전이 결합된 자동 생성 감사 산출물
```

Claude와 Codex가 제품 방향, 반론, 합의, 실행 범위를 함께 정리한 기록은
[AI 에이전트 검토 기록](docs/ai-agent-review-2026-08-31.md)에 있습니다.

## 기여와 라이선스

개발 절차는 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

- 소스 코드: [MIT](LICENSE)
- 규칙·데이터·문서: [CC BY 4.0](LICENSE-DATA)
