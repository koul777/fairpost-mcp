<div align="center">

# FairPost MCP

[![FairPost MCP 홍보·시연 영상](docs/assets/fairpost-promo.gif)](docs/assets/fairpost-promo.mp4)

<sub>미리보기를 누르면 1920×1080 MP4 시연 영상을 볼 수 있습니다.</sub>

**채용공고의 표현, 빠진 정보, 확인할 질문을 근거와 함께 정리하는 결정론적 리뷰 도구**

[웹에서 체험하기](https://fairmcp.vercel.app/web/) · [MCP 연결](#mcp-연결) · [개발 로드맵](docs/roadmap.md)

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

## 지금 사용해 보기

가장 빠른 방법은 [배포된 웹 앱](https://fairmcp.vercel.app/web/)을 여는 것입니다.
샘플 공고를 불러오거나 직접 문장을 붙여 넣은 뒤 **공고 점검**을 누르면 됩니다.
정적 웹에서는 입력과 분석이 브라우저 안에서만 처리됩니다.

Python 3.11 이상에서는 CLI를 사용할 수 있습니다.

```powershell
python -m pip install -e ".[dev]"
fairpost check .\posting.txt
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
| 사람 중심 | 판정 대신 수정할 표현과 추가로 확인할 질문을 제시합니다. |
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

로컬 MCP는 아래 네 도구를 제공합니다.

- `check_job_posting`: 전체 표현·누락·질문 검토
- `next_review_question`: 아직 답하지 않은 질문 한 개와 진행률 반환
- `save_answer`: 유효한 질문 ID에 대한 조직별 답변 저장
- `get_saved_answers`: 저장된 답변 조회

답변은 사용자 컴퓨터의 `~/.fairpost/answers.json`에만 저장됩니다.

### Vercel 원격 MCP

운영 엔드포인트는 `https://fairmcp.vercel.app/api/mcp`입니다. 기본 설정에서는
Bearer 인증이 필요하며, 원격 환경에는 `org_id`와 로컬 답변 저장 기능을 받지
않는 분석용 읽기 도구만 노출합니다. Claude Desktop용 제한 엔드포인트와 인증,
배포 절차는 [Vercel 배포 가이드](docs/vercel-deployment.md)를 참고하세요.

원격 MCP를 사용하면 공고문이 AI 제공자와 Vercel 서버를 거칩니다. 완전한
기기 내 처리가 필요하다면 정적 웹, CLI 또는 루프백 로컬 MCP를 사용하세요.

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
| 자동화 테스트 | 863 passed |
| 전체 데이터 규칙 | 71 |
| 질문 카드 | 52 |
| 배포 형태 | 정적 웹 + Vercel 읽기 전용 MCP |

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
[AI 에이전트 검토 기록](docs/ai-agent-review-2026-08-30.md)에 있습니다.

## 기여와 라이선스

개발 절차는 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

- 소스 코드: [MIT](LICENSE)
- 규칙·데이터·문서: [CC BY 4.0](LICENSE-DATA)
