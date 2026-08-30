# Vercel 원격 MCP 배포

검증 기준일: 2026-08-30

## 엔드포인트

```text
읽기 전용 MCP:   https://fairmcp.vercel.app/api/mcp
Claude 읽기 전용: https://fairmcp.vercel.app/api/claude-mcp
상태 확인:       https://fairmcp.vercel.app/api/health
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
답변 저장ㆍ조회를 포함한 전체 5도구는 사용자 컴퓨터의 루프백 로컬 MCP에서만 제공한다.

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

2026-08-31 운영 배포 `dpl_Hqxh9tR2W2uAjKjyCEqeGcNpvuhr`는 일반ㆍClaude
경로의 익명 요청 401, Bearer 인증 후 일반 읽기 전용 3도구와 Claude 호환 평문
1도구의 실제 호출, 규칙ㆍ매칭 버전, 런타임 지문과 파일별 소스 해시, 보안 헤더
검증을 통과했다. 검증기는 저장 쓰기를 수행하지 않았으며 결과는
`reports/vercel_deployment_audit.json`에 원문ㆍ비밀값 없이 기록한다.

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
