# 책 부록 보완 구현·검증 · 2026-10-09

범위 내 구현과 검증을 완료했다. 사용자 검토용 실제 로컬 앱은 **http://127.0.0.1:8123/web/**에서 계속 실행한다. 공개 배포·push·커밋은 하지 않았다.

## 제품 동작

쉬운 모드에서도 분배·절차·대인·정보의 의미를 짧게 읽고 해당 공고의 질문에 답할 수 있다. 두 모드는 같은 답변 상태를 사용하며 현재 모드의 입력만 렌더링한다. 확인한 사실·근거, 수정 여부와 이유, 남은 확인사항·담당자·다음 행동을 기록하고 메모 복사로 보관한다. 같은 문구와 규칙으로 다시 검사하면 답변·메모를 유지한다.

문구 또는 규칙 버전이 달라지면 이전 답변·메모를 수정 전 기록으로 분리한다. 현재 결과가 오래된 상태에서는 편집과 복사를 막는다. 이전 기록은 HTML 이스케이프해 표시하며, 기록이 있는 최근 최대 10회만 현재 탭 메모리에 남는다. 한도와 새로고침 시 소실을 화면 및 README에서 설명했다. 기존 역할 기록의 별도 브라우저 저장 정책은 유지한다.

조직 조건과 역할 기록은 쉬운 모드에서도 선택 disclosure로 접근한다. AI·현행 법령 보강은 전문가 모드의 선택 기능으로 유지한다. 기본 공고 검사는 브라우저 내부의 기존 결정론적 엔진을 사용하고 외부 AI를 호출하지 않는다. AI 안내 미탐지를 실제 AI 사용의 증거로 해석하지 않도록 적용 여부 확인 문구를 추가했다. 수정 전후 비교는 중립적 확인 목록이며 점수·녹색 통과·공정성 보증을 만들지 않는다.

fairpost web 명령은 기존 로컬 런타임을 사용한다. --open-browser는 서버 준비 뒤에만 브라우저를 연다. Windows run_fairpost_web.bat도 같은 경로를 사용한다. 소스 체크아웃과 설치 패키지의 완전한 웹 자산을 찾고 의존성 누락·포트 충돌·자산 누락을 안내한다. 루프백 주소, 동일 출처·호스트 보호, 기존 MCP/stdio 동작을 유지한다. 잘못된 선택형 AI 설정 때문에 기본 화면 기동이 실패하지 않으며 보강 기능은 사유와 함께 사용 불가로 처리한다.

README와 루트 랜딩은 로컬 화면을 첫 경로로 안내한다. 원격 endpoint 표·개인정보·Bearer 요구·기존 Vercel 계약 링크는 전체를 선택 details 안에 보존했다. 공개 AI 제공 여부는 2026-10-02의 기존 확인 기록으로 설명하며 새 외부 확인을 하지 않았다.

## 책 근거와 판단 경계

사용자 제공 10월 교정 PDF의 인쇄 쪽수와 PDF 펼침면 번호를 구분했다. 네 관점은 인쇄 2~7쪽/PDF 5~7, 문의·재검토는 10~13쪽/PDF 9~10, AI 운영 질문은 126쪽/PDF 67, 지원자의 목소리는 202~203쪽/PDF 105, 데이터·대리변수의 운영 확인은 262·270쪽/PDF 135·139, 실제 사람 개입의 권한·시간·정보·책임은 298~299쪽/PDF 153에 연결한다. 부록의 채용공고 검토 질문이라는 취지는 302~303쪽/PDF 155다.

book-map과 설계 문서는 책의 관점 연결과 법령·연구·NCS의 직접 근거를 분리한다. 기존 book_ref에는 외부 보고서 위치도 있어 모두 이 책의 쪽수로 취급하지 않는다. 채용공고만으로 실제 운영·모델 감사 결과를 단정하거나 채용 외 보상·승진 전체를 지원한다고 확대하지 않는다. 책의 QR 최종 목적지 확인은 인쇄 전 편집 확인 항목으로 남는다.

## 변경 파일

| 영역 | 변경 파일 |
|---|---|
| 실행 | cli/main.py, mcp_server/local_runtime.py, run_fairpost_web.bat |
| 화면 | web/index.html, web/app.js, web/styles.css |
| 회귀 | tests/test_cli.py, tests/test_local_runtime.py, tests/test_web_parity.py, tests/web_app_review_runner.cjs |
| 독자 안내 | README.md, index.html |
| 책·감사 | docs/book-map.md, docs/book-companion-design-2026-10-09.md, docs/book-companion-review-2026-10-09.md, 이 문서 |

PDF·추출물·PNG·런타임 세션/검증 로그는 Git 제외 tmp/book-review/에 유지한다. pyproject.toml이나 엔진·규칙 데이터는 변경하지 않았다.

## 검증

| 검증 | 정확한 결과·증거 |
|---|---|
| 단일 전체 Python 회귀 | python -m pytest -ra → **1307 passed, 8 skipped in 218.19s (0:03:38)**. 실패·오류 0, exit 0. tmp/book-review/full-pytest.txt 및 full-pytest-exit.txt |
| skip | tests/test_statute_audit_workflow.py의 8개 사례는 bash와 jq 필요. 환경 요건으로 skip되었으며 통과로 세지 않음 |
| 집중 실행 | CLI·로컬 런타임·원격 보안·MCP 88개 통과, exit 0. runtime-targeted-tests-final.txt 및 exit 파일 |
| JS UI | 최종 web_app_review_runner.cjs JSON 완성, stderr 빈 파일. 쉬운 질문·ID 유일성·모드/같은 입력 유지·오래된 복사 차단·이전 기록 이스케이프·기본 외부 POST 0 확인. 전체 pytest의 UI wrapper도 통과 |
| export | python tools/export_web_bundle.py --check 통과. 규칙 버전 2026.07-local-v5-nfkc-whitespace-zero-width-regex-source-offset-da8f450a87f4 유지 |
| 실제 Python 앱 | leader-functional-qa.json의 18개 확인 모두 통과: DOM-event 17개와 이미 검증한 실제 clipboard 1개. 복사 8681자와 이전 근거 확인 |
| 실제 시각·네트워크 | headless-visual-qa.json: 1366×900과 375×812 모두 가로 넘침 없음, 4 관점/17 답변, 중복 ID와 JS 오류 없음. 기록된 네트워크는 루프백 GET 12개, 외부 요청·POST 없음. 팀장이 desktop/mobile PNG 레이아웃 수용 |
| 불변성 | SHA-256 전후 6/6 일치. web/engine.js, web/data.js, core/engine.py, data/rules/questions.yaml, data/rules/law.yaml, data/slots.yaml. 전체 값은 invariant-hashes-final.json |

초기 집중 실행에서는 새 optional 설정 테스트 세 개가 MCP SDK의 한 번만 실행 가능한 lifespan manager를 재사용해 실패했다. 실행 중 UI 소스 변경으로 fingerprint teardown도 실패했다. 테스트마다 독립 FastMCP fixture를 사용하도록 수정하고 파일이 안정된 뒤 집중·전체 회귀를 통과했다. 원래 fingerprint guard와 제품 엔진을 우회하지 않았다. 초기 실패 로그는 runtime-targeted-tests.txt로 보존했다.

Orca의 큰 viewport screenshot 타일링과 offscreen browser.click의 무동작은 검증 도구의 관찰이다. DOM 오류를 별도로 확인하고, 클릭 전 scrollIntoView 및 fresh snapshot을 사용했다. 정상 desktop/mobile 캡처는 별도 headless Chromium을 검증된 내부 PTY에서 실행해 생성했다. root landing 캡처 이후 원격 details 범위를 고쳤으며 최종 계약 assertions는 전체 회귀에서 통과했다.

## 실제 Orca 실행과 전달 복구

Run: run_3a388a95356a. 소유 코디네이터: term_9b843729-acac-4596-b652-60a0fa88b779. 공식 RuntimeClient를 사용했으며 누락된 Node 환경 대신 상위 팀장이 검증한 코디네이터 자신의 핸들을 명시했다.

| 작업 | 요청/실효 모델·effort | Task | 최종 Dispatch |
|---|---|---|---|
| 책·설계 | gpt-6-astra / gpt-6-astra · high/high | task_65b7403e4039 | ctx_8549e0f29878 |
| 로컬 실행 | gpt-6.1-sol / gpt-6.1-sol · high/high | task_78c4f04f69ae | ctx_5ae430486821 |
| UI | gpt-6.1-sol / gpt-6.1-sol · high/high | task_11c142d3d5b0 | ctx_fe47ba96890e |
| 독자 문서 | gpt-6-luna / gpt-6-luna · medium/medium | task_d733a2b0527b | ctx_cf87455890ff |

처음 세 start는 agent-update-prompt에서 실패했다. 같은 터미널의 준비 화면을 확인하고 retryOf로 재시도했다. retry receipt는 outcome_unknown / turn_start_unobserved 및 모델 null을 그대로 보존했다. 화면에서 실제 unsent draft를 확인한 뒤 bare Enter로 기존 payload만 제출했고 실제 모델·turn start를 읽었다. 중복 payload나 무근거 취소·retry를 하지 않았다. Luna는 ready / observed receipt다. 초기 dependent Luna 계획 Task task_de254ac78edd는 실패한 전달 의존성을 성공으로 위조하지 않고 superseded/failed로 기록한 후, 팀장 지시에 따라 실제 artifact readiness에 기반한 문서 Task로 대체했다.

네 작업자의 worker_done 호출은 runtime_access_denied / EPERM으로 거부되어 **accepted worker_done은 0개**다. 제한된 공식 IPC 승인 옵션이 도구에 없어 Full Access로 바꾸지 않았다. 구현·독립 검증을 계속하고 최종 artifact 수용 뒤 scoped worker-list와 worker-read로 마지막 turn과 activity=done을 확인했다. 설치 recovery-and-cleanup의 명시적 worker-abandon으로 네 Dispatch를 abandoned/failed 처리하고, 각 Task에 전달 실패 이유를 failed로 기록했다. receipt는 processAction=none이며 filesystem/process를 멈추지 않았다. 자동 터미널 보존은 worker-abandon의 문서화된 fallback 동작이며 사용자가 요청한 보존이 아니다. 코디네이터가 지시를 잘못 해석하여 불필요한 worker-retain을 추가 호출했고 receipt는 retained / reason=user_requested / processAction=none을 반환했다. 이 backend reason 필드는 실제 사용자 요청의 근거로 쓰지 않는다. 최초 실패 attempt도 명시적 failed 상태다. reclaimable 작업자는 0개, 코디네이터 inbox는 비어 있다.

제품은 완료됐지만 Orca의 정상 성공 전달은 실패했다. 이 차이를 team-state.json과 실제 receipt에 보존한다. 임의 worker_done, 원시 terminal close, 권한 확대, 자격증명 출력은 하지 않았다. 서버 터미널 term_6f49f418-b86d-4c82-adbf-a16ce447df12는 사용자 검토용으로 실행을 유지하고 코디네이터도 최종 보고 후 idle 한다.

최종 정리: git diff --check exit 0 (줄바꿈 변환 안내만 출력). 완료된 검증 helper term_02c7ba7f-f65b-4ad0-b0d3-7b2a3e23170c만 공식 terminal.close로 종료했고 ptyKilled=true receipt를 저장했다. 웹 서버·작업자·코디네이터는 종료하지 않았다. 최종 team-state.json은 phase=completed이며 전체 회귀 exit 0과 QA 18/18을 구조화해 보존했다.
