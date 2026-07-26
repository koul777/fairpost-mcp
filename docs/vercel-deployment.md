# Vercel 원격 MCP 배포

검증 기준일: 2026-07-26

## 배포 구조

Vercel Python Function이 `api/index.py`의 ASGI `app`을 로드한다.
MCP 주소는 다음 두 가지다.

```text
개발자용 전체 도구: https://fairpost-mcp.vercel.app/api/mcp
Claude 읽기 전용:  https://fairpost-mcp.vercel.app/api/claude-mcp
```

브라우저에서 `https://fairpost-mcp.vercel.app/`를 열면 운영 주소와
상태 확인 링크를 표시한다. MCP 주소를 브라우저에서 인증 없이 직접 열면
전체 도구 주소는 HTTP 401이 나오는 것이 정상이다. Claude 읽기 전용
주소는 무인증으로 연결되며 `check_job_posting`만 노출한다.

`mcp_server.remote`는 FastMCP의 stateless Streamable HTTP 앱을 감싸며
다음을 적용한다.

- Bearer 인증
- Claude용 무인증 읽기 전용 도구 분리
- 1 MiB 기본 요청 크기 제한
- `Cache-Control: no-store`
- 보안 응답 헤더
- `/api/health` 상태 조회
- 공고문 원문 비영속 처리

로컬 `fairpost-mcp`의 `http://127.0.0.1:8000/mcp` 동작은 변경하지 않는다.

## 인증

긴 무작위 토큰을 생성한다.

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

Vercel 로그인과 프로젝트 연결 후 production 환경변수로 등록한다.

```powershell
npx --yes vercel@57 login
npx --yes vercel@57 link --yes --project fairpost-mcp
[Environment]::SetEnvironmentVariable(
  "FAIRPOST_MCP_TOKEN", $token, "User"
)
npx --yes vercel@57 env add FAIRPOST_MCP_TOKEN production `
  --value "$token" --yes --force --sensitive
```

`FAIRPOST_MCP_TOKEN`이 없는 Vercel 배포는 기본적으로 `/api/mcp` 요청을
503으로 거부한다. `/api/claude-mcp`는 공개되지만 입력을 저장하지 않고
외부 상태를 바꾸지 않는 `check_job_posting`만 제공한다. 토큰을 URL이나
저장소에 넣지 않는다.

헤더를 지원하는 MCP 클라이언트는 다음 형태로 연결한다.

```json
{
  "mcpServers": {
    "fairpost": {
      "type": "http",
      "url": "https://fairpost-mcp.vercel.app/api/mcp",
      "headers": {
        "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}"
      }
    }
  }
}
```

## 답변 저장

Vercel Functions의 파일시스템을 영속 저장소로 간주하지 않는다.
`save_answer`와 `get_saved_answers`를 사용하려면 Vercel Marketplace에서
Upstash Redis를 프로젝트에 연결하고 다음 환경변수가 주입되었는지
확인한다.

```text
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
```

이름이 이전 Vercel KV 형식이면 `KV_REST_API_URL`,
`KV_REST_API_TOKEN`도 인식한다. `org_id`는 Redis 키에 그대로 저장하지
않고 SHA-256으로 변환하며, 질문 ID와 답변은 Redis hash에 저장한다.

Upstash가 없더라도 `check_job_posting`은 동작한다. 답변 저장ㆍ조회는
영속성을 가장하지 않고 구성 오류를 반환한다.

## 배포와 검증

```powershell
npx --yes vercel@57 deploy --prod --yes

Invoke-RestMethod https://fairpost-mcp.vercel.app/api/health

npx -y @modelcontextprotocol/inspector --cli `
  https://fairpost-mcp.vercel.app/api/mcp --transport http `
  --header "Authorization: Bearer $token" `
  --method tools/list

$env:FAIRPOST_MCP_TOKEN = [Environment]::GetEnvironmentVariable(
  "FAIRPOST_MCP_TOKEN", "User"
)
python tools/verify_vercel_deployment.py
```

프로덕션 배포 후에는 `check_job_posting`을 실제 호출해 규칙 ID,
법령 근거, `isError: false`를 확인한다. 상태 URL에는 비밀정보나
공고문을 포함하지 않는다.

2026-07-26 운영 검증 결과:

- 프로젝트: `hrkim/fairpost-mcp`
- 프로덕션: `https://fairpost-mcp.vercel.app`
- 전송: stateless Streamable HTTP
- 무인증 요청: HTTP 401
- 노출 도구: 3개
- 제한적 성별 자격 사례: `SEX-001`, 제7조, `isError: false`
- Upstash 미연결: 답변 저장ㆍ조회 비활성

감사 증거는 `reports/vercel_deployment_audit.json`에 있으며 토큰과
시험 공고문 원문은 기록하지 않는다.

커스텀 도메인을 추가하면 DNS rebinding 보호가 허용하도록
`FAIRPOST_MCP_ALLOWED_HOSTS`와 `FAIRPOST_MCP_ALLOWED_ORIGINS`에도
해당 호스트와 HTTPS origin을 등록한다.

## 개인정보 처리 경계

- 정적 웹ㆍCLI: 입력이 기기 밖으로 나가지 않는다.
- 로컬 MCP: FairPost MCP 서버는 로컬이지만, 클라우드 AI 클라이언트에
  입력한 공고문은 해당 AI 제공자가 처리할 수 있다.
- Vercel MCP: AI 제공자와 Vercel이 공고문을 처리한다.
- FairPost 엔진: 공고문 원문을 파일ㆍDB에 영속 저장하지 않는다.
- 답변 저장을 켜면 질문 답변은 구성한 Upstash Redis에 저장된다.

원격 모드는 PRD의 “사용자 입력 공고문을 저장ㆍ전송하지 않는다”를
기기 내 처리로 해석할 수 없다. 따라서 사용자 화면과 운영 문서에서
원격 처리 사실을 별도로 표시해야 한다.

## 공식 참고

- Vercel MCP 배포:
  https://vercel.com/docs/mcp/deploy-mcp-servers-to-vercel
- Vercel Python Runtime:
  https://vercel.com/docs/functions/runtimes/python
- Vercel Marketplace Storage:
  https://vercel.com/docs/marketplace-storage
- Upstash Redis REST API:
  https://upstash.com/docs/redis/features/restapi
