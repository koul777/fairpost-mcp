# 법령 스냅샷 유지관리

## 런타임 원칙

FairPost는 실행 중 법령 API를 호출하지 않는다. 검수된 현행 조문을
`data/statutes/`에 저장하고 규칙 사전 버전에 포함한다. 사용자는 같은
사전 버전에서 항상 같은 결과를 받는다.

각 스냅샷은 다음 정보를 가진다.

- 국가법령정보센터의 법령 ID와 출처
- 조문 제목과 원문
- 조문 시행일
- 수집 기준일
- 원문 SHA-256

로더는 조문 해시, 시행일과 수집일의 선후 관계, 법령 규칙의
`statute_id`ㆍ`article`ㆍ`snapshot_date` 일치를 검사한다.

## 정기 비교

`.github/workflows/statute-snapshot-audit.yml`은 매일 공식
국가법령정보센터 Open API와 여섯 법령을 비교한다. PRD의 월 1회 기준을
최소선으로 보고, 개정 감지 지연을 줄이기 위해 주기를 강화했다.

1. 공식 법령명과 법령 ID를 확인한다.
2. 저장된 모든 조문의 제목ㆍ원문ㆍ시행일ㆍ해시를 비교한다.
3. 변경이 있으면 스냅샷과 연결 규칙의 `snapshot_date`를 갱신하고,
   `reports/statute_audit.json`에 영향받는 규칙 ID를 기록한다.
4. 정적 웹 번들을 같은 사전 버전으로 다시 생성한다.
5. 데이터 검증과 전체 테스트를 실행한다.
6. 자동 병합하지 않고 `statute-update` PR을 생성한다.

비공개 학습 코퍼스는 CI에 포함되지 않는다. 익명 코퍼스 집계 보고서는
조문 원문이 아니라 규칙 트리거ㆍ제외 범위ㆍ슬롯ㆍ정규화 알고리즘의
`matching_version`으로 유효성을 검사한다. 따라서 조문만 바뀐 감사 PR은
기존 익명 보고서를 유지할 수 있고, 실제 매칭 로직이 바뀐 경우에는 로컬
학습 코퍼스에서 보고서를 다시 생성하기 전까지 검증이 실패한다.

API 실패, 조문 삭제ㆍ이동, 법령명 불일치는 성공으로 취급하지 않고
워크플로를 실패시킨다.

## 사람 검수

변경 PR은 다음을 확인한 뒤에만 병합한다.

1. 감사 보고서의 `affected_rule_ids`가 실제 영향 범위와 일치하는가
2. 조문 번호와 적용 범위가 공고문 단계에 여전히 관련되는가
3. 개정 조문 시행일이 도래했는가
4. 규칙의 메시지와 대안 문구가 개정 취지와 충돌하지 않는가
5. 규칙 동작 변경 시 봉인 홀드아웃 회귀 평가를 다시 실행했는가
6. 웹과 MCP의 `ruleset_version`이 일치하는가

공식 원문과 차이가 발견되어도 운영 규칙을 자동으로 바꾸거나 병합하지
않는다. 검토 완료 전 배포본은 기존 검수 버전을 유지하며, 모든 검사
결과의 `statute_snapshot_date`와 `statute_notice`가 그 기준일을 알린다.

대상 조문과 규칙ㆍ질문 연결 현황 및 의도적으로 법령 근거를 단정하지
않은 항목은 [statute-scope-map.md](statute-scope-map.md)에 기록한다.

긴급 개정은 다음 정기 실행을 기다리지 않고 GitHub Actions의
`workflow_dispatch`로 즉시 비교한다.

## 로컬 명령

오프라인 무결성 검사:

```powershell
python tools\build_statutes.py
```

공식 현행 원문 비교:

```powershell
python tools\build_statutes.py `
  --check-official `
  --report reports\statute_audit.json
```

공식 변경분으로 검토용 작업 트리를 갱신:

```powershell
python tools\build_statutes.py `
  --refresh-official `
  --report reports\statute_audit.json
python tools\export_web_bundle.py
python -m pytest
```
