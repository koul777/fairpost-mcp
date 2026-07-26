# 코퍼스 구축 기록

## 현재 스냅샷

- 수집일: 2026-07-26
- 공공: 300건
- 공공 출처: 잡알리오 100건, 클린아이 100건, 나라일터 100건
- 청년 채용 공고: 공식 사이트 구조화 목록 300건
- 청년 채용 분할: 학습 210건, 봉인 홀드아웃 90건
- 민간: 진천군 공개 구인정보 3,000건
- 민간 분할: 학습 2,100건, 봉인 홀드아웃 900건
- PRD 정식 평가 코퍼스: 공공 300건 + 민간 300건, 학습 420건,
  봉인 홀드아웃 180건
- 확장 코퍼스: 공공 300건 + 민간 3,000건, 학습 2,310건,
  봉인 홀드아웃 990건
- 원문 위치: 로컬 `.corpus*/` (Git 제외)
- 공개 집계: `reports/corpus_summary.json`
- 학습 세트 규칙 집계: `reports/corpus_rule_coverage.json`
- 청년 채용 공개 집계: `reports/youth_job_corpus_summary.json`
- 청년 채용 규칙 집계: `reports/youth_job_rule_coverage.json`
- 민간 공개 집계: `reports/private_open_corpus_summary.json`
- 민간 학습 규칙 집계: `reports/private_open_training_analysis.json`
- 최종 결합 집계: `reports/final_corpus_summary.json`
- PRD 정식 코퍼스 집계: `reports/prd_corpus_summary.json`

직군 값은 PRD에 명시된 `office`(사무), `tech`(기술),
`research`(연구), `field`(현장) 네 개만 사용합니다. 수집 문구에서
직군 단서를 찾지 못하면 공공은 사무, 민간은 현장으로 귀결하며
`other` 값은 생성하지 않습니다. 기존 고정 코퍼스를 재분류할 때는
`tools/reclassify_corpus.py`가 IDㆍ원문 해시ㆍ학습/홀드아웃 구성원을
보존하고 메타데이터만 변경합니다.

규칙 집계 보고서에는 전체 `ruleset_version`과 별도로
`matching_version`을 기록합니다. 조문 원문ㆍ설명 문구만 바뀌면 표현
매칭 빈도는 달라지지 않으므로 보고서를 다시 만들 필요가 없습니다.
트리거ㆍ제외 범위ㆍ슬롯ㆍ정규화ㆍ섹션 알고리즘이 바뀔 때만
`matching_version`이 바뀌고 검증기가 재집계를 요구합니다.

민간 코퍼스는 공공데이터포털에 공개된 진천군 일자리 구인정보 CSV에서
최신 접수일 우선으로 수집했습니다. 원천 파일은 38,700행이며 회사명,
채용제목, 고용형태, 급여, 학력, 경력, 성별, 우대조건, 복리후생,
근무시간과 직무내용을 제공합니다. 회사명ㆍ연락처ㆍ이메일은 저장 전에
비식별화하고 주소 열은 학습 텍스트에서 제외합니다.

고용24 수집기도 유지합니다. 현재 제공된 키는 채용정보 엔드포인트에서
`개인회원은 사용할 수 없는 OPEN-API입니다.`를 반환하므로 고용24 자료는
현재 코퍼스에 포함하지 않았습니다. 한국노인인력개발원 노인 구인정보
상세 API 수집기도 구현했지만 해당 서비스의 별도 활용신청 전에는 401을
반환합니다.

```powershell
python tools\collect_corpus.py `
  --source jincheon-jobs `
  --limit-per-source 3000 `
  --exclude-manifest .corpus\train\manifest.json `
  --exclude-manifest .corpus\holdout\manifest.json `
  --output-dir .corpus-private-open `
  --summary reports\private_open_corpus_summary.json

python tools\combine_corpora.py `
  --public-dir .corpus `
  --private-dir .corpus-private-open `
  --output-dir .corpus-final `
  --summary reports\final_corpus_summary.json `
  --expected-public 300 `
  --expected-private 3000

python tools\build_prd_corpus.py
```

결합기는 두 코퍼스의 기존 학습ㆍ홀드아웃 배정을 보존하면서 전체 원문
해시 중복, ID 중복, sector와 70/30 분할을 다시 검증합니다.
`build_prd_corpus.py`는 고정된 공공 분할 210/90을 모두 보존하고, 고정된
민간 분할 안에서 각각 210/90건을 직군×고용형태 비율로 선택합니다.
선택 순서는 ID의 SHA-256으로 결정하며 공고 본문은 선택에 사용하지
않습니다. 따라서 봉인 홀드아웃 원문을 열거나 구성원을 학습 세트로
옮기지 않고 PRD의 정확한 420/180 평가 모집단을 재현합니다. 확장
3,300건 코퍼스는 삭제하지 않고 추가 분석용으로 유지합니다.

청년 일자리 키는 로컬에서 정상 로딩되지만, 공공데이터포털 기본 URL에
실호출하면 `HTTP 401 Unauthorized`가 반환됩니다. 알리오에서 발급된
키의 승인 Swagger URL을 `YOUTH_JOB_SERVICE_URL`에 넣기 전까지 키 기반
연결 완료를 주장하지 않습니다. 대신 공식 사이트의 공개 구조화 조회
경로로 청년 채용 공고 300건을 별도 수집했습니다.

## 격리 원칙

수집 직후 직군·고용형태 층별로 70/30을 결정론적으로 분할합니다. 학습과
홀드아웃 manifest의 원문 해시는 겹치지 않습니다. 후보 생성기와 학습
코퍼스 분석기는 경로에 `holdout`이 포함되면 실행을 거부합니다.

홀드아웃 성능 수치는 사람이 정답을 라벨링한 뒤에만 생성합니다. 현재
홀드아웃은 봉인 상태이므로 정밀도·재현율 목표를 달성했다고 주장하지
않습니다.

## 개인정보와 재배포

수집 시 담당자 이메일, 전화번호, 성명 표기와 기관·기업명을
비식별화합니다. 저장소에는 공고 원문, 공고별 결과, 기관별 순위를
포함하지 않으며 익명 집계만 공개합니다.
