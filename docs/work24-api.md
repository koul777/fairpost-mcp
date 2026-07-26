# 고용24 채용정보 API

## 인증 설정

`.env`에는 인증키 한 항목만 둡니다.

```dotenv
WORK24_AUTH_KEY=인증키
```

다음은 목록 호출에 필요한 최소 요청입니다.

```text
GET https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do
  ?authKey=인증키
  &callTp=L
  &returnType=XML
  &startPage=1
  &display=10
```

XML 태그에는 따옴표를 붙이지 않습니다. 태그는 요청 파라미터나 환경
변수가 아니라 서버가 반환하는 구조입니다.

## 목록 응답 필드

| XML 태그 | 의미 |
|---|---|
| `total` | 총건수 |
| `wantedAuthNo` | 구인인증번호 |
| `company` | 회사명 |
| `busino` | 사업자등록번호 |
| `indTpNm` | 업종 |
| `title` | 채용제목 |
| `salTpNm` | 임금형태 |
| `sal` | 급여 |
| `minSal` | 최소임금액 |
| `maxSal` | 최대임금액 |
| `region` | 근무지역 |
| `holidayTpNm` | 근무형태 |
| `minEdubg` | 최소학력 |
| `maxEdubg` | 최대학력 |
| `career` | 경력 |
| `regDt` | 등록일자 |
| `closeDt` | 마감일자 |
| `infoSvc` | 정보제공처 |
| `wantedInfoUrl` | 채용정보 URL |
| `empTpCd` | 고용형태코드 |
| `jobsCd` | 직종코드 |
| `smodifyDtm` | 최종수정일 |

`empTpCd`는 `10` 기간의 정함이 없는 근로계약, `11` 같은 계약의
시간선택제, `20` 기간의 정함이 있는 근로계약, `21` 같은 계약의
시간선택제를 뜻합니다.

fairpost 수집기는 목록에서 `wantedAuthNo`를 얻은 뒤 `callTp=D`로 상세
정보를 조회합니다. 모든 상세 XML leaf 값을 공고 텍스트로 구성한 다음
회사명, 담당자 연락정보를 비식별화합니다. 인증키는 오류 로그와 공개
보고서에 기록하지 않습니다.

## 현재 확인 결과

2026-07-26 16:47 KST에 로컬 키로 공식 URL을 다시 호출했을 때 HTTP
200, `application/xml;charset=UTF-8`, `GO24` 루트와 함께 다음 업무
오류가 반환됐습니다.

```xml
<GO24>
  <error>개인회원은 사용할 수 없는 OPEN-API입니다.</error>
</GO24>
```

서버가 키를 개인회원 키로 식별한 결과이므로 따옴표나 XML 파싱 문제가
아닙니다. 현재 `.env` 값은 바깥 따옴표를 포함해 38자이고 수집기가
따옴표를 제거한 36자 값을 전송합니다. 고용24 신청현황의 처리값과
승인 대상 회원 유형을 확인해야 합니다.
