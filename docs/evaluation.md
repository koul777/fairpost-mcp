# 홀드아웃 성능 평가

성능 목표는 사람이 봉인 홀드아웃 전체를 직접 검토한 뒤에만 판정합니다.
규칙 개발 중에는 `.corpus-prd/holdout/` 원문과 라벨을 열람하지 않습니다.

## 로컬 라벨링 화면

사람 검수자는 다음 명령으로 네트워크 요청이 차단된 단일 HTML을 생성합니다.

```powershell
python tools\build_annotation_ui.py
```

생성된 `.corpus-prd/holdout/labeler.html`을 브라우저에서 열어 공고별로 다음을
검토합니다.

- 실제 법령 관련 표현과 동일 규칙 표현의 개수
- 실제로 확인되지 않은 11개 절차ㆍ정보 슬롯
- 현재 사전에 연결할 규칙 ID가 없는 문제 표현의 개수
- 공고 전체 검토 완료 여부

`검토 완료분 내보내기`로 받은 `annotations.jsonl`은 부분 진행 상태로도
보관할 수 있습니다. 다시 화면에서 `라벨 불러오기`를 선택하면 이어서
검토할 수 있습니다. 화면은 서버에 공고문이나 라벨을 전송하지 않으며
브라우저 저장소에도 자동 저장하지 않습니다.

PRD의 정식 평가 세트는 공공 300건과 민간 300건을 70/30으로 고정
분할한 600건입니다. 이 중 봉인 홀드아웃은 공공 90건과 민간 90건,
총 180건입니다. 기본 명령으로
`.corpus-prd/holdout/labeler.html`을 생성해 두었습니다.

```powershell
python tools\build_annotation_ui.py `
  --input .corpus-prd\holdout\records.jsonl `
  --manifest .corpus-prd\holdout\manifest.json `
  --output .corpus-prd\holdout\labeler.html
```

공공 300건과 민간 3,000건을 결합한 `.corpus-final`의 990건 홀드아웃은
추가 스트레스 평가에 사용할 수 있지만 G1ㆍG2의 필수 평가 모집단을
대체하지 않습니다.

## 라벨 형식

라벨 파일은 로컬 JSONL이며 저장소에 커밋하지 않습니다.

```json
{
  "id": "job-alio:123",
  "content_hash": "본문 SHA-256",
  "text": "비식별화된 홀드아웃 공고문",
  "expected_findings": ["AGE-001"],
  "expected_absent_slots": ["appeal_channel"],
  "expected_expressions": [
    {"rule_id": "AGE-001"}
  ]
}
```

`expected_expressions.rule_id`는 해당 문제 표현을 현재 사전이 다루면 규칙
ID, 아직 다루지 못하면 `null`로 기록합니다. 이 값으로 사전 커버리지를
계산합니다. 표현 문구 자체는 평가 보고서에 기록하지 않습니다.
`expected_findings`는 `expected_expressions`에서 `null`이 아닌 규칙 ID의
집합과 정확히 일치해야 합니다. 평가기는 두 필드가 다르면 라벨 오류로
중단합니다.

성능 지표의 측정 단위는 다음과 같습니다.

- 표현 탐지: 공고와 법령 규칙 ID의 쌍
- 부재 탐지: 공고와 11개 슬롯 ID의 쌍
- 사전 커버리지: 사람이 표시한 개별 문제 표현

동일 규칙에 해당하는 표현이 한 공고에 여러 번 있어도 fairpost 출력은
규칙별 Finding 하나이므로 정밀도ㆍ재현율은 공고-규칙 쌍으로 측정합니다.
개별 표현 수는 사전 커버리지의 분모로 별도 보존합니다.

## 실행

```powershell
python tools\evaluate.py .corpus-prd\holdout\annotations.jsonl `
  --train-manifest .corpus-prd\train\manifest.json `
  --holdout-manifest .corpus-prd\holdout\manifest.json `
  --holdout-records .corpus-prd\holdout\records.jsonl `
  --enforce-targets `
  --output reports\evaluation.json
```

`--holdout-records`를 생략하면 홀드아웃 manifest와 같은 폴더의
`records.jsonl`을 사용합니다.

목표 강제 실행은 다음을 모두 확인합니다.

- 라벨 본문의 SHA-256이 `content_hash`와 일치
- 모든 라벨이 홀드아웃 manifest에 포함
- 홀드아웃 manifest와 `records.jsonl`의 IDㆍ본문 해시가 정확히 일치
- 홀드아웃 전체가 정확히 한 번씩 라벨링됨
- 공공과 민간 홀드아웃이 각각 90건 이상
- 규칙 ID와 슬롯 ID가 현재 사전에 실재
- 학습/홀드아웃 해시 중복이 없음
- 표현 탐지 정밀도 0.90 이상
- 부재 탐지 재현율 0.85 이상, 정밀도 0.80 이상

해당 양성 사례가 하나도 없어 분모가 0인 정밀도ㆍ재현율은 `1.0`으로
간주하지 않고 `null`로 보고합니다. 이 경우 목표 게이트는 통과하지
않습니다. 전체 결과와 함께 공공ㆍ민간별 지표와 출처별 홀드아웃 건수도
보고서에 기록합니다.

개발 중 일부 라벨의 진단만 필요하면 `--allow-partial`을 사용할 수 있지만,
이 결과는 목표 달성 근거로 사용할 수 없습니다.
