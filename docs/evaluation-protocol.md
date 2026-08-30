# FairPost 평가 무결성 프로토콜

이 문서는 규칙 개발용 **train calibration**과 출시 판단용 **sealed holdout final**을 분리한다. 목표는 AI나 규칙 개발자가 예측 결과를 정답처럼 복제하거나, 같은 홀드아웃 결과를 본 뒤 튜닝해 다시 성능을 주장하는 일을 예방하고 감사 흔적을 남기는 것이다.

## 1. Train calibration

train 표본의 사람 라벨만 사용해 라벨 지침, 보호 문맥, 표현 규칙과 슬롯 추출기를 조정한다. 일부 라벨만 있어도 실행할 수 있으며 이 결과는 G1/G2 출시 성능으로 주장할 수 없다.

```powershell
python tools\evaluate.py .corpus-prd\train\annotations.jsonl `
  --phase calibration `
  --train-manifest .corpus-prd\train\manifest.json `
  --calibration-records .corpus-prd\train\records.jsonl `
  --holdout-manifest .corpus-prd\holdout\manifest.json `
  --output reports\calibration-evaluation.json
```

calibration 보고서의 `target_gate.applicable`은 `false`이다. `--enforce-targets`는 이 단계에서 거부된다. 봉인 홀드아웃 원문·라벨·예측 결과는 calibration에 사용하지 않는다.

## 2. 규칙 동결과 라벨링 인계

calibration을 마치면 규칙과 매칭 구현을 동결한다. 그 다음 현재 버전으로 오프라인 라벨링 화면과 인계 보고서를 순서대로 다시 만든다.

```powershell
python tools\build_annotation_ui.py
python tools\build_human_labeling_handoff.py
```

인계 빌더는 라벨링 HTML 안의 다음 값을 현재 입력과 자동 비교한다.

- `ruleset_version`과 `matching_version`
- holdout ID와 본문 SHA-256 목록
- 평가 단계 `sealed_holdout_final`
- G1/G2 측정 범위

하나라도 다르거나 과거 HTML에 결합 정보가 없으면 stale 산출물로 거부한다. 규칙 또는 매칭 버전이 바뀌면 기존 라벨링 화면과 인계 보고서를 재사용하지 않는다.

## 3. 사람 gold 확인

최종 라벨은 FairPost 예측을 보지 않은 사람이 작성해야 한다. AI는 형식 검사, 라벨링 화면 생성, 파일 해시 계산을 도울 수 있지만 `expected_findings`, `expected_absent_slots`, `expected_expressions`를 생성하거나 사람 라벨로 대리 확인하면 안 된다. 권장 절차는 전체 1인 검토, 30% 독립 이중 검토, 불일치 조정 기록이다.

라벨 동결 후 별도 `human-attestation.json`을 만든다. 리뷰어는 실명 대신 조직 내에서 추적 가능한 가명 ID를 쓸 수 있다.

```json
{
  "schema_version": 2,
  "attestation": "human_gold",
  "prediction_blinded": true,
  "ai_generated_labels": false,
  "reviewer_ids": ["hr-reviewer-01"],
  "annotations_sha256": "완성된 annotations.jsonl의 SHA-256",
  "holdout_manifest_sha256": "holdout manifest.json의 SHA-256",
  "holdout_records_sha256": "holdout records.jsonl의 SHA-256",
  "ruleset_version": "인계 보고서와 같은 규칙 버전",
  "matching_version": "인계 보고서와 같은 매칭 버전",
  "attested_at": "2026-08-30T18:00:00+09:00"
}
```

PowerShell에서는 `Get-FileHash -Algorithm SHA256 <파일>`로 해시를 계산할 수 있다. 평가기는 확인서의 세 파일 해시와 두 버전, 사람 라벨 선언, 블라인드 여부를 검증한다. `records.jsonl` 해시는 공공·민간 구분을 포함한 최종 게이트 메타데이터까지 사람 확인서에 묶는다. 확인서는 사람 참여의 암호학적 증명이 아니라 허위·실수에 책임 주체와 감사 가능한 명시적 선언을 요구하는 운영 통제다.

## 4. Sealed holdout final

다음 명령은 기존 CLI 호환성을 위해 `--phase final`이 기본값이지만, 최종 실행에서는 단계를 명시한다.

```powershell
python tools\evaluate.py .corpus-prd\holdout\annotations.jsonl `
  --phase final `
  --train-manifest .corpus-prd\train\manifest.json `
  --holdout-manifest .corpus-prd\holdout\manifest.json `
  --holdout-records .corpus-prd\holdout\records.jsonl `
  --human-attestation .corpus-prd\holdout\human-attestation.json `
  --enforce-targets `
  --output reports\evaluation.json
```

final 실행은 예측 계산 전에 holdout·라벨·확인서·규칙·매칭 버전을
`final-evaluation-receipt.json`의 `pending` 상태로 예약한다. 보고서를
원자적으로 저장한 뒤에만 영수증을 `finalized`로 전환한다. 계산이나 저장이
늦게 실패하면 완료 영수증으로 위장하지 않고 `pending`이 남으며, 정확히 같은
결합 입력으로만 복구할 수 있다. 이후에는 모든 값이 동일한 재현 실행만
허용한다. 다음 중 하나가 바뀌면 같은 영수증으로 실행할 수 없다.

- holdout manifest 또는 records
- 사람 라벨 파일 또는 확인서
- 규칙 버전 또는 매칭 버전
- 평가기와 실제 호출되는 `core` 엔진 파일들의 Python AST, Python/PyYAML 실행환경 지문
- 전체/부분 라벨 상태

평가 입력은 실행 시작 시 한 번만 바이트로 캡처하고, 파싱·해시·확인서·영수증에 같은 바이트를 사용한다. 평가 중 평가기/엔진 소스 지문이 바뀌면 보고서를 쓰지 않고 실패한다. 보고서 안의 영수증 상태는 실행 횟수에 따라 달라지지 않는 `bound`로 기록한다.
재현 실행은 새 보고서를 쓰기 전에 기존 `finalized` 영수증의 보고서 SHA-256을
먼저 대조하므로, 다른 결과가 기존 보고서를 덮어쓴 뒤 실패하는 순서를 허용하지
않는다. 실제 영수증의 `pending`ㆍ`finalized` 상태와 최초 등록 시각은 별도
영수증 파일에서 확인한다.

`--allow-partial`로 final 결과 일부를 본 경우에도 홀드아웃은 공개된 것으로 등록되며 출시 성능 주장 자격을 잃는다. 목표 미달 결과를 본 뒤 규칙을 수정했다면 같은 180건으로 다시 G1/G2 통과를 주장하지 않고 새 독립 홀드아웃을 구성한다. 영수증 삭제나 수동 변조를 기술적으로 완전히 막을 수는 없으므로 영수증과 평가 보고서를 Git 이력 또는 변경 불가능한 감사 저장소에 즉시 보존하고 코드 리뷰에서 삭제를 금지한다.

## 5. G1/G2 범위와 질문 카드

G1/G2가 측정하는 대상은 다음뿐이다.

- 법령 관련 표현 탐지: 공고-법령 규칙 ID 쌍의 정밀도
- 절차 정보 부재 탐지: 공고-슬롯 ID 쌍의 재현율과 정밀도
- 사전 커버리지: 사람이 표시한 문제 표현 중 현재 법령 규칙 ID로 연결된 비율(보조 지표)

현재 52개 질문 카드는 G1/G2 대상이 아니다. 질문 카드는 실제 채용담당자 파일럿에서 관련성, 이해도, 실행 가능성, 무관 판정 비율을 별도로 측정한다. 질문 카드 수나 자동 테스트 통과를 법령 표현·부재 탐지 성능의 근거로 사용하지 않는다.

## 6. 출시 판정

최종 보고서는 다음 조건을 모두 만족할 때만 `target_gate.passed: true`가 된다.

- 공공 90건과 민간 90건 전체에 사람 라벨 존재
- train/holdout 본문 해시 중복 0건
- 현재 입력과 결합된 사람 gold 확인서 검증
- 표현 정밀도 0.90 이상
- 부재 탐지 재현율 0.85 이상, 정밀도 0.80 이상
- 최초 공개 영수증상 출시 주장 가능 상태

어떤 지표의 분모가 0이면 `null`이며 통과로 간주하지 않는다.
