# 2026 공정채용 가이드북 첨부 검증 절차

상태: **승인 대기 / 본문 미파싱**
대상: [2026년 공정채용 가이드북 상세 게시물](https://www.ncs.go.kr/blind/rh13/bbs_lib_view.do?libDstinCd=19&libSeq=20260813113144612)

## 현재 확인 범위

- 공식 자료실 목록에서 2026년 게시 항목의 제목, 게시·수정일(`2026-08-13`), 첨부 존재를 확인했다.
- 상세 게시물 식별자는 `libDstinCd=19`, `libSeq=20260813113144612`이다.
- 상세 HTML에서 첨부 다운로드에 사용되는 게시물·파일 식별자가 노출되지만, 이 저장소에서는 첨부 바이너리를 아직 다운로드하거나 파싱하지 않았다.

따라서 현재 `data/guidance/ncs-fair-hiring.yaml`의 2026 출처는 게시물 메타데이터 근거이며, 첨부 본문의 특정 문장·페이지를 직접 인용하는 근거가 아니다.

## 승인 후 1회성 실행 절차

1. 담당자가 공식 상세 게시물과 첨부파일명을 육안 확인하고 다운로드 승인을 기록한다.
2. 원본 파일을 `reports/source_artifacts/` 아래에 저장하되, 저장 파일명에 게시물 식별자와 원래 파일명을 함께 기록한다. 공고 원문이나 지원자 데이터는 같은 디렉터리에 넣지 않는다.
3. `downloaded_at`(KST), 공식 상세 URL, 원래 파일명, 파일 크기, SHA-256을 `reports/ncs-2026-guide-attachment.json`에 기록한다.
4. PDF라면 페이지별 텍스트 추출 결과와 페이지 수를 별도 산출물로 만들고, 추출 실패 페이지·스캔 페이지를 명시한다. 원본을 수정하거나 다시 저장해 해시를 바꾸지 않는다.
5. 카탈로그 통제와 연결할 때는 `control_id`, `page`, `section`, 짧은 근거 요약, `evidence_hash`를 기록한다. 본문에서 직접 확인한 통제만 `ncs-2026-guide`에 매핑한다.
6. 두 번째 사람이 원본 해시와 인용 페이지를 재확인한 후에만 해당 통제의 `evidence_status`를 `attachment_verified`로 올린다.

## 통과·실패 기준

- 통과: 공식 URL·첨부 파일명·다운로드 시각·SHA-256·페이지/섹션 인용이 모두 있고, 원본과 추출본의 해시가 일치하며, 두 번째 사람이 인용을 재현한다.
- 실패: 첨부가 다른 게시물에서 왔거나, 다운로드 URL만 있고 파일 해시가 없거나, 스캔 문서의 OCR 결과만으로 문구를 단정하거나, 페이지 인용 없이 통제를 연결하는 경우.
- 보류: 다운로드 승인·본문 파싱·사람 재검토 중 하나라도 없으면 `ncs-2026-guide`는 게시물 확인 상태로 유지하고 법적 결론이나 확정 통제로 표현하지 않는다.

## 관련 근거

- [NCS 공정채용 가이드 자료실 목록](https://www.ncs.go.kr/blind/rh13/bbs_lib_list.do?libDstinCd=19)
- [현재 확인 기록](../reports/ncs-2026-guide-evidence.md)
## Current mapping guard

Until the attachment body is parsed and page-level evidence is independently
checked, `ncs-2026-guide` is intentionally not listed in any control's
`source_ids`. The catalog still retains the official detail-page record so that
the pending verification work is traceable without presenting metadata as
substantive guidance evidence.
