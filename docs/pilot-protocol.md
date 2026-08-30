# FairPost 채용담당자 파일럿 프로토콜

이 파일럿은 질문 카드가 채용공고 게시 전 검토에 실제로 도움이 되는지
확인한다. 법령 표현 정밀도나 부재 탐지 재현율을 측정하는 봉인 홀드아웃
평가와 목적이 다르다. 질문의 발동률이나 파일럿 만족도를 G1ㆍG2 성능으로
보고하지 않는다.

## 범위와 개인정보 경계

- 3개 이상 독립 채용팀이 실제 초안 20건 이상을 정적 웹 또는 로컬 CLI로
  검토한다.
- 공고 원문은 담당자의 브라우저 또는 컴퓨터 밖으로 보내지 않는다.
- 입력 JSONL에는 가명 `case_id`, `team_id`, 정량값과 열거형 응답만 둔다.
- 조직명, URL, 사람 이름, 연락처, 공고 원문과 자유서술은 수집하지 않는다.
- 보고서는 팀ㆍ사례 식별자를 제거한 집계만 보존한다.

## 한 사례의 기록

`examples/pilot_feedback.example.jsonl`의 각 행은 다음을 기록한다.

- 검토에 걸린 분
- 표시된 질문별 `actionable`, `relevant_no_action`, `irrelevant`
- 최종 결과 `edited`, `confirmed`, `escalated`, `no_action`
- 면책문 이해 여부
- 로컬 전용 처리 확인
- 실제 사용한 규칙셋ㆍ매칭 버전

질문이 표시되지 않은 사례도 `question_feedback: []`로 기록할 수 있다.
도구는 현재 사전에 없는 질문 ID와 현재 버전이 아닌 행을 거부한다.

## 초기 통과 기준

```powershell
python tools\summarize_pilot_feedback.py `
  --input .private-review\pilot-feedback.jsonl `
  --output reports\pilot_summary.json `
  --min-cases 20 `
  --min-teams 3 `
  --max-median-minutes 10 `
  --min-actionable-case-rate 0.70 `
  --max-irrelevant-question-rate 0.20
```

통과 기준은 중앙 검토시간 10분 이하, 실행 가능한 사례 70% 이상, 무관
질문 20% 이하, 면책문 이해와 전 사례 로컬 처리다. `alert`는 실패를
숨기지 않고 종료 코드 `2`를 반환한다. 파일럿 시작 전에 조직의 실제
업무량과 검토 단계에 맞춰 임계값을 승인하고 변경 이력을 남긴다.

## 해석

- `actionable`은 해당 질문이 공고 수정, 내부 확인 또는 상위 검토로
  이어졌음을 뜻한다.
- `relevant_no_action`은 질문은 적절했지만 기존 절차가 이미 충족됐음을
  뜻한다.
- `irrelevant`는 현재 공고 또는 채용 절차와 관계가 없음을 뜻한다.
- 파일럿 결과가 통과해도 공정성, 적법성 또는 시장 전체 일반화를
  증명하지 않는다.
- `actionable_case_rate`는 하나 이상의 질문을 `actionable`로 평가한
  사례만 센다. 질문이 표시되지 않은 채 다른 이유로 수정한 사례는
  `non_no_action_outcome_rate`에는 포함되지만 질문 유용성에는 포함되지 않는다.
