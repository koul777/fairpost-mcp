# 민간 채용공고 공정성 모니터링

기준일: 2026-08-04

## 목적과 경계

이 흐름은 공개 민간 채용공고에서 법령 관련 표현, 확인되지 않은 절차 정보,
사람 검토 질문의 변화를 반복 관찰한다. 결과는 위법ㆍ차별ㆍ공정성을 자동
판정하지 않는다. 공고 원문, 레코드 ID, 조직명, 담당자와 연락처는 배포
보고서에 기록하지 않는다.

현재 정량 기준선은 `.corpus-private-open/train/records.jsonl`의 민간
2,100건이다. 출처 범주는 `jincheon-jobs` 하나이므로 전체 민간 시장의
성능이나 발생률로 일반화할 수 없다. 봉인 holdout은 이 흐름에서 읽지 않는다.

외부 근거와 공개 사례의 claim-evidence 기록은
`docs/private-fairness-research-bundle.json`에 분리했다.

채용절차법은 상시 30명 이상의 근로자를 사용하는 사업ㆍ사업장의 채용절차에 적용된다.
따라서 이 문서의 법 규칙 발동은 공고 표현을 찾은 `검토 후보`이지 개별 기업의 법 위반
확정이 아니다. 실제 판단에는 사업장 규모, 요구 시점, 직무 관련성, 전자ㆍ자발 제출 예외와
후속 안내를 함께 확인해야 한다. 익명 발동률도 발생률ㆍ정밀도ㆍ시장 전체 위반률로 해석하지
않는다.

## 새 공개 공고를 안전하게 스냅샷으로 만들기

기업 채용 페이지, 고용24 공개 페이지, 이용허락을 받은 피드에서 확보한
공고는 다음 JSONL 계약으로 준비한다. 저장소의
`examples/private_monitoring_input.example.jsonl`은 실제 회사나 사람을
포함하지 않는 합성 예시다.

- `source_category`: `company-career-page`, `work24`, `licensed-feed` 중 하나
- `source_url`: 공개 HTTPS 공고 URL
- `published_at`: `YYYY-MM-DD` 게시일
- `organization`: 비식별화에 사용할 조직명
- `text`: 공고 본문

원천 JSONL을 지속 보관할 필요는 없다. 다음 명령은 공고문에서 선언된
조직명ㆍ이메일ㆍ전화번호ㆍ담당자 이름을 제거하고, URLㆍ본문 해시 중복을
결정론적으로 제거한 `train` 전용 스냅샷과 익명 요약을 만든다.

```powershell
python tools\build_private_monitoring_snapshot.py `
  --input incoming\private-postings.jsonl `
  --output-dir .corpus-private-monitoring `
  --summary reports\private_monitoring_snapshot.json `
  --exclude-manifest .corpus-private-open\train\manifest.json
```

`--exclude-manifest`는 반복할 수 있다. 기존 train의 `content_hashes`를
지정하면 재수집 공고가 제외된다. 출력은
`.corpus-private-monitoring/train/records.jsonl`과 `manifest.json`뿐이며,
요약에는 원문ㆍURLㆍ레코드 IDㆍ조직ㆍ연락처를 넣지 않는다. 입력ㆍ출력ㆍ
요약ㆍ제외 manifest 중 어느 경로라도 `holdout`, `hold-out`, `test`,
`tests`, `dev`, `evaluation`, `eval` 구성요소를 포함하면 읽기 전에 중단한다.
입력 레코드에 `split` 메타데이터가 있다면 정확히 `train`이어야 한다.
저장하는 `source_url`에서는 query와 fragment를 제거해 서명 토큰ㆍ추적값을
남기지 않으며, 원래 전체 URL은 출력 파일에 보존하지 않는다.

## 이번 조사에서 확인한 고우선 개선

### 서류 반환 거부와 전자제출 예외 분리

고용노동부의 2023년 하반기 점검 결과는 채용서류 미반환ㆍ미파기를 주요
사례로 제시한다. 한편 채용절차법 제11조는 홈페이지ㆍ전자우편 제출과
구인자 요구 없이 자발 제출한 경우를 반환 의무의 예외로 둔다.

따라서 fairpost는 두 층으로 나눈다.

- `RETURN-001`: 전자접수 예외 문맥이 없는 일률적 반환 제한 표현
- `Q-INFO-013`: 반환 대상ㆍ예외, 청구기간ㆍ방법, 보관ㆍ파기 및 플랫폼
  안내와의 상충을 사람이 확인하는 질문

### 성별 배제ㆍ선호ㆍ포용 분리

- `SEX-001`: `남성만`, `여성만`처럼 직접 대상을 제한하는 표현
- `Q-DIST-015`: `여성 우대`, `남성 인력 선호`처럼 우대ㆍ선호의 근거와
  범위를 확인할 표현
- 보호 문맥: `성별 무관`, `여성도 지원 가능`처럼 지원 범위를 넓히는 안내

### 국적 배제ㆍ근로자격 분리

- `Q-DIST-016`: 외국인ㆍ국적에 따른 직접 배제와 취업 가능한 체류자격ㆍ
  직무요건을 구분해 사람이 확인하는 질문
- 보호 문맥: `외국인 지원 가능`, `국적 무관`, 취업 가능한 체류자격 확인

### 혼인ㆍ임신ㆍ출산ㆍ자녀 질문 분리

- `Q-DIST-017`: 혼인 여부, 결혼 예정, 임신ㆍ출산 계획과 자녀 정보가
  채용 판단에 사용되는지 사람이 확인하는 질문
- 보호 문맥: 해당 정보를 지원서에 기재하지 말라는 블라인드 안내

우대ㆍ선호 질문은 법 위반 finding이 아니다. 직무의 본질적 요건 또는
적극적 고용개선 목적, 적용 대상과 기간을 사람이 확인한다.

### 사진 요구 변형

공개 민간 사례에는 `사진 필수` 외에 `사진 부착`, `이력서 사진 첨부`가
나타났다. `PHOTO-001`에 이 변형을 추가했으며 `사진 부착 불필요`,
`사진 제출 금지` 같은 보호적 안내는 제외한다.

### 합격자 한정 통보와 화상면접 분리

`합격자에 한해 개별 통보`, `불합격자 별도 통보 없음`은 결과 안내 슬롯에
문구가 있다는 이유로 기존 누락 질문이 사라질 수 있었다. `Q-INFO-014`는
이를 위반으로 단정하지 않고 불합격자를 포함한 안내 대상ㆍ시점ㆍ채널과
처리상태 확인 방법을 묻는다. 채용절차법에서 불합격자 전원 통지 의무를
확인했다는 뜻은 아니다. 근거는 NCS 공정채용 컨설팅이 제시하는
`불합격사유 자율 피드백` 가이드이며, 의무가 아니라 권고ㆍ지원 기준으로
구분한다. 단순 `화상 면접`, `온라인 인터뷰`는 AI 또는 자동화 평가 표현으로
취급하지 않는다.

### AI 결정의 적용 경계

개인정보 보호법 제37조의2는 개인정보를 처리한 완전 자동화 결정의 설명 요구와
조건부 거부ㆍ인적 재처리 절차를 규정한다. 현행 인공지능기본법은 중대한 영향을
미치는 채용 판단ㆍ평가가 고영향 AI에 해당할 때 위험관리, 설명 방안, 사람의
관리ㆍ감독을 검토하게 한다. `AI-001`은 `AI가 최종 결정`, `AI 자동 탈락`처럼
완전 자동화를 시사하는 문구만 검토 신호로 올린다. AI 사용 문구나 공고상 고지
부재만으로 실제 결정 구조, 적용 사업자, 다른 페이지의 고지 또는 위반을 확정하지
않는다. `ai_disclosure`와 관련 질문도 적용 단계ㆍ평가 역할ㆍ사람 재검토 권한을
확인하는 용도다.

### 건강ㆍ보호장구의 직무 필수성

국가인권위원회 공개 사례는 획일적 키ㆍ몸무게 기준보다 실제 업무와 연결된
과학적ㆍ객관적 수행평가가 필요하다고 설명한다. `Q-DIST-007`은 추상적인
건강 조건과 함께 `안전보호구 착용 가능자`, `방진마스크 착용 필수인 분`
같은 표현과 실제 기업 공고에서 확인한 `방진복 착용 가능한 자`의 작업위험 연결,
대체 장비ㆍ작업조정과 합리적 편의를 확인한다.
보호장구 지급 안내 자체는 질문을 발동하지 않는다.
국가인권위원회의 2010년ㆍ2022년 B형간염 채용거부 사례는 특정 질환 보유 사실만으로
일률 배제하지 않고 실제 수행 가능성을 개별 검토해야 한다는 외부 맥락으로만 연결했다.
이는 공고상 병력 기재 요구 자체가 곧 위법이라는 근거가 아니므로 `HEALTH-001`의
자동 탐지 범위를 넓히지 않았다.

### 장애 직접 배제와 편의 제공 분리

`DISABILITY-001`은 `장애인 지원 불가`, `비장애인만`처럼 장애를 이유로 지원
대상을 직접 제한하는 표현만 finding 후보로 둔다. 반대로 `장애인 우대`,
`합리적 편의 제공`, 보호장구ㆍ작업조정 안내는 법 위반으로 자동 단정하지 않고
`Q-DIST-007`에서 직무 필수성과 편의 제공 범위를 사람이 확인한다.

### 가족 증빙의 제출 단계

채용절차법은 기초심사자료와 입증자료에서 직무와 무관한 직계 존비속ㆍ형제자매의
학력ㆍ직업ㆍ재산을 요구ㆍ수집하지 못하게 하지만, 가족관계증명서라는 문서명 자체를 일률
금지한다고 적지는 않는다. 공개 민간 공고에는 이 증명서가 최종합격자의 입사서류로만
제시되는 사례도 있다. 따라서 `FAMILY-001`은 부모ㆍ형제자매의 학력ㆍ직업ㆍ재산 표현만
finding 후보로 삼는다. 가족관계증명서는 지원 단계와 최종합격 후 제출 모두 `Q-INFO-011`에서
시점, 목적, 마스킹과 평가위원 접근 여부를 확인한다. 사회형평 우대자격 입증도 자동 위반으로
단정하지 않는다. 고용노동부의 2022ㆍ2023ㆍ2024년 민간 중심 점검은 가족 학력ㆍ직업 또는
부모 재산 요구를 반복 지적했지만, 발표된 전체 조치 건수는 가족정보 위반만의 건수가 아니므로
발생률로 사용하지 않는다. 비식별 군집 감사에서 기존 `Q-INFO-011`은 158건이었고 주민등록초본과
기본증명서를 검토 문서명으로 확장한 뒤 162건이 됐다. 가족 학력ㆍ직업ㆍ재산 finding은
0건이다. `형제자매의 학력` 변형 52건은 51건이 기재 금지, 1건이 미수집 안내여서 모두
보호 문맥이었다. 변형 탐지와 보호 제외를 함께 구현했으며 상세 집계는
`reports/private_family_evidence_context_audit.json`에 둔다.

## 반복 실행

기준 익명 감사 보고서가 있으면 새 공개 공고의 비식별 스냅샷, 익명 감사,
드리프트 게이트와 선택 규칙의 로컬 검토 큐를 한 오프라인 명령으로 연결한다.
모든 읽기ㆍ쓰기 경로와 기준선 스키마를 먼저 검사하고, 방금 생성된
`train/records.jsonl`만 감사ㆍ표본화한다.

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
  --rule-id AI-001 `
  --rule-id DISABILITY-001 `
  --rule-id FAMILY-001 `
  --rule-id HEALTH-001 `
  --rule-id PHOTO-001 `
  --rule-id RETURN-001 `
  --rule-id Q-DIST-012 `
  --rule-id Q-DIST-013 `
  --rule-id Q-DIST-014 `
  --rule-id Q-DIST-015 `
  --rule-id Q-DIST-016 `
  --rule-id Q-DIST-017 `
  --rule-id Q-INFO-013 `
  --rule-id Q-INFO-014 `
  --rule-id Q-INFO-011 `
  --rule-id Q-PROC-006 `
  --rule-id Q-DIST-004 `
  --rule-id Q-DIST-006 `
  --rule-id Q-DIST-007 `
  --rule-id Q-DIST-010 `
  --per-rule 20 `
  --require-version-match
```

드리프트 경보가 있어도 snapshot, audit, drift와 queue 산출물은 보존하며 종료 코드 `2`를
반환한다. 입력ㆍ경로ㆍ스키마ㆍ실행 오류는 `1`, 정상은 `0`이다. 표준출력에는 건수와
상태만 있고 경로ㆍURLㆍ해시ㆍ원문ㆍ레코드 IDㆍ규칙 ID는 포함하지 않는다. review queue는
재식별 위험이 남는 로컬 산출물이므로 `.private-review/` 밖으로 전송하지 않는다.

스냅샷과 익명 감사만 갱신할 때는 하위 실행기를 직접 쓴다.

```powershell
python tools\run_private_monitoring.py `
  --input incoming\private-postings.jsonl `
  --output-dir .corpus-private-monitoring `
  --snapshot-summary reports\private_monitoring_snapshot.json `
  --audit-output reports\private_fairness_audit_new.json `
  --exclude-manifest .corpus-private-open\train\manifest.json `
  --baseline reports\private_fairness_audit.json
```

하위 실행기의 표준출력에는 성공 여부, 스냅샷ㆍ감사 레코드 수, 법 규칙이 하나 이상
발동한 레코드 수, 기준선 비교 여부만 JSON으로 출력한다. 경로ㆍURLㆍ해시ㆍ
원문ㆍ레코드 IDㆍ규칙 ID는 포함하지 않는다. 네트워크 호출도 하지 않으므로
스케줄러에서 승인 대기 없이 실행할 수 있다. 이 실행기는 snapshot과 audit 네 산출물을
복구 가능한 다중 파일 절차로 게시하고, 일반적인 파일 시스템 오류가 나면 기존 파일을 모두
복원한다. 통합 cycle도 일곱 필수 산출물과 선택한 sampling 감사를 모두 임시 경로에서
완성한 뒤 같은 방식으로 게시한다.
다중 rename은 단일 원자 연산이 아니므로 프로세스 강제 종료나 전원 손실까지 복구를 보장하지
않으며, 다음 실행 전에 `.fairpost-temp-*`ㆍ`.fairpost-backup-*` 잔여물을 확인해야 한다.

이미 존재하는 민간 train 스냅샷만 익명 집계할 때는 감사 도구를 직접 쓴다.

```powershell
python tools\audit_private_fairness.py `
  --input .corpus-private-open\train\records.jsonl `
  --output reports\private_fairness_audit.json
```

이전 스냅샷과 비교할 때는 이전 익명 감사 JSON 또는 기존
`analyze_corpus.py` 보고서를 기준선으로 지정한다.

```powershell
python tools\audit_private_fairness.py `
  --input .corpus-private-new\train\records.jsonl `
  --manifest .corpus-private-new\train\manifest.json `
  --baseline reports\private_fairness_audit.json `
  --output reports\private_fairness_audit_new.json
```

보고서는 다음만 포함한다.

- 입력 바이트 SHA-256, 레코드 수와 승인된 출처 범주별 건수
- 법 규칙ㆍ질문ㆍ슬롯의 공고 단위 발동 건수와 비율
- 한 번도 관찰되지 않은 규칙ㆍ질문
- 고빈도 질문과 고빈도 누락 슬롯
- 기준선 대비 건수 변화와 규칙ㆍ매칭 버전 호환 여부
- 신규 snapshot manifest를 지정한 경우 레코드 바이트 해시ㆍIDㆍ본문 해시ㆍ
  출처별 건수 무결성 검증 여부

임의 source 문자열은 `other`로 묶는다. 원문ㆍIDㆍ조직ㆍ개인정보는
보고서에 포함하지 않는다. 입력 경로와 해결된 실제 경로 모두 정확한
`train` 구성요소가 있어야 하며 `holdout`, `test`, `dev`, `evaluation`
등은 파일을 읽기 전에 거부한다.

단일 실행기는 manifest 무결성을 항상 확인하고
`input.manifest_verified=true`를 기록한다. 기존 레거시 코퍼스처럼 새
manifest 계약이 없는 입력을 감사 도구로 직접 읽으면 이 값은 `false`다.

## 회차별 드리프트 감시

새 회차의 익명 감사 보고서는 이전 회차의 익명 감사 보고서와 비교한다. 비교 도구는 원문이나
공고 ID를 읽지 않고 규칙별 공고 비율, 출처 범주별 점유율, 0건과 관찰 상태 사이의 전이,
규칙·매칭 버전 호환성만 검사한다.

```powershell
python tools\check_private_fairness_drift.py `
  --current reports\private_fairness_audit_new.json `
  --baseline reports\private_fairness_audit.json `
  --output reports\private_fairness_drift.json `
  --max-record-rate-delta 0.10 `
  --max-source-share-delta 0.20 `
  --require-version-match
```

정상일 때 종료 코드는 `0`, 입력 계약이나 경로 오류는 `1`, 검토가 필요한 드리프트 경보는
`2`다. `0건→관찰` 또는 `관찰→0건` 전이는 비율 임계치보다 작더라도 경보가 된다. 버전이
바뀐 두 보고서를 의도적으로 비교할 때에는 `--require-version-match`를 빼면 버전 차이는
보고서에 남지만 그 차이만으로 실패시키지는 않는다. CI의 합성 공고 스모크 테스트는 기준선
생성 후 통합 cycle을 실행해 snapshot, manifest 검증, 익명 감사, 자기 비교 드리프트와 선택
규칙 review queue까지 재현한다. 이어 미라벨 큐의 summary가 산출물을 남기고 종료 코드 `2`로
검토 미완료를 차단하는지도 확인한다.

## 사람 검토 큐

관찰 건수는 정확도나 위반 건수가 아니다. 규칙별 검토 적합도를 확인할 때에는 train
스냅샷에서 결정론적 표본을 뽑아 로컬 큐를 만든다. 이 표본은 고정 해시 순서와 고유
비식별 문맥 축약을 사용하므로 게시물 모집단에서 무작위 추출한 정밀도 표본이 아니다.

```powershell
python tools\build_private_review_queue.py `
  --input .corpus-private-open\train\records.jsonl `
  --output .private-review\queue.jsonl `
  --rule-id SEX-001 `
  --rule-id AGE-002 `
  --rule-id AI-001 `
  --rule-id DISABILITY-001 `
  --rule-id FAMILY-001 `
  --rule-id HEALTH-001 `
  --rule-id PHOTO-001 `
  --rule-id RETURN-001 `
  --rule-id Q-DIST-012 `
  --rule-id Q-DIST-013 `
  --rule-id Q-DIST-014 `
  --rule-id Q-DIST-015 `
  --rule-id Q-DIST-016 `
  --rule-id Q-DIST-017 `
  --rule-id Q-INFO-013 `
  --rule-id Q-INFO-014 `
  --rule-id Q-INFO-011 `
  --rule-id Q-PROC-006 `
  --rule-id Q-DIST-004 `
  --rule-id Q-DIST-006 `
  --rule-id Q-DIST-007 `
  --rule-id Q-DIST-010 `
  --per-rule 20
```

각 행의 `label`을 `true_positive`, `false_positive`, `uncertain` 가운데 하나로 바꾼다.
큐에는 조직명·URL·원본 레코드 ID를 넣지 않고 문맥의 이메일·전화·담당자명을 다시
비식별화한다. 그래도 짧은 원문 문맥에는 재식별 위험이 남을 수 있으므로 `.private-review/`는
Git에서 제외하며 큐를 외부로 전송하거나 보고서에 첨부하지 않는다. 표본 수가 20보다 적으면
그 규칙의 고유 비식별 문맥이 20개보다 적다는 뜻이다. 반복 문맥은 한 행으로 축약되므로
관찰 공고 전체를 검토했다는 뜻은 아니다. 같은 공고가 여러 규칙에 걸리면 규칙별 행으로
중복될 수 있으므로 전체 aggregate precision은 모집단 정밀도로 해석하지 않고 규칙별 지표를
기본으로 사용한다. absence 질문은 개별 매치 문맥이 없으므로 이 큐의 대상이 아니다.
현재 22개 규칙의 축약ㆍ절단 집계는
`reports/private_review_sampling_audit.json`에 비식별 보고서로 보존한다.

```powershell
python tools\build_private_review_sampling_audit.py `
  --queue .private-review\queue.jsonl `
  --manifest .private-review\queue.jsonl.manifest.json `
  --source-input .corpus-private-open\train\records.jsonl `
  --output reports\private_review_sampling_audit.json
```

이 명령은 원본 train에서 동일한 규칙ㆍquotaㆍ문맥 설정으로 큐를 다시 계산해 sidecar와
라벨 외 행이 정확히 재현될 때만 익명 집계를 쓴다.

큐와 함께 `queue.jsonl.manifest.json`이 생성된다. 이 sidecar에는 원문ㆍ문맥ㆍ검토 ID를
넣지 않고 규칙ㆍ매칭 버전, 입력 스냅샷 해시, 선택 규칙, 표본 파라미터, 행 수와 라벨을
제외한 필드의 다이제스트만 기록한다. 규칙별 후보 매치ㆍ고유 문맥ㆍ선택 행ㆍ중복 축약ㆍ
quota 절단 수도 `rule_sampling` 집계로 기록한다. 큐와 manifest는 복구 가능한 한 세대로 게시되며,
일반적인 게시 실패 때에는 이전 두 파일을 복원한다. 강제 종료까지 단일 원자성을 보장하지는
않는다. 이 다이제스트는 실수로 다른 큐를 섞거나 라벨 외 필드를
바꾼 경우를 찾는 로컬 무결성 장치이지, 비밀키로 서명된 외부 신뢰 증명은 아니다. 큐와
manifest를 함께 임의로 다시 만든 행위까지 인증하지는 않는다.

큐를 네트워크가 차단된 단일 HTML 검토 화면으로 만들 수 있다.

```powershell
python tools\build_private_review_ui.py `
  --input .private-review\queue.jsonl `
  --output .private-review\review.html
```

`review.html`은 외부 스크립트ㆍ글꼴ㆍ이미지ㆍ네트워크 요청을 허용하지 않고 큐를 base64로
포함한다. 브라우저에서 규칙별 필터를 사용해 `true_positive`, `false_positive`, `uncertain`을
선택할 수 있고, 실수한 판정은 `미검토로 되돌리기`로 복원할 수 있다. 다운로드 버튼으로 받은
`private-review-labeled.jsonl`을 `.private-review/` 안에 보관한다. HTML과 다운로드 파일 모두
짧은 문맥을 포함하므로 외부 전송ㆍ공유ㆍ커밋을 금지한다.
각 카드에는 현재 규칙 메시지 또는 사람 검토 질문을 표시한다. 이 화면의 `정탐`은 규칙과의
관련성을 뜻할 뿐 차별이나 법 위반의 확정 판정이 아니라는 층별 안내도 함께 표시한다.

라벨링 뒤에는 행 단위 문맥을 다시 내보내지 않는 집계 게이트를 실행한다. 입력은 원래의
미라벨 큐가 아니라 검토 화면에서 내려받은 파일이어야 한다.

```powershell
python tools\summarize_private_review.py `
  --input .private-review\private-review-labeled.jsonl `
  --output .private-review\summary.json `
  --manifest .private-review\queue.jsonl.manifest.json `
  --source-input .corpus-private-open\train\records.jsonl `
  --min-reviewed-per-rule 10 `
  --min-precision 0.80 `
  --expect-rule-id SEX-001 `
  --expect-rule-id AGE-002 `
  --expect-rule-id AI-001 `
  --expect-rule-id DISABILITY-001 `
  --expect-rule-id FAMILY-001 `
  --expect-rule-id HEALTH-001 `
  --expect-rule-id PHOTO-001 `
  --expect-rule-id RETURN-001 `
  --expect-rule-id Q-DIST-012 `
  --expect-rule-id Q-DIST-013 `
  --expect-rule-id Q-DIST-014 `
  --expect-rule-id Q-DIST-015 `
  --expect-rule-id Q-DIST-016 `
  --expect-rule-id Q-DIST-017 `
  --expect-rule-id Q-INFO-013 `
  --expect-rule-id Q-INFO-014 `
  --expect-rule-id Q-INFO-011 `
  --expect-rule-id Q-PROC-006 `
  --expect-rule-id Q-DIST-004 `
  --expect-rule-id Q-DIST-006 `
  --expect-rule-id Q-DIST-007 `
  --expect-rule-id Q-DIST-010
```

정상은 종료 코드 `0`, 입력 오류는 `1`, 검토 표본 부족 또는 정밀도 하한 미달은 보고서를
쓴 뒤 `2`를 반환한다. 최소 검토 수에는 정밀도 분모를 실제로 형성하는 확정 라벨
`true_positive`와 `false_positive`만 포함한다. `uncertain`은 별도 집계하지만 최소 검토 수와
정밀도 분모에서는 제외한다. 정밀도는
`true_positive / (true_positive + false_positive)`로 계산한다. 집계 보고서에는
문맥·매치 문자열·검토 ID가 포함되지 않는다. 큐 생성 때 선택한 각 `--rule-id`를 집계의
`--expect-rule-id`로 반복할 수 있으며, 이 값은 manifest의 선택 규칙과 정확히 같아야 한다.
임계값이 0인 단순 집계에서는 manifest 선택 규칙을 자동 사용할 수 있지만, 정밀도ㆍ최소 표본
품질 게이트가 `ok`가 되려면 호출자가 기대 규칙을 명시하고 `--source-input`으로 원본 train
snapshot 해시도 검증해야 한다. 둘 중 하나라도 없으면 출처 미검증 경보가 생긴다. 따라서 큐가
비었거나 0행 규칙이 manifest에서 빠지거나 라벨 외 필드가 바뀌거나 오래된 규칙셋ㆍ다른 원본
큐를 현재 성능으로 오인하는 경로를 차단한다.

2026-08-04 현재 로컬 큐는 22개 선택 규칙 중 17개에서 232행이며 모두 미라벨이다.
후보 매치는 3,039건, 고유 비식별 문맥은 2,626건이고 규칙별 최대 20행을 선택했다.
`AI-001`, `DISABILITY-001`, `FAMILY-001`, `HEALTH-001`, `Q-DIST-014`는 관찰 행이
없어 큐가 비었다. 따라서
`.private-review/summary.json`은 `alert`, precision은 `null`이다. 별도 모델 보조 triage 집계는
규칙 경계 탐색에만 사용했고 사람 라벨이나 성능 증거로 간주하지 않는다.

병역ㆍ대리변수 질문 `Q-DIST-010`은 익명 문맥 재감사에서 기존 163건 중 136건이
지원자 정보 수집이 아니라 근속 복리후생 설명으로 분류됐다. 기존 `재직 기간`ㆍ`근속 기간`
탐지는 유지하되, 후보 자체가 이 두 표현이고 같은 근거리 문맥에 복리후생ㆍ근속 포상 결합이
모두 있을 때만 제외하도록 좁혔다. 민간 train 발동은 27건으로 줄었고 군복무 15건,
취미 5건, 출신학교 4건, 동아리 2건, 거주지역 1건은 남았다. 비교 코퍼스의 기존 발동 수도
보존했다. 전후 집계와 분류 경계는 `reports/private_military_proxy_context_audit.json`에
원문ㆍ식별자 없이 기록했다.

거주ㆍ차량 질문의 익명 재감사는 `Q-DIST-004` 255건과 `Q-DIST-006` 10건을 직무상 운전,
야간 이동, 숙소 지원, 통근 선호, 일괄 자격 문맥으로 배타 분류했다. 보수적 하향 후보는
차량 규칙 7건, 주소 행정 문맥 2건이고 명시적 비거주 허용과 숙소 지원이 함께 있는 보호
후보는 1건이었다. 이는 사람 라벨이나 법적 결론이 아니며 상세 집계는
`reports/private_residence_vehicle_context_audit.json`에 있다.

우대ㆍ가점 절차 질문 `Q-PROC-006` 1,365건도 익명 재감사했다. 일반 채용 우대 1,181건,
법정ㆍ정책 우대 168건, 보호 문구와 다른 우대 신호가 함께 있는 13건, 직원 복리후생 후보
1건, 모호한 참조 2건이었고 순수 상품ㆍ고객 우대는 0건이었다. 우대사항 섹션 1,310건 중
일반 우대 853건은 점수나 적용 단계 단서가 없어 오히려 절차 검토 우선순위가 높았다.
보호 문구 제외는 민간 공고 수를 줄이지 않으면서 비교 코퍼스 발동을 잃었고, 섹션 한정은
실제 채용 우대 후보를 잃어 규칙 변경을 보류했다. 집계와 보류 근거는
`reports/private_preference_process_context_audit.json`에 있다.

성별 우대ㆍ인원 배분 질문 `Q-DIST-015` 50건은 성별 대상 모집 19건, 선호ㆍ우대 9건,
성별 인원 배분 6건, 성별ㆍ연령 결합 15건, 역할 분리 1건으로 배타 분류했다. 이 중 22건은
성별 무관ㆍ모두 지원 같은 포용 문구가 다른 성별 조건과 함께 있어 단순 보호 제외가 아니라
모순 검토 대상이었다. 돌봄ㆍ사생활ㆍ안전 직무 단서 10건 중 수급자 성별과 결합한 사례는
1건이었지만 직업상 필요성 근거가 명시된 사례는 0건이라 자동 제외를 추가하지 않았다.
상세 집계는 `reports/private_gender_context_audit.json`에 있다.

건강ㆍ신체조건 질문 `Q-DIST-007` 28건은 채용 신체검사 17건, 추상적 건강기준 5건,
구체 위험 근거가 없는 일반 작업환경 6건으로 분류됐다. 구체 위험요인ㆍ기능적 수행요건ㆍ
법정검사 근거ㆍ보호구와 편의 안내가 확인된 사례는 0건이었다. 따라서 일반 현장ㆍ생산 문구만
근거로 하향하거나 자동 제외하지 않고, 법정ㆍ특수검사 또는 구체 위험ㆍ기능요건이 함께 있을
때만 사람 검토 우선순위를 하향하는 기준을
`reports/private_health_job_relevance_context_audit.json`에 기록했다.

0행 규칙은 미발견이 아니라 관찰 공백으로 관리한다. 현재 일반 210/90, 청년 210/90,
민간 2,100/900의 train/holdout 익명 발동 수에서 `AI-001`, `DISABILITY-001`,
`FAMILY-001`, `HEALTH-001`은 모두 0건이었다. `Q-DIST-014`는 일반 3/1건,
청년 1/0건, 민간 0/0건으로 민간 출처 공백이다. holdout에서는 건수만 확인했고 문맥은
검토하거나 보고하지 않았다. 다음 수집의 P1은 자동 탈락ㆍ최종결정ㆍ사람 검토 없음 같은
AI 결정 문구, 지원ㆍ면접 단계 병력ㆍ질병ㆍ치료 이력, 장애인 직접 배제 표현이다. P2는
첨부 지원서의 부모ㆍ형제자매 직업ㆍ재산과 어학점수 최소요건이다. 공고 본문뿐 아니라
첨부 지원서, 채용 FAQㆍ절차 페이지, 최종 제출서류를 분리 수집해야 한다. 건강정보 규칙은
자연 관찰이 0인 상태에서 패턴을 넓히지 않고 현재 양성 4종, 지원단계 비수집ㆍ합격 후
검진ㆍ보호구/편의 제공ㆍ병력 미기재 안내 등 음성 5종, 보호 문구 뒤 실제 치료이력 요구를
보존하고 실제 요구 뒤 보호 문구가 와도 앞 후보를 살리는 양방향 혼합 회귀를 먼저 고정했다.
AI 규칙도 자연 관찰 0건을 유지하되 `AI가 최종
결정하지 않음`, `AI 자동 탈락 없음` 같은 사람 최종결정 보호 문맥 4종과, 그 뒤에 별도
자동탈락 요구가 있거나 실제 자동탈락 뒤 보호 문구가 와도 실제 후보를 보존하는 양방향
혼합 회귀를 고정했다.

결과 통지 질문 `Q-INFO-014` 161건도 최신 규칙으로 재검산했다. 합격자 한정 통보가
161건 모두에 있었고, 단순 결과발표 70건, 발표 일정ㆍ예정 71건, 일정 없는 선택적 개별통보
19건, 직접 불합격 무통보 1건으로 배타 분류됐다. 일정ㆍ채널ㆍ불합격자 언급만으로는 실제
통지를 보장하지 않으므로 제외하지 않았다. 동일 절 안에서 불합격자 또는 전 지원자,
결과조회 가능, 구체 채널이 모두 확인되고 합격자 한정 조건이 없을 때만 하향하는 기준의
현재 후보는 0건이며, 집계는 `reports/private_result_notice_context_audit.json`에 있다.

## 규칙 후보 처리 절차

1. 공개ㆍ허용 출처에서 최신 공고를 찾고 URL, 게시일, 접근일만 보존한다.
2. 조직명ㆍ담당자ㆍ연락처를 제거한 뒤 표현을 `positive`, `negative`,
   `ambiguous`로 분류한다.
3. 공식 법령ㆍ정부 매뉴얼ㆍ인권위 결정에서 지지 근거와 반대 근거를 함께
   확인한다.
4. 위반을 직접 뒷받침하고 예외 문맥을 좁힐 수 있을 때만 law 규칙 후보로
   둔다. 그 밖에는 질문 카드 또는 기각ㆍ유보 후보로 둔다.
5. 양성 사례와 보호적 음성 사례를 함께 테스트한다.
6. 공개 train 집계, 웹 번들, Python/JavaScript 패리티와 익명 보고서를
   재생성한다.
7. 사람 라벨이 없는 집계를 정밀도ㆍ재현율 성능 주장에 사용하지 않는다.

## 검증 명령

```powershell
python tools\validate_data.py
python tools\export_web_bundle.py --check
python tools\verify_web_parity.py
python -m pytest
```

민간 감사 전용 회귀는 다음으로 빠르게 확인한다.

```powershell
python -m pytest -q `
  tests\test_private_case_regressions.py `
  tests\test_private_monitoring_snapshot.py `
  tests\test_private_monitoring_runner.py `
  tests\test_private_fairness_audit.py `
  tests\test_private_fairness_drift.py `
  tests\test_private_fairness_cycle.py `
  tests\test_private_review_queue.py `
  tests\test_private_review_ui.py `
  tests\test_summarize_private_review.py
```

`tests/fixtures/private_fairness_cases.json`에는 조사 사례를 회사명ㆍURLㆍ
연락처 없이 사례군 문장으로 일반화한 양성ㆍ보호적 음성 43종이 있다.
실제 공고를 테스트 코드에 복사하지 않고도 성별 제한/우대/포용, 사진,
서류 반환, 정년, 병역, 거주ㆍ자차, 건강, 어학, 증빙, 자동결정의 경계를
계속 회귀 검증한다.

## 현재 관찰과 다음 수집 우선순위

현재 민간 2,100건에서는 성별 제한 관련 법 규칙이 629건에서, 연령 제한
관련 법 규칙이 192건에서 발동했다. 이 값은 한 지역 공개 구인 출처의
관찰 빈도이며 정답률이나 위반률이 아니다. 이의제기 경로, 평가 기준,
자격요건의 직무 관련성, 결과 안내와 전형 단계는 90% 이상에서 확인되지
않아 정보 구조가 단순한 출처의 특성이 강하게 반영됐다.

이번 확장 규칙을 같은 train에 적용하면 사진 부착ㆍ첨부 `PHOTO-001`이
4건, 서류 반환 제한 `RETURN-001`이 68건에서 관찰됐다. 성별 우대ㆍ선호
검토 질문 `Q-DIST-015`는 성별 우대뿐 아니라 명시적 성별 모집ㆍ인원 배분ㆍ연령 결합ㆍ
업무 분리를 포함해 50건, 반환 범위
검토 질문 `Q-INFO-013`은 69건에서 발동했다. 69건 모두 `반환하지 않으며`
변형이었고, 68건은 이메일ㆍ방문 혼합 접수, 1건은 이메일 단독 접수였다.
이메일 단독 1건은 법 finding에서 보호하되 반환ㆍ파기 범위 질문에는 남겼다.
합격자 한정 통보 검토 질문
`Q-INFO-014`는 161건에서, 기존 건강ㆍ신체조건 질문 `Q-DIST-007`은
28건에서 발동했다. 후자의 보호장구 확장 표현은 현재 이 단일 출처에서는
관찰되지 않았다. 장애인 관련 표현은 195건, 장애ㆍ보훈 우대 문맥은 75건이었지만
직접 지원 배제 변형은 0건이다. `DISABILITY-001`은 0행 규칙도 사라짐으로 감추지 못하도록
현재 기본 review queue의 기대 규칙에 포함했다.
종교 관련 단독 키워드는 148건이었지만 보호 문맥 제외 뒤 남은 9건도 명시적
신앙 자격이 아니었다. 이에 `Q-DIST-012`는 단독 키워드 발동을 제거하고
지원자격ㆍ우대ㆍ종교관 질문에 직접 연결된 표현만 검토하도록 좁혔으며 현재
민간 train에서는 명시적 신앙 자격 문맥 1건만 남았다. 상세 경계는
`reports/private_religion_context_audit.json`에 있다. 혼인 여부 표현 53건 중
51건은 블라인드 미기재 안내로 보호했고, 남은 2건만 `Q-DIST-017` 검토
대상으로 올렸다. 상세 경계는 `reports/private_marital_context_audit.json`에
있다. 가족 학력ㆍ직업ㆍ재산 finding `FAMILY-001`은 0건이고,
가족관계증명서ㆍ주민등록초본ㆍ기본증명서 등의 요구 시점ㆍ목적을 묻는 `Q-INFO-011`은
162건이다. 기존 질문이 놓치던 기본증명서 4건이 추가 검토 대상으로 늘었다. `형제자매의
학력` 변형 52건은 모두 기재 금지 또는 미수집 보호 안내였고, 보호 제외를 함께 적용해
`SCHOOL-001`은 0건을 유지했다. 이 값들은 실제 민간 표현의 검토 물량을 보여줄 뿐이며,
상세 제한은 `reports/private_family_evidence_context_audit.json`에 있다.
사람 정답 검토 전에는 참양성이나 위반률로 간주하지 않는다.

AGE-002의 수정 전 193건을 비식별 군집화하면 명시적 상ㆍ하한 118건, 완곡한
우대ㆍ선호 65건, 고령자 문맥 5건, 프로그램 문맥 1건, 제한 없음 1건, 기타 3건이었다.
제한 없음 활용형 1건만 추가 보호해 현재 192건이 됐고, 이중부정 제한과 일반 연령 하한은
계속 발동한다. 이 모델 보조 군집은 사람 정답이 아니며 상세 익명 집계는
`reports/private_age_context_audit.json`에 있다.

`Q-INFO-014` 발동 161건은 합격자만 개별 통보 157건, 서류 미비 미통보 1건, 기타 선택적 통보
3건으로 익명 군집화됐다. 전체 지원자 통보나 결과 조회처럼 자동 제외할 만큼 명확한 보호
문맥은 없었다. 따라서 규칙은 그대로 유지하고 사람 검토 대상으로 남겼다. 이 모델 보조 집계는
성능 측정이 아니며 상세 제한은 `reports/private_result_notice_context_audit.json`에 있다.

성별 구조화 필드는 `SEX-001` 제한 629건과 성별무관 1,475건으로 전체 2,100건을 덮었고,
4건은 두 값이 한 공고 안에서 충돌했다. 충돌 공고는 자동 제외하지 않는다. 기존 성별 우대
검토 질문 8건에 명시적 성별 모집ㆍ인원 배분ㆍ연령 결합ㆍ업무 분리를 추가해
`Q-DIST-015`는 50건이 됐다. 시설 이용자 성별 인원 4건은 지원자 조건이 아니어서 보호했다.
이 값은 위반률이 아니며 상세 익명 집계는 `reports/private_gender_context_audit.json`에 있다.

국적ㆍ외국인 관련 표현은 34건이었지만 대부분 외국인 지원 가능ㆍ우대 또는 체류ㆍ취업자격
문맥이었다. 직접 배제 표현 1건만 `Q-DIST-016` 사람 검토 대상으로 남겼다. 이 질문은 국적
자체와 국내에서 합법적으로 근무할 수 있는 체류자격, 언어ㆍ운전ㆍ보안 등 직무상 직접 요건을
구분하며, 표현만으로 차별이나 법 위반을 확정하지 않는다.

범죄경력ㆍ전과ㆍ신원조회 표현은 16건이 관찰됐고 보호 문맥 제외 후 `Q-DIST-013`이 15건에서
발동했다. 국가인권위원회의 2025년 실효된 범죄경력 채용 차별 결정에 맞춰 근거를 보강했으며,
직무 관련성ㆍ조회 권한ㆍ경과 기간ㆍ소명 절차를 사람에게 묻는 기존 review-only 경계를 유지했다.
15건의 익명 재감사에서는 일률적 부재ㆍ결격 6건을 우선 검토, 일반 조회 4건을 보통 검토,
법정 성범죄ㆍ아동보호 조회 5건을 하향 검토 후보로 분류했다. 명시적 직무 관련성이나
목적 제한이 함께 적힌 사례는 0건이어서 자동 제외는 하지 않는다. 배타 군집과 중복 문맥
검산은 `reports/private_criminal_record_context_audit.json`에 원문 없이 기록했다.

다음 수집은 기존 출처 건수 확대보다 출처 다양화에 우선순위를 둔다.

- 기업 자체 채용 페이지의 사무ㆍ기술ㆍ연구 직군
- 고용24 공개 구인정보의 서비스ㆍ현장 직군
- 대기업ㆍ중견ㆍ중소기업과 정규ㆍ기간제의 균형
- 이메일ㆍ온라인ㆍ방문 접수 방식별 서류 반환 문맥
- 성별 우대ㆍ선호ㆍ포용, 병역 유예, 사진 요구, 거주ㆍ자차 조건의 보호적
  음성 사례

고용24 Open API는 2026-08-03 실제 호출에서 현재 키가 개인회원으로
식별되어 자동수집이 거부됐다. 이 제한은 공개 웹 사례 조사와 기존 공개
코퍼스 분석으로 우회하며, 권한이 바뀌기 전에는 반복 실행을 해당 API에
의존시키지 않는다.

## 사람 검토가 필요한 항목

- 전자접수와 종이 원본이 섞인 공고에서 반환 대상과 예외가 구분되는가?
- 공고 본문과 플랫폼 표준 반환ㆍ파기 안내가 서로 모순되지 않는가?
- 성별 우대ㆍ선호의 직무상 또는 적극적 개선조치 근거가 문서화되는가?
- 사진ㆍ병역ㆍ주민등록 증빙은 적절한 단계에서 최소한으로 요구되는가?
- 거주ㆍ자차ㆍ신체검사 조건에 대체수단과 합리적 편의가 있는가?
- 새 출처가 기존 단일 지역ㆍ현장직 편중을 실제로 줄이는가?
