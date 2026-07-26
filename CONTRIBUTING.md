# 기여 가이드

## 규칙 변경

1. 후보 표현의 코퍼스 빈도와 출처 분할을 기록합니다.
2. 법령 규칙은 국가법령정보센터 원문으로 조항과 시행일을 확인합니다.
3. 모든 규칙에 `provenance`, `book_ref`, 검수 시점을 남깁니다.
4. 애매한 후보는 질문 카드로 전환하거나 `rejected.yaml`에 사유를 남깁니다.
5. 홀드아웃 원문은 규칙 작성 중 열람하지 않습니다.

기관 규칙은 `data/local_rules.example.yaml`을 복사해 로컬에서 관리하며
`basis.type: consensus`만 사용합니다.

## 품질 기준

```powershell
python -m pytest
python tools\export_web_bundle.py --check
python tools\build_statutes.py
python tools\verify_web_parity.py
python tools\verify_distribution.py
```

표현 탐지는 정밀도를 우선하고, 부재 탐지는 재현율을 우선합니다. 오탐을
고칠 때는 일반화된 제외 표현과 필요한 최소 window를 사용하고 회귀
테스트를 추가합니다. 점수·등급·합격·통과 판정은 도입하지 않습니다.

## 데이터

공고 원문, 담당자 개인정보, 기관별 순위는 커밋하지 않습니다. 공개
저장소에는 수집 코드와 익명 집계만 포함합니다. 사전과 문서 기여는
CC BY 4.0, 코드는 MIT 조건을 따릅니다.
