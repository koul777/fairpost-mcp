# FairPost 엔진 성능 벤치마크

`tools/benchmark_engine.py`는 FairPost Python 엔진의 개발 환경 기준 처리 성능을
재현 가능한 방식으로 관찰하는 도구다. 결과는 운영 환경의 SLA나 용량 보장이
아니며, 규칙 또는 매칭 구현이 바뀌었을 때 같은 조건의 추세를 비교하기 위한
근거 자료다.

## 실행

기본값은 `.corpus-prd/train/records.jsonl` 전체를 한 번 예열하고 세 번
측정한다.

```powershell
python tools/benchmark_engine.py
```

입력과 반복 조건은 명시적으로 조정할 수 있다.

```powershell
python tools/benchmark_engine.py `
  --input .corpus-final/train/records.jsonl `
  --warmup 1 `
  --repeats 5 `
  --max-records 500 `
  --output reports/engine_performance.json
```

- `--warmup`: 측정 전에 전체 선택 레코드를 실행하는 횟수이며 기본값은 1이다.
- `--repeats`: 선택 레코드를 측정하는 횟수이며 기본값은 3이다.
- `--max-records`: 입력 순서상 앞에서부터 측정할 최대 레코드 수다. 생략하면
  전체 입력을 사용한다.

## 데이터 경계와 개인정보 보호

도구는 경로에 정확한 `train` 구성 요소가 있는 JSONL 파일만 허용한다.
`holdout`, `test`, `dev`, `evaluation` 계열 경로는 실행 전에 거부하므로 봉인
평가 자료가 성능 실험에 섞이지 않는다.

보고서에는 공고 원문, 레코드 ID, 기관명이나 기관별 결과, 개별 레코드 타이밍을
기록하지 않는다. `input.sha256`은 실제 선택된 원문을 입력 순서와 UTF-8 바이트
길이까지 포함해 묶은 SHA-256이다. 따라서 입력 동일성을 검증할 수 있지만 보고서로
원문이나 식별자를 복원할 수는 없다.

## 측정 방법

엔진과 규칙 로딩은 측정에서 제외한다. 각 `engine.check()` 호출의 직전과 직후를
단조 증가 시계인 `time.perf_counter()`로 측정한다. `elapsed_seconds`는 모든
개별 호출 시간의 합이고, `postings_per_second`는 측정 호출 수를 이 합으로 나눈
값이다. 지연 시간은 밀리초 단위의 최솟값, 평균, p50, p95, p99, 최댓값으로
집계하며 백분위는 nearest-rank 방식을 사용한다. 집계 순서와 반올림 규칙은
고정돼 있지만 운영체제 부하, CPU 전원 상태, Python 버전에 따라 실측값은 달라질
수 있다.

보고서는 현재 `ruleset_version`, `matching_version`, Python 구현과 버전, 운영체제,
CPU 아키텍처, 입력 해시와 건수, 예열·반복 설정을 함께 남긴다. 서로 다른 보고서를
비교할 때는 이 조건들이 같은지 먼저 확인해야 한다.

## 결과 해석

`reports/engine_performance.json`은 단일 개발 장비에서 얻은 기준선이다. 다음과 같은
주장은 이 보고서만으로 할 수 없다.

- 동시 사용자 수 또는 서버 처리 용량
- 네트워크, MCP 전송, 웹 렌더링을 포함한 종단 간 지연 시간
- 특정 하드웨어에서 보장되는 최대 지연 시간
- 운영 SLA 또는 규제 준수 성능

운영 용량을 판단하려면 대상 배포 환경에서 동시성, 장시간 부하, 메모리, 오류율을
별도로 측정해야 한다.
