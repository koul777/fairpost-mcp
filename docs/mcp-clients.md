# MCP 클라이언트 연결

검증일: 2026-08-30

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
      "url": "https://fairmcp.vercel.app/api/mcp",
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
  --url https://fairmcp.vercel.app/api/mcp `
  --bearer-token-env-var FAIRPOST_MCP_TOKEN

codex mcp get fairpost
```

2026-08-30 재확인에서 `codex mcp get fairpost`는 아직 등록된 서버가 없다고
응답했다. 위 명령으로 등록한 뒤 Codex 앱을 완전히 종료하고 다시 실행해야
사용자 환경변수와 새 MCP 설정이 함께 로드된다. 이 등록ㆍ재시작ㆍ실제 호출은
현재 완료 증거가 아니라 남은 운영 작업이다.

## 원격 HTTP와 Vercel

Claude Desktop의 원격 HTTP 커넥터는 사용자의 `127.0.0.1`에 접근할 수
없다. Claude Desktop의 `Customize > Connectors > Add custom connector`
화면에는 다음 읽기 전용 주소를 등록한다.

```text
https://fairmcp.vercel.app/api/claude-mcp
```

이 엔드포인트는 운영자가 `FAIRPOST_ALLOW_PUBLIC_CLAUDE_REMOTE=1`을 별도로
설정한 경우에만 인증 없이 열립니다. 기본은 비활성화되고 Bearer 토큰이 있는
배포에서는 같은 인증을 적용합니다. 열려 있을 때도 `check_job_posting` 하나만 제공하며 MCP
`ToolAnnotations`에서 `readOnlyHint: true`, `destructiveHint: false`로
표시한다. 저장ㆍ수정 도구는 노출하지 않는다. 연결 후
`Customize > Connectors > fairpost > Tool permissions`에서 읽기 전용
도구를 `Always allow`로 설정할 수 있다. 원격 배포는 공고문이 AI
제공자와 Vercel을 거친다는 사실을 사용자에게 고지해야 한다.

헤더를 지정할 수 있는 개발자용 운영 엔트리포인트는
`https://fairmcp.vercel.app/api/mcp`이며 고정 Bearer 토큰을
지원한다. 임의 사용자가
접근하지 못하도록 Vercel의 `FAIRPOST_MCP_TOKEN`을 반드시 구성한다.
현재 PC에는 같은 이름의 사용자 환경변수로 토큰을 저장하므로 Claude
Code를 새로 시작하면 `.mcp.json`의 `${FAIRPOST_MCP_TOKEN}` 참조가
해석된다.
Bearer 토큰을 URL 쿼리에 넣지 않는다.

원격 엔드포인트는 인증 여부와 관계없이 `save_answer`와
`get_saved_answers`를 노출하지 않는다. 두 도구와 조직별 답변은 루프백 로컬
MCP에서만 사용한다. 상세 절차는 [vercel-deployment.md](vercel-deployment.md)에 있다.

참고:

- Anthropic 원격 MCP 안내:
  https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers
- 공식 MCP Inspector:
  https://modelcontextprotocol.io/docs/tools/inspector

## 독립 클라이언트 검증

공식 MCP Inspector 2.4.0으로 현재 Bearer 운영 배포의 도구 목록과
`check_job_posting`을 호출했다.

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  https://fairmcp.vercel.app/api/mcp --transport http `
  --header "Authorization: Bearer $token" `
  --method tools/call --tool-name check_job_posting `
  --tool-arg "text=여성만 지원 가능"
```

호출은 종료 코드 0으로 끝났고 `SEX-001`, 남녀고용평등과 일ㆍ가정 양립
지원에 관한 법률 제7조, `isError: false`를 반환했다. Inspector 목록은 공개
2도구가 모두 읽기 전용임을 확인했고, SDK 프로토콜 테스트는 원격 2도구와
루프백 로컬 4도구를 각각 검증한다.
감사 결과는 `reports/mcp_client_audit.json`에 원문 없이 저장하고 현재 배포 ID,
규칙ㆍ매칭ㆍ런타임 지문과 Vercel 감사 SHA-256에 결합한다. Claude Code의 프로젝트
MCP 승인은 여전히 사용자 확인이 필요한 별도 단계다.
Vercel 운영 호출 감사 결과는 `reports/vercel_deployment_audit.json`에
토큰과 시험 공고문 원문 없이 저장한다.
