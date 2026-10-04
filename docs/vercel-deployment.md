# Vercel 원격 MCP 배포

검증 기준일: 2026-08-30 (운영 배포 검증 기록은 2026-08-31)<br>
최종 갱신: 2026-10-03 (도구 수 표기와 운영 증거 상태를 바로잡았으며 배포를 다시 검증한 것은 아님)

## 엔드포인트

```text
읽기 전용 MCP:   https://fairmcp.vercel.app/api/mcp
Claude 읽기 전용: https://fairmcp.vercel.app/api/claude-mcp
상태 확인:       https://fairmcp.vercel.app/api/health
선택적 보강 검토: https://fairmcp.vercel.app/api/assisted-review
```

Vercel Python Function은 `api/index.py`의 ASGI `app`을 로드한다. 로컬
`fairpost-mcp`의 `http://127.0.0.1:8000/mcp` 동작은 바꾸지 않는다.

## 접근 모드

- `FAIRPOST_MCP_TOKEN`이 설정되면 `/api/mcp`는 Bearer 인증 뒤에 읽기 전용 분석 도구 3개를 제공한다.
- `FAIRPOST_MCP_TOKEN`이 없고 `FAIRPOST_ALLOW_PUBLIC_REMOTE=1`이면 `/api/mcp`는 공개되지만 읽기 전용 분석 도구 3개만 제공한다.
- `/api/mcp` 도구는 인증 여부와 관계없이 `check_job_posting`, `check_job_posting_structured`, `next_review_question`이다.
- `/api/claude-mcp`는 기본 비활성화된다. `FAIRPOST_MCP_TOKEN`이 있으면 같은
  Bearer 인증을 적용하고, `FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE=1`을 별도로
  설정한 경우에만 무인증 읽기 전용 `check_job_posting` 하나를 제공한다.

공유 Bearer 토큰만으로는 호출자가 제출한 `org_id`의 소유권을 증명할 수 없다.
따라서 네트워크 배포에는 `save_answer`, `get_saved_answers`를 노출하지 않는다.
NCSㆍ선택적 현행법 HR 검토, 답변 저장ㆍ조회와 역할 검토 기록을 포함한 전체 10도구는 사용자 컴퓨터의 루프백 로컬 MCP에서만 제공한다.

정적 웹의 선택형 `AI·현행 법령 보강` 스위치는 `/api/assisted-review`를
사용한다. Claude는 `FAIRPOST_ANTHROPIC_API_KEY`ㆍ`FAIRPOST_ANTHROPIC_MODEL`,
GPT는 `FAIRPOST_OPENAI_API_KEY`ㆍ`FAIRPOST_OPENAI_MODEL`, Gemini는
`FAIRPOST_GEMINI_API_KEY`ㆍ`FAIRPOST_GEMINI_MODEL`로 각각 설정한다. 둘 이상을
설정하면 웹에서 요청별로 선택하며 `FAIRPOST_AI_PROVIDER`가 최초 선택값이다.
이 중 하나와 Korean Law MCP가 모두 설정된 배포에서만 준비 상태가 된다. 스위치가 꺼진
기본 상태에서는 이 경로를 호출하지 않는다. 호출 제한은 기본 분당 5회이며
`FAIRPOST_ASSISTED_REVIEW_REQUESTS_PER_MINUTE`로 조정한다. 유료 AI API를
공개 배포에 연결할 때는 이 인스턴스별 제한 외에 배포 접근제어와 전역 비용
한도를 별도로 설정해야 한다.

**보강 실행 접근제어(실패 시 닫힘):** AI 제공자가 설정된 원격 배포에서
`POST /api/assisted-review`는 다음 중 하나를 만족할 때만 처리하고, 아니면 403과
이유를 반환한다.

- 사내 클라이언트: `Authorization: Bearer <FAIRPOST_ASSISTED_REVIEW_TOKEN>`
  (상수 시간 비교, `Authorization` 헤더가 정확히 하나일 때만). MCP용
  `FAIRPOST_MCP_TOKEN`과 별도 값이다. 브라우저 코드에는 넣을 수 없으므로 웹
  화면에서는 이 방식을 쓰지 않는다.
- 브라우저: `FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER=1`이고, 요청 `Origin`이
  `FAIRPOST_ASSISTED_REVIEW_ALLOWED_ORIGINS`(쉼표 구분, 정확히 일치) 중 하나이며
  `Sec-Fetch-Site: same-origin`일 때. 목록을 비우면 MCP 허용 출처(Vercel 기본
  출처와 `FAIRPOST_MCP_ALLOWED_ORIGINS`)를 재사용하되 `*`가 들어간 항목은 제외한다.

둘 다 설정하지 않고 AI 키만 넣으면 `GET /api/assisted-review`와 `/api/health`의
`assisted_review`는 `ready:false`와 "보강 실행 인증이 설정되지 않았습니다" 이유를
반환하고 POST는 거부되어 웹 스위치가 비활성화된다. 토큰만 설정한 배포는 토큰 없는
조회에 `ready:false`를 반환해 웹 스위치를 끈다. 응답의 `access_control`에는 각
방식의 사용 여부만 표시하며 토큰 값은 응답ㆍ로그에 남기지 않는다. 루프백 로컬 런타임
(`python -m mcp_server.local_runtime web`)은 기존 동일 출처 검사만 적용하며 이 게이트를 쓰지 않는다.

브라우저 허용은 비용 방어로는 약하다. `Origin`과 `Sec-Fetch-Site`는 정상
브라우저가 다른 사이트에서 이 경로를 호출하지 못하게 막을 뿐이며, curl 같은
비브라우저 클라이언트는 두 헤더를 임의로 위조할 수 있다.

**배포 게이트:** 운영 Production 환경에 AI 제공자 키를 설정하기 전에 다음을 모두
확인한다. 2026-10-04 운영 확인 결과 `ai_configured:false`로 키가 없어 현재 비용
노출은 없다.

- 위 접근제어 중 하나 이상을 설정하고 배포 후 `GET /api/assisted-review`의
  `access_control`과 `ready`로 확인한다. 둘 다 없으면 키를 넣어도 요청은 거부된다.
- 사내 토큰만 쓰는 경우에도 제공자 계정의 지출 상한을 설정한다.
- 공개 브라우저 허용(`FAIRPOST_ASSISTED_REVIEW_ALLOW_BROWSER`)을 켜는 경우 헤더
  위조로 누구나 호출할 수 있다고 가정하고, 제공자 계정의 지출 상한과 Vercel
  Firewall의 `/api/assisted-review` 요청 제한(rate limiting) 규칙을 반드시 함께
  설정한다. 인스턴스별 분당 제한은 서버리스 인스턴스 사이에 공유되지 않는다.
- Vercel에서 실제 클라이언트 IP가 호출 제한 키로 들어오는지 확인한다.

법령 MCP 조회 후 AI를 순차 호출하므로 Python Function의 `maxDuration`은 60초로
설정한다. AI 호출 자체는 기본 30초에서 중단되어 함수 제한 안에 실패 응답을
반환하도록 한다.

모델 변수를 생략하면 2026-09-13 기준 기본값 `claude-sonnet-5`,
`gpt-5.6-terra`, `gemini-3.6-flash`를 사용한다. 운영 계정에서 허용하지 않거나
비용 정책이 다르면 해당 `*_MODEL`을 명시한다.

제공자별 API URL은 기본으로 각 공식 엔드포인트를 사용한다. 선택적
`FAIRPOST_ANTHROPIC_API_URL`, `FAIRPOST_OPENAI_API_URL`,
`FAIRPOST_GEMINI_API_URL`을 지정해도 해당 공식 호스트와 정해진 경로만
허용하므로 서버 키가 임의 호스트로 전송되지 않는다. 이전
`FAIRPOST_AI_API_URL`ㆍ`FAIRPOST_AI_API_KEY`ㆍ`FAIRPOST_AI_MODEL`은 OpenAI 호환
로컬·사내 엔드포인트용으로 유지된다.

`mcp_server.remote`는 다음 보안 기본값을 적용한다.

- DNS rebinding 보호
- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- 기본 요청 바디 제한 1 MiB
- 익명 경로의 클라이언트별ㆍ인스턴스별 고정 창 제한(기본 분당 60회,
  `FAIRPOST_PUBLIC_REQUESTS_PER_MINUTE`로 1~10,000 범위 조정)

이 제한은 원문을 저장하지 않고 클라이언트 주소를 인스턴스별 임시 HMAC 키로
익명화해 최대 1분만 보유한다. 서버리스 인스턴스 사이에 공유되는 전역 제한은
아니다. 따라서 익명 경로를 일반 공개하기 전에는
외부 게이트웨이의 전역 제한과 남용 모니터링을 추가해야 한다. 별도 통제가 없는
동안에는 Bearer 모드 또는 기본 비활성 모드를 사용한다.

## 인증 설정

읽기 전용 엔드포인트를 제한된 사용자에게만 제공할 때는 `FAIRPOST_MCP_TOKEN`을 설정한다.

```powershell
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
  $bytes = New-Object byte[] 32
  $rng.GetBytes($bytes)
} finally {
  $rng.Dispose()
}
$token = (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
```

```powershell
npx --yes vercel@57 login
npx --yes vercel@57 link --yes --project fairmcp
[Environment]::SetEnvironmentVariable(
  "FAIRPOST_MCP_TOKEN", $token, "User"
)
npx --yes vercel@57 env add FAIRPOST_MCP_TOKEN production `
  --value "$token" --yes --force --sensitive
```

`FAIRPOST_MCP_TOKEN`이 없으면 `/api/mcp`는 기본적으로 503으로 거부된다.
운영자가 명시적으로 `FAIRPOST_ALLOW_PUBLIC_REMOTE=1`을 넣은 경우에만 공개
읽기 전용 모드로 열린다. 토큰은 URL이나 저장소에 넣지 않는다.

헤더를 지원하는 MCP 클라이언트는 다음 형태로 연결한다.

```json
{
  "mcpServers": {
    "fairpost": {
      "type": "http",
      "url": "https://fairmcp.vercel.app/api/mcp",
      "headers": {
        "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}"
      }
    }
  }
}
```

## 답변 저장

Vercel 원격 엔드포인트는 답변을 저장하거나 조회하지 않는다. 조직 답변은
`fairpost-mcp`를 루프백에서 실행할 때 사용자 컴퓨터의 로컬 JSON에만 저장한다.
원격 저장은 사용자별 인증 주체와 조직 권한 결합, 보존ㆍ삭제 정책, 암호화와
감사 로그가 설계되기 전까지 지원하지 않는다.

## 배포 검증

```powershell
npx --yes vercel@57 deploy --prod --yes

Invoke-RestMethod https://fairmcp.vercel.app/api/health

npx -y @modelcontextprotocol/inspector --cli `
  https://fairmcp.vercel.app/api/mcp --transport http `
  --header "Authorization: Bearer $token" `
  --method tools/list

$env:FAIRPOST_MCP_TOKEN = [Environment]::GetEnvironmentVariable(
  "FAIRPOST_MCP_TOKEN", "User"
)
python tools/verify_vercel_deployment.py
python tools/verify_vercel_deployment.py --allow-write-check
```

- 기본 `verify_vercel_deployment.py`는 읽기/목록/상태만 검증하고 실서버 저장 쓰기는 하지 않는다.
- `--allow-write-check`는 호환성 플래그이며 원격 저장을 호출하지 않고 쓰기 도구가 목록에 없음을 검증한다.
- 릴리스 증거를 만들 때는 `--source-commit`, `--verified-by`, `--approval-ref`를 함께 지정한다. 이 값은 배포와 승인 흐름을 추적하는 운영 메타데이터이며 전자서명이나 신원 증명은 아니다.
- 상태 응답에는 공고문 원문이나 비밀값을 넣지 않는다.

2026-08-31 운영 배포 `dpl_3BxzdYUYzsqShgdX5HW4zVudgWQX`는 일반ㆍClaude
경로의 익명 요청 401, Bearer 인증 후 일반 읽기 전용 3도구와 Claude 호환 평문
1도구의 실제 호출, 규칙ㆍ매칭 버전, 런타임 지문과 파일별 소스 해시, 보안 헤더
검증을 통과했다. 검증기는 저장 쓰기를 수행하지 않았으며 결과는
`reports/vercel_deployment_audit.json`에 원문ㆍ비밀값 없이 기록한다.

이 기록은 2026-08-31 배포 기준이다. 2026-10-02에 확인한 운영 `/api/health`의 런타임 지문
`runtime-4b2fc967…`은 이 저장소 `main`(5c6a968)의 지문과 일치하지만, 커밋된 감사 보고서는
이전 지문 `runtime-e31bb133…`을 기록하고 있어 현재 배포에 대해서는 다시 생성되지 않았다.
같은 시점에 `/api/health`의 `assisted_review`는 `ready: false`, `available_providers: []`였다.

### 2026-10-04 수정 전후 비교 배포

소스 `2b228897e977635cfc58c6022e173de60078c17b`를 배포한
`dpl_FVpndNcd5gofWCQZxbPoW9ea7UhG`가 `https://fairmcp.vercel.app`에 연결됐다.
공개 health의 규칙ㆍ매칭ㆍ소스 지문은 로컬과 일치하고, 운영 웹에서 수정 전후 비교의
데스크톱ㆍ태블릿ㆍ모바일 동작과 axe 점검을 통과했다.

인증 후 MCP 호출은 로컬 `FAIRPOST_MCP_TOKEN`으로 401이 반환되어 보류했다.
Vercel의 민감 환경변수는 pull 시 `[SENSITIVE]` 자리표시자만 반환하므로 이것을 토큰으로
사용하거나 기존 운영 토큰을 임의 교체하지 않는다. 올바른 토큰으로 위 검증기를 다시
실행하기 전까지 `reports/vercel_deployment_audit.json`은 과거 증거로 유지한다.
이번 공개 상태ㆍ웹 검증과 인증 차단은 `reports/production_rollout.json`에 분리해 기록한다.

## 개인정보 처리 경계

- 정적 웹과 CLI는 입력이 기기 밖으로 나가지 않는다.
- 로컬 MCP는 서버는 로컬이지만, 연결한 클라우드 AI 클라이언트가 입력을 처리할 수 있다.
- Vercel MCP는 Vercel 함수에서 공고문을 처리한다. 클라우드 AI 클라이언트에
  연결한 경우에는 해당 AI 제공자도 입력을 처리할 수 있다.
- FairPost 엔진은 공고문 원문을 파일이나 DB에 영속 저장하지 않는다.
- 위 비영속 진술은 FairPost 애플리케이션의 파일ㆍDB 저장 동작에 한정한다. AI
  제공자와 Vercel의 전송 처리ㆍ요청 로그ㆍ보존 정책은 각 제공자의 계약과 설정을
  별도로 확인해야 한다.
- 질문 답변 저장ㆍ조회는 루프백 로컬 MCP에서만 제공한다.

## 참고

- Vercel MCP 배포:
  https://vercel.com/docs/mcp/deploy-mcp-servers-to-vercel
- Vercel Python Runtime:
  https://vercel.com/docs/functions/runtimes/python
