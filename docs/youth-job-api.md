# 청년 일자리 올인원 지원 서비스

## 연결 위치

청년 채용정보는 사용자 공고 점검 런타임에 연결하지 않습니다. 공식
공공기관 채용공고를 사전에 수집해 규칙과 정보 슬롯의 관찰 빈도를
익명 집계하는 빌드 도구에서만 사용합니다.

루트 `.env`에 발급받은 인증키와 승인 화면의 채용 목록 URL을 둡니다.

```dotenv
YOUTH_JOB_SERVICE_AUTH_KEY=발급받은_인증키
YOUTH_JOB_SERVICE_URL=승인된_Swagger의_채용목록_전체_URL
```

`YOUTH_JOB_SERVICE_URL`을 비워 두면 공공데이터포털의 재정경제부
공공기관 채용정보 조회서비스 URL
`https://apis.data.go.kr/1051000/recruitment/list`를 사용합니다.

```powershell
python tools\collect_corpus.py `
  --source youth-job `
  --limit-per-source 300 `
  --output-dir .corpus-youth-api `
  --summary reports\youth_job_api_corpus_summary.json
```

## 인증키 구분

`opendata.alio.go.kr`에서 직접 발급한 키와 `data.go.kr`에서 활용신청한
서비스키는 발급 주체와 호출 주소가 다를 수 있습니다. 한 사이트의 키를
다른 사이트의 기본 URL에 넣어 받은 `401`은 키 문자열 오류를 의미하지
않습니다. 신청현황의 Swagger에 표시된 Base URL과 목록 경로를 합친
전체 URL을 `YOUTH_JOB_SERVICE_URL`에 넣어야 합니다.

키는 URL 디코딩 후 `serviceKey` 요청 매개변수로만 전송합니다. 오류
메시지, 보고서, 코퍼스에는 인증키를 기록하지 않습니다.

2026-07-26 실호출에서는 현재 `.env`의 키를 공공데이터포털 기본 URL에
보냈을 때 `HTTP 401 Unauthorized`가 반환됐습니다. 키는 로컬에서 정상
로딩됐지만 발급받은 알리오 서비스의 Swagger URL이 공공데이터포털
URL과 일치하지 않으므로, 이 결과만으로 키가 잘못됐다고 판단하지
않습니다. 현재 키 기반 연결은 완료로 표시하지 않습니다.

## 공식 공개 조회 경로

키 기반 URL을 확인하기 전에도 공식 사이트가 검색 화면에 제공하는
구조화 목록을 빌드 타임에 수집할 수 있습니다.

```powershell
python tools\collect_corpus.py `
  --source youth-job-site `
  --limit-per-source 300 `
  --output-dir .corpus-youth `
  --summary reports\youth_job_corpus_summary.json
```

수집 대상은 청년인턴 코드 `R1060`과 채용연계형 청년인턴 코드
`R1070`으로 균형 배분합니다. 담당자 이메일·전화번호·성명 표기와
기관명은 저장 전에 비식별화하며, 70% 학습 세트와 30% 봉인
홀드아웃으로 즉시 분할합니다.

2026-07-26 공식 공개 조회 경로로 300건을 수집했고, 학습 210건과
홀드아웃 90건으로 분할했습니다. 익명 집계는
`reports/youth_job_corpus_summary.json`과
`reports/youth_job_rule_coverage.json`에 기록합니다.
