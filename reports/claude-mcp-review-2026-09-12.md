# Claude MCP 협업 검토 기록

확인일: 2026-09-12 (KST)

## 호출 상태

- MCP가 제공한 Claude CLI에서 새 일회성 읽기 전용 검토를 실행했다.
- 기존 Claude 세션을 조회하거나 메시지를 보내지 않았다.
- Claude는 `core/review_packet.py`, `mcp_server/storage.py`, `mcp_server/server.py`, `data/guidance/ncs-fair-hiring.yaml`을 검토했다.
- Claude의 공식 NCS 페이지 접근은 해당 실행의 WebFetch 권한 거부로 확인하지 못했다. 따라서 NCS 최신성 판단은 별도 공식 브라우징 근거인 [`ncs-2026-guide-evidence.md`](ncs-2026-guide-evidence.md)와 구분한다.

## Claude가 제시한 우선순위

1. `ReviewEvent.note`가 최대 4,000자의 자유 텍스트라서 사용자가 공고 원문이나 지원자 개인정보를 붙여 넣으면 “원문 미저장” 계약을 우회할 수 있다. 필드명 차단만으로는 의미 기반 내용을 막지 못한다.
2. 기존 저장소 읽기는 모든 패킷을 매번 역직렬화·검증하므로, 손상된 패킷 하나가 무관한 패킷의 조회·추가까지 막을 수 있다.
3. 2026 가이드북은 공식 게시 항목의 존재와 메타데이터는 확인되지만, 첨부 본문 문구까지 파싱한 상태로 표현하면 안 된다.

## 반영한 조치

- `LocalReviewPacketStore`의 특정 `get`, `append`, `purge` 경로가 전체 패킷 검증 대신 top-level 구조와 대상 패킷만 읽도록 분리했다.
- `save`도 top-level 구조와 패킷 수만 확인한 뒤 새 패킷을 추가해, 무관한 손상 패킷 때문에 새 검토 시작이 막히지 않게 했다. 손상 패킷은 자동 삭제하지 않으며 대상 삭제·복구가 필요하다.
- 저장 패킷 수를 256개로 제한하고, `purge_role_review`로 특정 패킷 또는 전체 로컬 패킷을 삭제할 수 있게 했다.
- `note`에 원문·지원자 개인정보를 넣지 않는 운영 경고와, 2026 가이드 본문 미파싱 제한을 문서화했다.
- note에는 이메일·한국 휴대전화·주민등록번호처럼 고신뢰 직접식별 패턴을 거부하는 검증을 추가했다. 원문 전체나 모든 개인정보를 의미적으로 판별하는 기능은 의도적으로 약속하지 않는다.
- 손상된 무관 패킷이 정상 패킷 조회를 막지 않는 회귀 테스트를 추가했다.

## 남은 작업

- note의 개인정보 위험은 의미 기반 자동 판별보다 UI 경고·민감정보 패턴 경고·사람 확인 절차를 먼저 설계해야 한다. 자동 필터를 도입할 경우 정상적인 직무 근거를 훼손하지 않는 오탐 테스트가 필요하다.
- NCS 첨부파일은 다운로드·본문 파싱·페이지 단위 인용을 별도 작업으로 수행한 뒤에만 2026 통제와 직접 연결한다.
## 2026 NCS mapping decision

A fresh Claude review was run after the implementation pass. Because the 2026
attachment body is not yet downloaded or parsed, `ncs-2026-guide` remains in the
catalog as a verified official listing/detail source but is no longer mapped to
any control's `source_ids`. This prevents raw HR review packets from implying
direct textual support that has not been verified. The attachment verification
plan remains the follow-up; once page/section/hash evidence exists, the mapping
can be restored with per-control evidence records.
## Follow-up Claude verification

A fresh Claude pass confirmed that the NCS mapping guard and targeted packet
validation now close the two reviewed risks. It also identified the remaining
coverage gap around persisted byte-limit violations, so the role-review tests
now exercise both `get` and `append` against an oversized packet. The obsolete
full-store reader identified in that review has since been removed, so the
targeted-read refactor has one canonical packet-read path.
## Resolution contract follow-up

The next fresh Claude review found one remaining inconsistency: the MCP tool
required a target for `resolve`, but the packet contract accepted an unlinked
resolve event. The contract and browser storage validation now reject unlinked
resolve events while preserving the optional field for ordinary legacy events.

## Follow-up: team charter review

2026-09-12 fresh Claude CLI review checked `docs/team-charter-2026-09-12.md` and `reports/role-review-audit-2026-09-12.json`.

- The reviewer confirmed the exact charter heading and the `team_delegation.charter` path.
- The reviewer confirmed named assignments for Tesla (system design), Feynman (tests), Darwin (data/provenance), Hypatia (web/UX), Rawls (fairness/policy), and Cicero (documentation/release communication).
- An earlier call returned unrelated stamp/queue text; it was discarded as non-responsive and was not used as evidence.
