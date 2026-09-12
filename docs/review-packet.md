# 다중 역할 공정채용 검토 패킷

`core.review_packet`은 하나의 공고 검토에 여러 관점을 참여시키기 위한 로컬 우선 기록 계약이다. 위원장이 최종 흐름을 조정하되, 각 역할은 독립적인 확인·수정 요청·이슈 제기를 남긴다.

## 참여 역할

| 역할 | 검토 초점 |
|---|---|
| `chair` | 쟁점 우선순위, 역할 간 충돌, 최종 조정 |
| `hr_owner` | 공고·지원서·운영 절차의 실행 가능성 |
| `job_sme` | 요건과 평가 요소의 직무관련성 |
| `interviewer` | 면접위원용 정보 차단, 질문·평가기준 운영 |
| `policy_reviewer` | 법령·공식 지침과의 구분, 예외 근거 |
| `auditor` | 재현성, 근거 링크, 버전·감사 추적 |
| `candidate_advocate` | 지원자 관점의 이해 가능성·불필요한 부담 |

역할은 법률 판정자나 합격 결정자가 아니다. 엔진 결과를 대체하지 않고, 사람이 확인할 쟁점과 후속 조치를 분리해 기록한다.

## NCS 공정채용 프로세스와 연결

공식 NCS 프로세스의 5단계인 `analysis → design → development → implementation → evaluation`을 `ReviewEvent.stage`로 사용한다. 따라서 “공고가 괜찮다”는 한 번의 의견 대신, 직무분석부터 채용 종료 후 환류까지 어느 단계에서 어떤 역할이 무엇을 확인했는지 남길 수 있다.

## 개인정보 경계

- 패킷에는 공고 원문 대신 SHA-256 `posting_fingerprint`만 넣는다.
- `posting_text`, 지원자 기록, 인적사항 필드를 입력하면 거부한다.
- `evidence_refs`에는 질문 ID·규칙 ID·공식 출처 ID처럼 식별자만 넣고, 원문은 로컬 검토 저장소에 둔다.
- `note`도 자유 텍스트로 저장되므로 공고 원문·지원자 식별정보·연락처를 붙여 넣지 않는다. 현재 구현은 필드명 차단과 길이 제한을 제공하지만 의미 기반 개인정보 필터를 제공하지 않는다.
- 구현은 이메일·한국 휴대전화·주민등록번호처럼 고신뢰 직접식별 패턴만 거부한다. 직무 근거 문장과 원문 유사 문장을 의미적으로 완벽히 판별한다고 보장하지 않으므로, 검토자는 원문 대신 요약과 `evidence_refs`를 사용한다.
- `actor_ref`는 기본적으로 내부 검토자 식별자만 사용한다. 이메일·주민번호·지원자 ID를 넣지 않는다.
- 패킷은 공정성 검토 이력이지 점수·순위·합격/불합격 결과가 아니다.

## 현재 연결과 다음 단계

1. `start_role_review`가 엔진의 findings/questions를 근거 참조로 바꾸고 위원장 이벤트를 생성한다.
2. `record_review_event`가 동일한 fingerprint와 버전 조합을 확인하며 역할별 이벤트를 추가한다.
3. `get_role_review`로 참여 역할과 이벤트를 조회할 수 있고, 정적 웹 UI에도 브라우저 전용 역할 검토 큐를 연결했다. `edit_requested` 또는 `escalate`는 `resolves_event_id`로 `resolve` 이벤트와 연결되며, 미해결 이슈 수와 종료 상태를 함께 표시한다.
4. `purge_role_review`로 특정 패킷 또는 로컬 패킷 전체를 삭제할 수 있다. 삭제 도구는 패킷 내용을 반환하지 않으며, 저장소 파일이 비면 파일도 제거한다.
5. 현재 저장소는 파일 기반 로컬 전용이다. 원격 공유는 인증·조직 권한·보존기간·삭제·암호화 정책이 확정된 뒤 opt-in으로 검토한다.

저장소의 새 패킷 저장·특정 패킷 조회·추가·삭제는 top-level 구조만 읽은 뒤 요청한 패킷을 역직렬화한다. 따라서 다른 패킷의 손상된 이벤트가 정상 패킷의 조회·추가·새 저장을 막지 않지만, 손상된 패킷은 자동 삭제하지 않으며 JSON 파일 구조 오류는 여전히 운영자 확인이 필요하다.

근거: [NCS 공정채용 소개](https://www.ncs.go.kr/blind/bl01/RH-102-001-01.scdo), [NCS 공정채용 프로세스](https://www.ncs.go.kr/blind/bl01/RH-102-002-01.scdo), [공정채용 가이드북 자료실](https://www.ncs.go.kr/blind/rh13/bbs_lib_list.do?libDstinCd=19).
## Storage limits

The local packet file is bounded to 256 packets and 128 MiB. Each packet is
limited to 256 events and 256 KiB. Reads and appends revalidate the selected
packet, while a full purge can remove a damaged or oversized local file without
parsing it. An oversized write is rejected before creating a temporary file.
Resolution semantics: a new `resolve` event must carry `resolves_event_id`, and
the target must be an `edit_requested` or `escalate` event in the same
packet. This keeps the chair's open-issue count reproducible across MCP and
browser review surfaces.

The packet also exposes `missing_roles` in the local MCP response so the chair
can see which of the seven review perspectives have not contributed yet.
