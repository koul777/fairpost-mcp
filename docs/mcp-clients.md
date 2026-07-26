# MCP 클라이언트 연결

검증일: 2026-07-26

## 로컬 HTTP

기본 엔드포인트는 `http://127.0.0.1:8000/mcp`다. 루프백 바인딩이므로
같은 컴퓨터의 클라이언트만 접근할 수 있고, 공고문을 외부 MCP 서버로
전송하지 않는다.

프로젝트 루트의 `.mcp.json`은 Claude Code용 HTTP 설정이다. 운영
`fairpost`와 개발용 `fairpost-local`을 함께 등록한다.

```json
{
  "mcpServers": {
    "fairpost": {
      "type": "http",
      "url": "https://fairpost-mcp.vercel.app/api/mcp",
      "headers": {
        "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}"
      }
    },
    "fairpost-local": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Claude Code에서 처음 사용할 때는 프로젝트 MCP 서버를 승인해야 한다.
현재 `claude mcp list`에서는 `fairpost`와 `fairpost-local`이 모두
`Pending approval`로 발견된다. 프로젝트에서 `claude`를 실행하여
사용할 서버를 승인한다.

```powershell
claude
```

## Codex

Codex CLI에는 운영 HTTP MCP를 전역 등록한다. 토큰 값 자체는 Codex
설정에 기록하지 않고 사용자 환경변수 이름만 참조한다.

```powershell
codex mcp add fairpost `
  --url https://fairpost-mcp.vercel.app/api/mcp `
  --bearer-token-env-var FAIRPOST_MCP_TOKEN

codex mcp get fairpost
```

현재 PC에는 `fairpost`가 `enabled`, `streamable_http`로 등록되어 있다.
MCP 목록은 Codex 프로세스 시작 시 로드되므로 등록 전에 열려 있던 앱과
대화에서는 도구가 즉시 나타나지 않는다. Codex 앱을 완전히 종료한 뒤
다시 실행하면 사용자 환경변수와 새 MCP 설정이 함께 로드된다.

## 원격 HTTP와 Vercel

Claude Desktop의 원격 HTTP 커넥터는 사용자의 `127.0.0.1`에 접근할 수
없다. Claude Desktop의 `Customize > Connectors > Add custom connector`
화면에는 다음 읽기 전용 주소를 등록한다.

```text
https://fairpost-mcp.vercel.app/api/claude-mcp
```

이 엔드포인트는 인증 없이 `check_job_posting` 하나만 제공하며 MCP
`ToolAnnotations`에서 `readOnlyHint: true`, `destructiveHint: false`로
표시한다. 저장ㆍ수정 도구는 노출하지 않는다. 연결 후
`Customize > Connectors > fairpost > Tool permissions`에서 읽기 전용
도구를 `Always allow`로 설정할 수 있다. 원격 배포는 공고문이 AI
제공자와 Vercel을 거친다는 사실을 사용자에게 고지해야 한다.

헤더를 지정할 수 있는 개발자용 운영 엔트리포인트는
`https://fairpost-mcp.vercel.app/api/mcp`이며 고정 Bearer 토큰을
지원한다. 임의 사용자가
접근하지 못하도록 Vercel의 `FAIRPOST_MCP_TOKEN`을 반드시 구성한다.
현재 PC에는 같은 이름의 사용자 환경변수로 토큰을 저장하므로 Claude
Code를 새로 시작하면 `.mcp.json`의 `${FAIRPOST_MCP_TOKEN}` 참조가
해석된다.
Bearer 토큰을 URL 쿼리에 넣지 않는다.

원격 `save_answer`와 `get_saved_answers`는 서버리스 로컬 파일을 사용하지
않는다. Upstash Redis 자격정보가 연결되지 않으면 두 도구는 명확한 오류를
반환한다. 상세 절차는 [vercel-deployment.md](vercel-deployment.md)에 있다.

참고:

- Anthropic 원격 MCP 안내:
  https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers
- 공식 MCP Inspector:
  https://modelcontextprotocol.io/docs/tools/inspector

## 독립 클라이언트 검증

공식 MCP Inspector 1.0.0으로 `check_job_posting`을 호출했다.

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  http://127.0.0.1:8000/mcp --transport http `
  --method tools/call --tool-name check_job_posting `
  --tool-arg "text=지원자격: 남성만 지원 가능"
```

호출은 종료 코드 0으로 끝났고 `SEX-001`, 남녀고용평등과 일ㆍ가정 양립
지원에 관한 법률 제7조, `isError: false`를 반환했다. SDK 프로토콜
테스트에서는 세 도구의 목록ㆍ점검ㆍ답변 저장ㆍ조회까지 검증한다.
감사 결과는 `reports/mcp_client_audit.json`에 원문 없이 저장한다.
Vercel 운영 호출 감사 결과는 `reports/vercel_deployment_audit.json`에
토큰과 시험 공고문 원문 없이 저장한다.
