# MCP 클라이언트 연결

검증일: 2026-08-31

## 로컬 HTTP

기본 엔드포인트는 `http://127.0.0.1:8000/mcp`다. 루프백 바인딩이므로
같은 컴퓨터의 클라이언트만 접근할 수 있고, 공고문을 외부 MCP 서버로
전송하지 않는다.

프로젝트 루트의 `.mcp.json`은 Claude Code용 HTTP 설정이다. 기본 이름
`fairpost`는 로컬 루프백만 가리킨다. Vercel 전송이 필요한 사용자가 데이터
경계를 확인한 뒤 명시적으로 선택할 수 있도록 운영 서버는
`fairpost-remote`라는 별도 이름으로 등록한다.

```json
{
  "mcpServers": {
    "fairpost": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    },
    "fairpost-remote": {
      "type": "http",
      "url": "https://fairmcp.vercel.app/api/mcp",
      "headers": {
        "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}"
      }
    }
  }
}
```

Claude Code에서 처음 사용할 때는 프로젝트 MCP 서버를 승인해야 한다.
`claude mcp list`에서 `fairpost`와 `fairpost-remote`가 `Pending approval`로
표시되면 프로젝트에서 `claude`를 실행하여 사용할 서버를 승인한다. 일반
검토에는 `fairpost`만 승인하고, 원격 처리가 필요하며 전송 경계를 수용한
경우에만 `fairpost-remote`도 승인한다.

```powershell
claude
```

## Codex

Codex CLI에서도 `fairpost`는 로컬 기본값으로 등록한다. 운영 HTTP MCP는
`fairpost-remote`라는 이름으로만 선택 등록한다. 토큰 값 자체는 Codex
설정에 기록하지 않고 사용자 환경변수 이름만 참조한다.

```powershell
codex mcp add fairpost `
  --url http://127.0.0.1:8000/mcp

codex mcp add fairpost-remote `
  --url https://fairmcp.vercel.app/api/mcp `
  --bearer-token-env-var FAIRPOST_MCP_TOKEN

codex mcp get fairpost
codex mcp get fairpost-remote
```

로컬 서버만 사용할 때는 첫 번째 등록으로 충분하다. 원격 서버를 등록한 뒤에는
Codex 앱을 완전히 종료하고 다시 실행해야 사용자 환경변수와 새 MCP 설정이
함께 로드된다. 원격 등록ㆍ재시작ㆍ실제 호출은 로컬 기본 설정과 분리된 운영
작업이다.

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
도구를 `Always allow`로 설정할 수 있다. 원격 MCP 호출은 Vercel 함수에서
결정론적 규칙 엔진으로 처리된다. 이를 클라우드 AI 클라이언트에 연결한 경우에는
해당 AI 제공자도 입력을 처리할 수 있다는 사실을 사용자에게 별도로 고지해야 한다.

헤더를 지정할 수 있는 개발자용 운영 엔트리포인트는
`https://fairmcp.vercel.app/api/mcp`이며 고정 Bearer 토큰을
지원한다. 임의 사용자가
접근하지 못하도록 Vercel의 `FAIRPOST_MCP_TOKEN`을 반드시 구성한다.
현재 PC에는 같은 이름의 사용자 환경변수로 토큰을 저장하므로 Claude
Code를 새로 시작하면 `.mcp.json`의 `${FAIRPOST_MCP_TOKEN}` 참조가
해석된다.
Bearer 토큰을 URL 쿼리에 넣지 않는다.

원격 엔드포인트는 인증 여부와 관계없이 `save_answer`와
`get_saved_answers`를 노출하지 않는다. 이 두 쓰기ㆍ조회 도구와 조직별 답변은
루프백 로컬 MCP에서만 사용한다. 상세 절차는
[vercel-deployment.md](vercel-deployment.md)에 있다.
로컬 `save_answer`는 같은 조직ㆍ질문에 저장된 기존 답변을 새 답변으로 교체할 수
있으므로 MCP 도구 주석도 파괴 가능 쓰기로 표시한다.

참고:

- Anthropic 원격 MCP 안내:
  https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers
- 공식 MCP Inspector:
  https://modelcontextprotocol.io/docs/tools/inspector

## 독립 클라이언트 검증

공식 MCP Inspector 2.4.0으로 현재 Bearer 운영 배포의 도구 목록과
`check_job_posting`, `check_job_posting_structured`를 호출했다.

```powershell
npx -y @modelcontextprotocol/inspector --cli `
  https://fairmcp.vercel.app/api/mcp --transport http `
  --header "Authorization: Bearer $token" `
  --method tools/call --tool-name check_job_posting `
  --tool-arg "text=여성만 지원 가능"

npx -y @modelcontextprotocol/inspector --cli `
  https://fairmcp.vercel.app/api/mcp --transport http `
  --header "Authorization: Bearer $token" `
  --method tools/call --tool-name check_job_posting_structured `
  --tool-arg "text=여성만 지원 가능"
```

호출은 종료 코드 0으로 끝났고 `SEX-001`, 남녀고용평등과 일ㆍ가정 양립
지원에 관한 법률 제7조, 구조화 v1과 `[0, 3]` 원문 offset,
`isError: false`를 반환했다. Inspector 목록은 공개
3도구가 모두 읽기 전용임을 확인했고, SDK 프로토콜 테스트는 일반 원격 3도구와
Claude 호환 평문 1도구, 루프백 로컬 5도구를 각각 검증한다.
감사 결과는 `reports/mcp_client_audit.json`에 원문 없이 저장하고 현재 배포 ID,
규칙ㆍ매칭ㆍ런타임 지문과 Vercel 감사 SHA-256에 결합한다. Claude Code의 프로젝트
MCP 승인은 2026-08-31 완료했고, `fairpost` 루프백 서버에서
`check_job_posting`, `check_job_posting_structured`, `next_review_question`의
실제 호출을 확인했다. 호출 관찰과
독립 감리 결정은 [Claude MCP × Codex 재감리 기록](ai-agent-review-2026-08-31.md)에
정리했다.
Vercel 운영 호출 감사 결과는 `reports/vercel_deployment_audit.json`에
토큰과 시험 공고문 원문 없이 저장한다.
