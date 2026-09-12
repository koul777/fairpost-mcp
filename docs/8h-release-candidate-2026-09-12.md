# 8시간 작업 릴리스 후보 점검

점검일: 2026-09-12 (KST)

## 현재 증거

| 항목 | 결과 | 근거 |
|---|---|---|
| 전체 자동 테스트 | 961/961 통과 | `reports/pytest-8h-2026-09-12-handoff.xml` |
| 현재 입력 fingerprint | JUnit에 기록·일치 | `fairpost_validation_source_fingerprint` |
| 로컬 evidence version audit | 21/21 current, pass | `reports/evidence_version_audit-8h-final.json` |
| 후보 배포 감사 | sdist/wheel pass, source-equivalent | `reports/distribution_audit-8h-final.json` |
| 후보 릴리스 보고서 | 생성됨; strict readiness blocked | `reports/build_artifact-8h-candidate-2026-09-12.json` |

## 확인된 blocker

1. 기존 `dist/fairpost-0.3.0` 파일은 현재 런타임 fingerprint보다 오래된 배포본이다. 따라서 기존 `distribution_audit.json`을 현재 변경의 릴리스 증거로 재사용하지 않는다. 현재 후보는 `.tmp/dist-candidate-8h-final`에서 별도 생성·감사했다.
2. 빌드 재현성 문제는 `packaging>=24.2`를 build-system 의존성으로 선언해 해결했고, 격리 후보 빌드가 성공했다.
3. 사람 holdout 라벨과 G1/G2 최종 평가, 민간 코퍼스 다양성, Work24 접근, 현재 배포 계약/외부 클라이언트 증거, release tag는 자동 테스트로 닫을 수 없다.

## 운영 결론

이번 점검은 “코드와 회귀 테스트는 통과했지만 strict release는 아님”으로 판정한다. 팀장 승인 없이 기존 배포 파일을 덮어쓰거나, 미검증 NCS 첨부 본문을 규칙 근거로 매핑하거나, 사람 평가를 자동 테스트로 대체하지 않는다.

다음 실행 순서는 승인된 빌드 환경에서 후보 sdist/wheel을 만들고 `verify_distribution.py`로 별도 감사한 뒤, 같은 실행의 JUnit·evidence audit·distribution audit을 묶어 candidate report를 생성하는 것이다.
