# fairpost

채용공고문에서 관련 법령 표현과 확인되지 않은 절차·정보 항목을 찾아,
담당자가 검토할 질문을 제시하는 결정론적 도구입니다. 정적 웹ㆍCLI는
기기 내에서, MCP는 로컬 또는 Vercel 원격 HTTP로 실행할 수 있습니다.

fairpost는 공정성 여부나 법 위반을 판정하지 않습니다. 점수, 등급,
합격·통과 판정을 만들지 않으며 런타임에 LLM이나 외부 API를 호출하지
않습니다. 엔진은 입력 공고문을 영속 저장하지 않습니다. 다만 원격 MCP를
사용하면 공고문은 AI 제공자와 Vercel 서버를 거치므로 완전한 기기 내
처리가 필요하면 정적 웹 또는 CLI를 사용해야 합니다.

매칭 과정에서는 전각 문자ㆍ비표준 공백ㆍ제로폭 문자와 제한된 한국어
어미를 정규화하지만, 결과의 offset과 인용문은 입력 원문을 그대로
가리킵니다.

PRD 항목별 현재 충족 상태와 남은 품질 증거는
[docs/prd-traceability.md](docs/prd-traceability.md)에 기록합니다.
요구사항별 최종 증거와 아직 충족되지 않은 외부 조건은
[docs/completion-audit.md](docs/completion-audit.md)에 구분해 기록합니다.
법령 개정 감시와 사람 검수 절차는
[docs/statute-maintenance.md](docs/statute-maintenance.md)에 있습니다.
대상 조문과 규칙ㆍ질문 연결 현황은
[docs/statute-scope-map.md](docs/statute-scope-map.md)에 있습니다.
책 확인본 6종과 실행 규칙의 연결은
[docs/book-map.md](docs/book-map.md), 능력중심ㆍ공정채용 가이드의 적용은
[docs/ability-based-hiring-guide-map.md](docs/ability-based-hiring-guide-map.md)에
기록합니다.

## 빠른 시작

Python 3.11 이상에서 설치하고 CLI를 실행합니다.

```powershell
python -m pip install -e ".[dev]"
fairpost check .\posting.txt
```

표준 입력도 사용할 수 있습니다.

```powershell
Get-Content .\posting.txt -Raw | fairpost check -
```

정적 웹은 [web/index.html](web/index.html)을 브라우저에서 열면 됩니다.
서버, 빌드, API 키가 필요하지 않습니다.

## MCP 연결

MCP 서버의 기본 전송 방식은 Streamable HTTP입니다. 기본 설정은 사용자
컴퓨터 안에서만 접근할 수 있는 `http://127.0.0.1:8000/mcp`입니다.

```powershell
fairpost-mcp
```

저장소의 `.mcp.json`은 Claude Code 프로젝트 범위에서 이 주소를
Streamable HTTP 서버로 등록합니다. 같은 설정을 다시 만들려면 다음 명령을
사용합니다.

```powershell
claude mcp add --transport http --scope project fairpost http://127.0.0.1:8000/mcp
```

Cursor 등 HTTP MCP를 지원하는 클라이언트 설정 예시는 다음과 같습니다.

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

제공 도구는 `check_job_posting`, `save_answer`, `get_saved_answers`입니다.
조직별 답변은 사용자 로컬의 `~/.fairpost/answers.json`에만 기록됩니다.
`FAIRPOST_ANSWERS_PATH`로 다른 로컬 경로를 지정할 수 있습니다.
`save_answer`는 현재 사전에 존재하는 질문 ID만 허용해 오타가 저장되는 것을
막습니다.
호스트와 포트는 `FAIRPOST_MCP_HOST`, `FAIRPOST_MCP_PORT`로 변경할 수
있습니다. 이전 클라이언트와의 호환성 확인이 필요한 경우에만
`fairpost-mcp-stdio`를 사용합니다.
Claude 계열 클라이언트와 공식 MCP Inspector의 확인 절차와 현재 검증
기록은 [docs/mcp-clients.md](docs/mcp-clients.md)에 있습니다.

Vercel 원격 MCP 엔트리포인트는 `/api/mcp`입니다. 기본적으로 Bearer
인증이 없으면 요청을 거부하고, `save_answer`를 사용하려면 Upstash Redis를
연결해야 합니다. 배포와 연결 절차는
[docs/vercel-deployment.md](docs/vercel-deployment.md)에 있습니다.
Claude Desktop 원격 커넥터에는 무인증 읽기 전용 엔드포인트
`https://fairpost-mcp.vercel.app/api/claude-mcp`를 사용합니다. 이
엔드포인트는 `readOnlyHint: true`인 `check_job_posting`만 제공하며
저장ㆍ수정 도구를 노출하지 않습니다.
루프백이 아닌 자체 호스트 바인딩은 여전히 기본적으로 거부하며, 이 운영
정책을 명시적으로 변경한 경우에만 `FAIRPOST_ALLOW_REMOTE=1`로 허용합니다.

## 고용24 API

고용24 API는 최종 사용자의 공고 점검에 연결하지 않습니다. 사전 구축용
민간 채용공고 코퍼스를 수집할 때만 `tools/collect_corpus.py`가 사용합니다.

루트의 `.env`에 기업회원용 채용정보 API 인증키를 넣습니다.

```dotenv
WORK24_AUTH_KEY=발급받은_인증키
```

따옴표는 있어도 제거되지만 사용하지 않는 편이 명확합니다. 공고 목록
응답의 `wantedAuthNo`, `company`, `busino`, `indTpNm`, `title`,
`salTpNm`, `sal`, `minSal`, `maxSal`, `region`, `holidayTpNm`,
`minEdubg`, `maxEdubg`, `career`, `regDt`, `closeDt`, `jobsCd`,
`smodifyDtm` 등의 태그는 XML 필드이며 `.env` 항목이 아닙니다.
전체 필드 설명은 [docs/work24-api.md](docs/work24-api.md)에 있습니다.

```powershell
python tools\collect_corpus.py `
  --source work24 `
  --limit-per-source 300 `
  --exclude-manifest .corpus\train\manifest.json `
  --exclude-manifest .corpus\holdout\manifest.json `
  --output-dir .corpus-private `
  --summary reports\private_corpus_summary.json

python tools\combine_corpora.py `
  --public-dir .corpus `
  --private-dir .corpus-private `
  --output-dir .corpus-final `
  --summary reports\corpus_summary.json
```

첫 명령은 이미 고정된 공공 300건과 원문 해시가 겹치지 않는 민간 300건을
수집해 별도로 70/30 분할합니다. 두 번째 명령은 기존 공공 분할과 새 민간
분할을 재분할하지 않고 결합해 최종 420/180 세트를 만듭니다. 권한만
진단하려면 별도 `--output-dir`에서 1건으로 먼저 호출합니다.

현재 고용24 채용정보 서비스는 계정 유형에 따라 호출 권한이 다릅니다.
응답이 `개인회원은 사용할 수 없는 OPEN-API입니다.`라면 인증키 문자열
형식 문제가 아니라 해당 서비스의 기업회원 권한 문제입니다.

## 민간 공개 구인정보

고용24 권한과 별개로 공공데이터포털의 진천군 일자리 구인정보 CSV를
민간 코퍼스에 사용할 수 있습니다. 원천 38,700행 중 최신 공고 3,000건을
비식별화해 2,100/900으로 고정 분할합니다.

```powershell
python tools\collect_corpus.py `
  --source jincheon-jobs `
  --limit-per-source 3000 `
  --output-dir .corpus-private-open `
  --summary reports\private_open_corpus_summary.json
```

잡코리아ㆍ매칭뱅크 같은 민간 사이트는 공개 열람과 자동 대량수집의
허용범위가 다르므로 별도 제휴ㆍ이용허락 없이 크롤링하지 않습니다.
출처별 판단과 추가 API 신청 항목은
[docs/private-job-sources.md](docs/private-job-sources.md)에 정리했습니다.

## 청년 일자리 API

재정경제부 청년 일자리 올인원 지원 서비스의 공공기관 채용정보도
사전 구축용 코퍼스 수집기에만 연결합니다. `.env`에는 인증키와 승인된
Swagger에 표시된 채용 목록 URL을 둡니다.

```dotenv
YOUTH_JOB_SERVICE_AUTH_KEY=발급받은_인증키
YOUTH_JOB_SERVICE_URL=승인된_채용목록_전체_URL
```

```powershell
python tools\collect_corpus.py `
  --source youth-job `
  --limit-per-source 300 `
  --output-dir .corpus-youth-api
```

발급 사이트와 호출 사이트가 다르면 인증키가 유효해도 `401`이 날 수
있습니다. 알리오 발급키와 공공데이터포털 서비스키의 구분, 공식 검색
페이지 기반 대체 수집 명령은
[docs/youth-job-api.md](docs/youth-job-api.md)에 정리했습니다.

## 공개 공고 코퍼스

잡알리오, 클린아이 잡플러스, 나라일터, 청년 일자리 공식 사이트는
빌드 타임 공개 공고 학습용으로만 사용합니다.
원문은 `.corpus/` 아래에 저장되고 Git에서 제외됩니다. 담당자 이름,
전화번호, 이메일, 기관·기업명은 수집 과정에서 비식별화됩니다.

```powershell
python tools\collect_corpus.py `
  --source job-alio `
  --source cleaneye `
  --source gojobs `
  --limit-per-source 100 `
  --output-dir .corpus `
  --summary reports\corpus_summary.json
```

수집 즉시 직군·고용형태별 70% 학습 세트와 30% 홀드아웃으로 고정
분할합니다. `tools/mine_candidates.py`는 경로에 `holdout`이 포함되면
실행을 거부합니다. 매칭뱅크 등 민간 사이트는 별도 이용허락 없이 자동
수집하지 않습니다.

학습 세트에서 확정 규칙과 슬롯의 관찰 빈도만 익명 집계할 수 있습니다.

```powershell
python tools\analyze_corpus.py
```

PRD의 정식 성능 평가 모집단은 공공 300건과 민간 300건, 총 600건입니다.
확장 코퍼스의 기존 학습ㆍ홀드아웃 배정을 보존하면서 정식
420/180 세트를 만들고 오프라인 라벨러를 생성합니다.

```powershell
python tools\build_prd_corpus.py
python tools\build_annotation_ui.py
python tools\build_human_labeling_handoff.py
```

정식 세트는 `.corpus-prd/`, 익명 집계는
`reports/prd_corpus_summary.json`, 사람 검수 인계 정보는
`reports/human_labeling_handoff.json`에 있습니다. 민간 3,000건을 모두
포함한 `.corpus-final` 3,300건은 추가 스트레스 분석용으로 유지합니다.

## 규칙과 스냅샷

- `data/rules/law.yaml`: 법령 관련 표현 규칙
- `data/rules/questions.yaml`: 질문 카드
- `data/rules/rejected.yaml`: 기각·유보 후보와 검수 사유
- `data/slots.yaml`: 11개 절차·정보 슬롯
- `data/statutes/`: 6개 법령의 로컬 조문 스냅샷
- `data/local_rules.example.yaml`: 기관 자체 규칙 템플릿

현재 기본 사전은 법령 표현 규칙 19개와 검토 질문 34개입니다. 문의처와
이의신청ㆍ인간 재검토 경로는 서로 다른 슬롯으로 확인하며, AI 채용의
데이터ㆍ평가항목ㆍ결정 역할ㆍ인간 개입, 대리변수, 결과 피드백,
지원자 부담 채용심사비용을 각각 별도로 다룹니다.

기관 자체 규칙은 `basis.type: consensus`만 허용합니다. 이를 법령
근거로 표시하면 로딩 단계에서 실패합니다.

CLI의 `--local-rules` 또는 MCP 실행 환경의
`FAIRPOST_LOCAL_RULES_PATH`로 사용자 로컬 질문 사전을 연결할 수
있습니다.

## 개발 및 검증

```powershell
python -m pytest
python tools\validate_data.py
python tools\export_web_bundle.py --check
python tools\build_statutes.py
python tools\verify_web_parity.py
python tools\verify_distribution.py
```

코드는 MIT 라이선스입니다. `data/`, `docs/`, README 등 사전과 문서는
CC BY 4.0으로 제공합니다.

> 이 결과는 점검 참고자료이며 공정성 여부에 대한 판정이나 법률 자문이
> 아닙니다. 확인되지 않은 항목은 해당 절차가 없다는 뜻이 아니라 이
> 공고문에서 발견되지 않았다는 뜻입니다.
