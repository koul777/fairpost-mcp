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
v1.0 진입 조건과 30ㆍ60ㆍ90일 실행 순서는
[docs/roadmap.md](docs/roadmap.md)에 기록합니다.
Claude와 Codex의 독립 반론ㆍ합의ㆍ오늘 커밋 범위는
[docs/ai-agent-review-2026-08-30.md](docs/ai-agent-review-2026-08-30.md)에 기록합니다.
현재 자동 생성 증거는 후보(candidate) 보고서이며, v1 릴리스 가능 여부는
`reports/build_artifact.json`의 `release_readiness`와 차단 사유로 판단합니다.
법령 개정 감시와 사람 검수 절차는
[docs/statute-maintenance.md](docs/statute-maintenance.md)에 있습니다.
대상 조문과 규칙ㆍ질문 연결 현황은
[docs/statute-scope-map.md](docs/statute-scope-map.md)에 있습니다.
책 확인본 6종과 실행 규칙의 연결은
[docs/book-map.md](docs/book-map.md), 능력중심ㆍ공정채용 가이드의 적용은
[docs/ability-based-hiring-guide-map.md](docs/ability-based-hiring-guide-map.md)에
기록합니다.
질문 관련성 표본 검토, 공통 체크리스트 분리와 오발동 감소 결과는
[docs/question-relevance-audit.md](docs/question-relevance-audit.md)에
기록합니다.

## 빠른 시작

Python 3.11 이상에서 설치하고 CLI를 실행합니다.

```powershell
python -m pip install -e ".[dev]"
fairpost check .\posting.txt
```

표준 입력도 사용할 수 있습니다.

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
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

제공 도구는 `check_job_posting`, `next_review_question`, `save_answer`,
`get_saved_answers`입니다. `check_job_posting`은 모든 findingㆍslotㆍ질문을
누락하지 않은 사람이 읽기 쉬운 일반 텍스트를 반환합니다.
`next_review_question`은 아직 답변이 저장되지 않은 질문 하나와
진행 상황만 돌려주어 한 번에 한 질문씩 진행할 때 사용합니다. 두 도구 모두
질문 문구를 사전에서 그대로 가져오며 코드에 문구를 만들어 넣지 않습니다.
조직별 답변은 사용자 로컬의 `~/.fairpost/answers.json`에만 기록됩니다.
`FAIRPOST_ANSWERS_PATH`로 다른 로컬 경로를 지정할 수 있습니다.
`save_answer`는 현재 사전에 존재하는 질문 ID만 허용해 오타가 저장되는 것을
막습니다.
로컬 호스트는 `FAIRPOST_MCP_HOST`로 세 가지 루프백 별칭(`127.0.0.1`,
`::1`, `localhost`) 중 하나를, 포트는 `FAIRPOST_MCP_PORT`로 변경할 수
있습니다. 이전 클라이언트와의 호환성 확인이 필요한 경우에만
`fairpost-mcp-stdio`를 사용합니다.
Claude 계열 클라이언트와 공식 MCP Inspector의 확인 절차와 현재 검증
기록은 [docs/mcp-clients.md](docs/mcp-clients.md)에 있습니다.

Vercel 원격 MCP 엔트리포인트는 `/api/mcp`입니다. 기본적으로 Bearer
인증이 없으면 요청을 거부합니다. 운영자가 공개 모드를 명시하면 이 주소도
열 수 있습니다. 인증 여부와 관계없이 원격 주소는 `org_id`를 받지 않는 분석용
2도구만 제공하며, 저장 2도구는 루프백 로컬 MCP에만 노출됩니다. 배포와 연결 절차는
[docs/vercel-deployment.md](docs/vercel-deployment.md)에 있습니다.
Claude Desktop 원격 커넥터용
`https://fairmcp.vercel.app/api/claude-mcp`도 기본적으로 비활성화됩니다.
Bearer 토큰을 설정하면 같은 인증을 적용하고, 토큰을 넣을 수 없는 커넥터를 위해
운영자가 `FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE=1`을 별도로 설정한 경우에만
무인증으로 엽니다. 이 엔드포인트는 `readOnlyHint: true`인
`check_job_posting`만 제공하며 저장ㆍ수정 도구를 노출하지 않습니다.
익명 모드는 클라이언트 주소를 인스턴스별 임시 HMAC 키로 익명화해 최대 1분만
보유하고 요청 원문을 기록하지 않는 분당 60회 고정 창 제한을 적용합니다.
이는 기본적인 방어층이며 분산 전역 제한은
아니므로 일반 공개 전에는 외부 게이트웨이의 전역 제한ㆍ남용 감시도 필요합니다.
답변 저장 기능이 있는 전체 MCP는 루프백이 아닌 호스트에 바인딩할 수 없습니다.
자체 호스팅 네트워크 경로도 `mcp_server.remote:app`의 읽기 전용 프로필을
사용해야 합니다.

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

민간 train 스냅샷은 원문ㆍIDㆍ조직ㆍ개인정보를 보고서에 남기지 않고
발동률과 이전 기준선 대비 변화만 반복 감사할 수 있습니다. 기준선이 준비된 뒤에는
스냅샷, 익명 감사, 드리프트 게이트, 선택 규칙의 로컬 검토 큐를 한 명령으로 만듭니다.

```powershell
python tools\run_private_fairness_cycle.py `
  --input incoming\private-postings.jsonl `
  --output-dir .corpus-private-monitoring `
  --snapshot-summary reports\private_monitoring_snapshot.json `
  --audit-output reports\private_fairness_audit_new.json `
  --baseline-audit reports\private_fairness_audit.json `
  --drift-output reports\private_fairness_drift.json `
  --review-queue-output .private-review\queue.jsonl `
  --sampling-audit-output reports\private_review_sampling_audit.json `
  --exclude-manifest .corpus-private-open\train\manifest.json `
  --rule-id SEX-001 `
  --rule-id AGE-002 `
  --rule-id PHOTO-001 `
  --rule-id RETURN-001 `
  --rule-id Q-DIST-012 `
  --rule-id Q-DIST-015 `
  --rule-id Q-DIST-016 `
  --rule-id Q-DIST-017 `
  --rule-id Q-INFO-014 `
  --per-rule 20 `
  --require-version-match
```

드리프트 경보가 있으면 산출물을 보존하고 종료 코드 `2`를 반환합니다. 검토 큐는 Git에서
제외됩니다. 큐를 네트워크가 차단된 단일 HTML 검토 화면으로 만든 뒤 브라우저에서 라벨을
입력하고, 내려받은 JSONL을 별도 집계 게이트로 확인합니다.

```powershell
python tools\build_private_review_ui.py `
  --input .private-review\queue.jsonl `
  --output .private-review\review.html
```

`review.html`을 로컬 브라우저로 열어 판정한 뒤 내려받은
`private-review-labeled.jsonl`을 `.private-review/`에 보관하고 다음 명령의 입력으로 사용합니다.
각 단계를 따로 실행하는 명령과 개인정보 경계는 상세 문서에 있습니다.

```powershell
python tools\summarize_private_review.py `
  --input .private-review\private-review-labeled.jsonl `
  --output .private-review\summary.json `
  --manifest .private-review\queue.jsonl.manifest.json `
  --source-input .corpus-private-monitoring\train\records.jsonl `
  --min-reviewed-per-rule 10 `
  --min-precision 0.80 `
  --expect-rule-id SEX-001 `
  --expect-rule-id AGE-002 `
  --expect-rule-id PHOTO-001 `
  --expect-rule-id RETURN-001 `
  --expect-rule-id Q-DIST-012 `
  --expect-rule-id Q-DIST-015 `
  --expect-rule-id Q-DIST-016 `
  --expect-rule-id Q-DIST-017 `
  --expect-rule-id Q-INFO-014
```

`--manifest`가 큐 생성 당시 선택한 규칙과 라벨 외 필드의 무결성을 검증하고,
`--source-input`이 원본 train snapshot의 바이트 해시를 대조합니다. 양수 품질 임계값을
통과하려면 `--expect-rule-id`도 호출자가 명시해야 하며 manifest 선택 규칙과 정확히
같아야 합니다. 이 train 표본 임계값은 운영 검토 게이트이며 PRD 홀드아웃
정밀도ㆍ재현율 목표의 달성 증거가 아닙니다.

입력 JSONL 계약은
`examples/private_monitoring_input.example.jsonl`에서 확인할 수 있습니다.
이 파일의 기관명ㆍURLㆍ공고 문장은 모두 문서화 목적의 합성 예시이며 실제
채용공고가 아닙니다. 배포 감사는 이 합성 원문과 비공개 실제 원문을 구분해
기록합니다.
입력ㆍ개인정보 경계와 기준선 비교 방법은
[docs/private-fairness-monitoring.md](docs/private-fairness-monitoring.md)에 있습니다.

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

사람 라벨을 만들기 전에는 [평가 무결성 프로토콜](docs/evaluation-protocol.md)에
따라 train calibration과 봉인 holdout final을 분리합니다. AI가 만든 라벨을
사람 정답으로 사용하지 않으며, 최종 평가는 현재 입력ㆍ규칙ㆍ매칭 버전에
결합된 사람 확인서와 최초 공개 영수증을 요구합니다. 같은 홀드아웃 결과를
본 뒤 규칙을 수정해 다시 출시 성능으로 주장하지 않습니다.

## 출시 증거와 제품 파일럿

규칙 변경 뒤 보고서의 과거 `passed` 값을 현재 증거로 오인하지 않도록 로컬
산출물 버전을 한 번에 검사합니다.

```powershell
python tools\check_evidence_versions.py --scope local
```

운영 배포와 빌드 증거까지 확인할 때는 `--scope all`을 사용합니다. stale
보고서의 버전 문자열만 바꾸지 않고 원 입력과 생성 도구로 재생성하며,
재생성할 수 없는 과거 연구는 `evidence_status: historical`로 명시합니다.
상세 절차는 [릴리스 증거 버전 관리](docs/evidence-versioning.md)에 있습니다.

질문 카드의 유용성은 G1ㆍG2 정확도가 아니라 실제 채용담당자 파일럿에서
별도로 측정합니다. 원문ㆍ조직명ㆍ자유서술을 수집하지 않는 입력 계약과
집계 명령은 [파일럿 프로토콜](docs/pilot-protocol.md)에 있습니다. 민간
코퍼스의 단일 출처ㆍ현장직 편중은 다음 익명 게이트로 추적합니다.

```powershell
python tools\audit_corpus_diversity.py
```

현재 다양성 상태는 의도적으로 `alert`이며, 허가된 독립 출처가 실제로
추가되기 전에는 경보를 해제하지 않습니다. 기준은
[민간 코퍼스 다양성 게이트](docs/corpus-diversity-gate.md)에 있습니다.

결정론 엔진의 개발 장비 기준 성능은 holdout을 열지 않고 train 입력만으로
재현합니다. 보고서에는 원문ㆍ레코드 IDㆍ기관명ㆍ개별 타이밍을 남기지 않습니다.

```powershell
python tools\benchmark_engine.py
```

측정 방법과 해석 한계는 [엔진 성능 벤치마크](docs/performance.md)에 있으며,
이 수치는 네트워크ㆍMCP 전송ㆍ동시 요청을 포함한 운영 SLA가 아닙니다.

## 규칙과 스냅샷

- `data/rules/law.yaml`: 법령 관련 표현 규칙
- `data/rules/questions.yaml`: 질문 카드
- `data/rules/rejected.yaml`: 기각·유보 후보와 검수 사유
- `data/slots.yaml`: 11개 절차·정보 슬롯
- `data/statutes/`: 6개 법령의 로컬 조문 스냅샷
- `data/local_rules.example.yaml`: 기관 자체 규칙 템플릿

현재 기본 사전은 법령 표현 규칙 19개와 검토 질문 52개입니다. 네 공정성
차원(분배ㆍ절차ㆍ대인ㆍ정보)은 각각 최소 한 개 이상의 질문이 일반 공고에서도
발동하도록 유지하며, 이 속성은 회귀 테스트로 고정합니다. 질문 카드에는
가능한 경우 발동 문맥ㆍ원문 offsetㆍ섹션을 함께 포함해 담당자가 왜 그 질문을
확인해야 하는지 추적할 수 있습니다. 문의처와
이의신청ㆍ인간 재검토 경로는 서로 다른 슬롯으로 확인하며, AI 채용의
데이터ㆍ평가항목ㆍ결정 역할ㆍ인간 개입, 대리변수, 결과 피드백,
지원자 부담 채용심사비용을 각각 별도로 다룹니다.

질문의 `review_scope`는 해당 공고에서 바로 볼 `posting`과 채용 전반에서
한 번 확인할 `common`을 구분합니다. 정적 웹은 공고별 질문을 먼저 보여
주고 공통 기본 체크리스트와 후속 질문은 접어서 표시합니다. 이의제기
경로ㆍ채용서류 반환ㆍ평가 기준처럼 누락 슬롯과 의미가 겹치며 자주
반복되는 질문은 해당 `확인되지 않은 항목` 카드 안에서 펼쳐봅니다. API의
기존 `questions` 배열과 `counts.questions`는 그대로 유지합니다.

법령 규칙의 `related_questions`는 그 표현이 발견됐을 때 함께 확인할 질문을
사전에 기록합니다. 로더는 존재하지 않는 질문 ID를 거부하므로 연결이 코드나
문서에서만 유지되다 어긋나는 일이 없습니다. 연결된 질문은 자체 트리거가
발동하지 않아도 결과에 포함되며 `trigger_reason: finding`으로 표시합니다.
다만 그 질문 자신의 보호 문맥 예외가 표현을 이미 걸러냈다면 연결은 그
판단을 덮어쓰지 않습니다.

각 질문의 `priority`는 표시 순서를 정합니다. 1은 발동한 법령 표현과 연결된
질문, 2는 공고 문구에서 직접 발동한 질문, 3은 누락 슬롯에서 나온 질문,
4는 공통 체크리스트입니다. 결과는 `(priority, id)` 순으로 정렬되어 같은
입력에 항상 같은 순서를 냅니다. `linked_findings`에는 그 질문을 불러온
법령 규칙 ID가 들어갑니다.

기관 자체 규칙은 `basis.type: consensus`만 허용합니다. 이를 법령
근거로 표시하면 로딩 단계에서 실패합니다. `related_questions`는 법령
규칙에만 둘 수 있습니다.

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
