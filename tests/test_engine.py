from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from core import FairpostEngine, RuleLoadError, load_ruleset
import core.loader as loader_module
from core.loader import _find_ancestor_data_dir


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


FULL_POSTING = """2026년 행정직 채용

자격요건
관련 행정 업무 경력 2년 이상
용모 단정한 20대 지원자 우대

전형절차
서류전형 후 면접전형
평가 기준은 직무경력과 문제해결 사례이며 우대사항은 서류전형 가점으로 반영

일정
접수 기간: 2026. 8. 1. ~ 8. 12.
면접 일정: 2026. 8. 20.
결과는 이메일로 개별 통보 예정

근무조건
연봉 4,000만원

문의처
인사팀 02-1234-5678 recruit@example.com
"""


def serialized(engine: FairpostEngine, text: str) -> bytes:
    return json.dumps(
        engine.check(text).to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_deterministic_for_100_runs() -> None:
    engine = FairpostEngine()
    expected = serialized(engine, FULL_POSTING)
    assert all(serialized(engine, FULL_POSTING) == expected for _ in range(100))


def test_runs_with_network_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def denied(*_args, **_kwargs):
        raise AssertionError("runtime network access")

    monkeypatch.setattr(socket, "create_connection", denied)
    result = FairpostEngine().check(FULL_POSTING)
    assert result.ruleset_version
    assert result.counts["questions"] >= 1


def test_youth_intern_exclusion_and_later_valid_match() -> None:
    engine = FairpostEngine()
    excluded = engine.check("청년인턴 채용")
    assert "AGE-001" not in {finding.id for finding in excluded.findings}

    mixed = engine.check("청년인턴 채용 안내입니다.\n자격요건\n젊은 지원자 우대")
    assert "AGE-001" in {finding.id for finding in mixed.findings}


def test_normalized_youth_intern_exclusion_is_not_flagged() -> None:
    result = FairpostEngine().check("２０대 청년\u200b인턴 채용")
    assert "AGE-001" not in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "phrase",
    [
        "연령 제한없음",
        "연령 제한 없습니다",
        "연령 제한이 없다",
        "나이 제한없음",
        "나이 제한 없습니다",
        "나이 제한이 없다",
        "나이 제한 없이 지원 가능합니다",
        "나이 제한 없슴",
    ],
)
def test_no_age_limit_phrase_is_not_flagged(phrase: str) -> None:
    result = FairpostEngine().check(f"응시자격\n{phrase}")
    assert "AGE-002" not in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "phrase",
    ["나이 제한 없지 않습니다", "연령 제한 없는 것은 아닙니다"],
)
def test_double_negative_age_limit_is_not_hidden(phrase: str) -> None:
    result = FairpostEngine().check(f"응시자격\n{phrase}")
    assert "AGE-002" in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "최종합격자 중 만 60세 이상인 자는 시니어인턴십 참여신청서를 제출합니다.",
        "노인일자리 및 사회활동지원사업 참여와 관련하여 최종합격자 중 60세 이상은 별도 동의서를 제출합니다.",
    ],
)
def test_senior_employment_support_after_final_selection_is_not_age_restriction(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "AGE-002" not in {finding.id for finding in result.findings}


def test_plain_age_minimum_without_support_program_still_triggers() -> None:
    result = FairpostEngine().check("지원자격\n만 60세 이상인 자만 지원할 수 있습니다.")
    assert "AGE-002" in {finding.id for finding in result.findings}


def test_korean_ending_normalization_preserves_original_offsets() -> None:
    text = "자격요건\n남성에 한합니다."
    result = FairpostEngine().check(text)
    finding = next(item for item in result.findings if item.id == "SEX-001")
    assert finding.matched_text == "남성에 한합니다"
    assert text[finding.offset[0] : finding.offset[1]] == finding.matched_text


@pytest.mark.parametrize("text", ["성별: 여성", "모집 성별\n남성"])
def test_structured_gender_requirement_is_flagged(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "SEX-001" in {finding.id for finding in result.findings}


@pytest.mark.parametrize("text", ["성별무관", "성별: 제한 없음"])
def test_gender_without_restriction_is_not_flagged(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "SEX-001" not in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "text, expected_text, rule_id",
    [
        ("자격요건\n２０대 지원자 우대", "２０대", "AGE-001"),
        ("자격요건\n남성에 한합\u200b니다.", "남성에 한합\u200b니다", "SEX-001"),
        ("전형절차\nＡＩ가 최종 결정", "ＡＩ가 최종 결정", "AI-001"),
    ],
)
def test_nfkc_and_zero_width_normalization_preserves_source_offsets(
    text: str,
    expected_text: str,
    rule_id: str,
) -> None:
    result = FairpostEngine().check(text)
    finding = next(item for item in result.findings if item.id == rule_id)
    assert finding.matched_text == expected_text
    assert finding.offset is not None
    assert text[finding.offset[0] : finding.offset[1]] == expected_text


@pytest.mark.parametrize(
    ("text", "expected_text", "rule_id"),
    [
        ("젊고 역동적인 지원자를 우대합니다.", "젊고", "AGE-001"),
        ("1995년 이후 출생자만 지원할 수 있습니다.", "1995년 이후 출생자만", "AGE-002"),
        ("외모 단정한 분을 찾습니다.", "외모 단정", "LOOK-001"),
    ],
)
def test_claude_mcp_review_variants_preserve_evidence_offsets(
    text: str,
    expected_text: str,
    rule_id: str,
) -> None:
    result = FairpostEngine().check(text)
    finding = next(item for item in result.findings if item.id == rule_id)

    assert finding.matched_text == expected_text
    assert finding.offset is not None
    assert text[finding.offset[0] : finding.offset[1]] == expected_text


def test_korean_ending_normalization_retrieves_question_card() -> None:
    result = FairpostEngine().check("책임감 있으신 분을 찾습니다.")
    assert "Q-INTER-001" in {question.id for question in result.questions}


def test_missing_appeal_process_is_reported_as_unconfirmed() -> None:
    result = FairpostEngine().check(
        "전형절차\n서류전형 후 면접을 진행합니다.\n"
        "결과는 이메일로 안내합니다.\n문의처\n인사팀에 문의하십시오."
    )
    appeal = next(slot for slot in result.slots if slot.slot == "appeal_channel")
    question = next(
        item for item in result.questions if item.id == "Q-INFO-001"
    )

    assert appeal.found is False
    assert question.question == "결과에 의문이 생긴 지원자는 어디로 연락합니까?"
    assert "없습니다" not in result.disclaimer


def test_named_appeal_channel_and_deadline_satisfy_appeal_slot() -> None:
    result = FairpostEngine().check(
        "유의사항\n결과 통보 후 7일 이내 인사팀 이메일로 "
        "이의신청할 수 있습니다."
    )
    appeal = next(slot for slot in result.slots if slot.slot == "appeal_channel")

    assert appeal.found is True
    assert set(appeal.components_found) >= {"channel_named", "has_deadline"}
    assert "Q-INFO-001" not in {question.id for question in result.questions}


@pytest.mark.parametrize(
    ("slot_id", "question_id", "present_text"),
    [
        (
            "appeal_channel",
            "Q-INFO-001",
            "유의사항\n결과 통보 후 인사팀으로 이의신청할 수 있습니다.",
        ),
        (
            "document_return",
            "Q-INFO-004",
            "제출서류\n채용서류 반환 청구 기간과 파기 시점을 안내합니다.",
        ),
        (
            "evaluation_criteria",
            "Q-PROC-002",
            "전형절차\n평가 기준과 평가 항목별 배점을 안내합니다.",
        ),
    ],
)
def test_slot_detail_question_exactly_tracks_slot_absence(
    slot_id: str,
    question_id: str,
    present_text: str,
) -> None:
    engine = FairpostEngine()
    missing = engine.check("2026년 사무직 채용 공고")
    missing_slot = next(slot for slot in missing.slots if slot.slot == slot_id)
    missing_question = next(
        question for question in missing.questions if question.id == question_id
    )

    assert missing_slot.found is False
    assert missing_question.matched_text is None
    assert missing_question.offset is None
    assert missing_question.section is None

    present = engine.check(present_text)
    present_slot = next(slot for slot in present.slots if slot.slot == slot_id)
    assert present_slot.found is True
    assert question_id not in {question.id for question in present.questions}


def test_contact_point_and_appeal_process_are_independent() -> None:
    engine = FairpostEngine()
    contact_only = engine.check(
        "문의처\n인사팀 02-1234-5678 recruit@example.com"
    )
    contact_slots = {slot.slot: slot.found for slot in contact_only.slots}
    contact_questions = {question.id for question in contact_only.questions}
    assert contact_slots["contact_point"] is True
    assert contact_slots["appeal_channel"] is False
    assert "Q-INFO-003" not in contact_questions
    assert "Q-INFO-001" in contact_questions

    appeal_only = engine.check(
        "유의사항\n결과 통보 후 7일 이내 온라인으로 이의신청할 수 있습니다."
    )
    appeal_slots = {slot.slot: slot.found for slot in appeal_only.slots}
    appeal_questions = {question.id for question in appeal_only.questions}
    assert appeal_slots["appeal_channel"] is True
    assert appeal_slots["contact_point"] is False
    assert "Q-INFO-001" not in appeal_questions
    assert "Q-INFO-003" in appeal_questions


def test_slot_evidence_is_bounded_to_the_matching_sentence() -> None:
    text = (
        "지원자격은 별도로 안내합니다. "
        "급여는 면접 후 협의합니다. "
        "개인정보 보관기간은 추후 안내합니다."
    )
    result = FairpostEngine().check(text)
    compensation = next(
        slot for slot in result.slots if slot.slot == "compensation"
    )

    assert compensation.evidence == "급여는 면접 후 협의합니다."
    assert "지원자격" not in compensation.evidence
    assert "개인정보" not in compensation.evidence


def test_long_slot_evidence_keeps_the_match_in_a_bounded_window() -> None:
    text = ("😀" * 180) + " 급여는 협의합니다 " + ("가" * 180)
    result = FairpostEngine().check(text)
    compensation = next(
        slot for slot in result.slots if slot.slot == "compensation"
    )

    assert compensation.evidence is not None
    assert "급여" in compensation.evidence
    assert len(compensation.evidence) <= 240
    assert compensation.evidence.startswith("…")
    assert compensation.evidence.endswith("…")


def test_book_based_questions_cover_full_ai_hiring_review() -> None:
    result = FairpostEngine().check(
        "AI 채용 안내\nAI 영상면접과 AI 역량검사 결과를 자동 평가합니다."
    )
    question_ids = {question.id for question in result.questions}
    assert {
        "Q-PROC-004",
        "Q-INFO-009",
        "Q-PROC-010",
        "Q-PROC-014",
        "Q-INTER-003",
        "Q-INTER-004",
        "Q-DIST-008",
        "Q-DIST-009",
        "Q-DIST-011",
    } <= question_ids


def test_ai_hiring_questions_preserve_both_report_sources() -> None:
    rules = {
        rule["id"]: rule
        for rule in load_ruleset().rules
        if rule["layer"] == "question"
    }
    report_titles = {
        rules[rule_id]["basis"]["title"]
        for rule_id in {
            "Q-PROC-004",
            "Q-INFO-009",
            "Q-PROC-010",
            "Q-PROC-014",
            "Q-INTER-003",
            "Q-INTER-004",
            "Q-DIST-008",
            "Q-DIST-009",
            "Q-DIST-011",
        }
    }
    assert report_titles == {
        "인공지능 채용 가이드라인(안) 개발",
        "채용분야 인공지능(AI) 활용실태 및 공정성 확보방안 연구",
    }


def test_book_based_baseline_questions_apply_to_regular_posting() -> None:
    result = FairpostEngine().check("경력직 채용 공고")
    questions_by_id = {question.id: question for question in result.questions}
    common_ids = {
        question.id
        for question in result.questions
        if question.review_scope == "common"
    }
    assert {
        "Q-INFO-005",
        "Q-INFO-007",
        "Q-INFO-008",
        "Q-INTER-005",
        "Q-INTER-006",
        "Q-PROC-009",
        "Q-PROC-013",
    } == common_ids
    assert questions_by_id["Q-PROC-002"].review_scope == "posting"
    assert result.counts["questions"] == len(result.questions)


@pytest.mark.parametrize(
    ("text", "expected_id"),
    [
        ("지원자격\n세례교인에 한함", "Q-DIST-012"),
        ("자격요건\n범죄경력이 없는 사람", "Q-DIST-013"),
        ("제출서류\n입사지원서 양식", "Q-INFO-010"),
        ("제출서류\n졸업증명서와 경력증명서 제출", "Q-INFO-011"),
        ("제출서류\n주민등록초본 제출", "Q-INFO-011"),
        ("최종 합격자 제출서류\n기본증명서 1부", "Q-INFO-011"),
        ("전형절차\n면접위원이 면접전형을 진행합니다.", "Q-PROC-011"),
        ("전형절차\n필기시험 후 면접", "Q-PROC-012"),
        ("2026년 사무직 채용 공고", "Q-PROC-013"),
    ],
)
def test_ncs_fair_hiring_gap_questions_are_retrievable(
    text: str,
    expected_id: str,
) -> None:
    result = FairpostEngine().check(text)
    assert expected_id in {question.id for question in result.questions}


def test_social_status_question_does_not_treat_job_type_as_applicant_status() -> None:
    result = FairpostEngine().check("고용형태\n비정규직(기간제) 채용")
    assert "Q-DIST-013" not in {question.id for question in result.questions}


@pytest.mark.parametrize(
    ("text", "question_id"),
    [
        ("지원자격\n학력 제한 없음", "Q-DIST-005"),
        ("블라인드 안내\n출신학교 기재 금지", "Q-DIST-005"),
        (
            "자기소개서에 출신학교, 가족관계 등 인적사항 관련 내용은 "
            "일체 기재 금지",
            "Q-DIST-005",
        ),
        ("지원자격\n지역제한 없음", "Q-DIST-006"),
        ("지원서에 종교 미기재", "Q-DIST-012"),
        (
            "지원서에 종교, 추천인, 주민등록번호 등 인적사항을 "
            "일체 기재하지 말아주세요",
            "Q-DIST-012",
        ),
        ("안내사항\n증빙서류 제출 불필요", "Q-INFO-011"),
        ("안내사항\n졸업증명서는 제출하지 않습니다.", "Q-INFO-011"),
        ("안내사항\n주민등록초본은 제출하지 않습니다.", "Q-INFO-011"),
        ("안내사항\n기본증명서 제출 불필요", "Q-INFO-011"),
        ("지원자격\n운전면허증 소지자", "Q-INFO-011"),
        ("직무분류\n사회복지.종교", "Q-DIST-012"),
        ("우대사항\n종교 관련 학과 졸업자 우대", "Q-DIST-012"),
        ("우대사항\n종교학 전공자 우대", "Q-DIST-012"),
        ("기관은 지역사회 발전과 혁신을 추구합니다.", "Q-DIST-013"),
        ("지원자격\n북한이탈주민 지원 가능", "Q-DIST-013"),
        ("지원자격\n북한이탈주민도 지원할 수 있습니다", "Q-DIST-013"),
        ("지원자격\n탈북자 지원 환영", "Q-DIST-013"),
        ("우대사항\n북한이탈주민 가점", "Q-DIST-013"),
        (
            "우대조건\n취업지원대상자, 장애인, 북한이탈주민, 다문화가족",
            "Q-DIST-013",
        ),
        ("급여기준\n면접 후 결정", "Q-PROC-011"),
        ("임금은 면접시 별도 협의합니다.", "Q-PROC-011"),
        ("경력자 급여는 면접시 별도합의", "Q-PROC-011"),
    ],
)
def test_protective_or_unrelated_context_does_not_retrieve_question(
    text: str,
    question_id: str,
) -> None:
    result = FairpostEngine().check(text)
    assert question_id not in {question.id for question in result.questions}


@pytest.mark.parametrize(
    ("text", "question_id"),
    [
        ("지원자격\n대졸 이상", "Q-DIST-005"),
        ("지원자격\n해당 지역 거주자", "Q-DIST-006"),
        ("지원자격\n세례교인에 한함", "Q-DIST-012"),
        ("지원자격\n전과자는 지원할 수 없음", "Q-DIST-013"),
        ("지원자격\n북한이탈주민 제외", "Q-DIST-013"),
        ("지원자격\n북한이탈주민 제외\n우대사항\n장애인 우대", "Q-DIST-013"),
        ("전형절차\n서류전형 후 면접을 실시합니다.", "Q-PROC-011"),
    ],
)
def test_restrictive_or_relevant_context_still_retrieves_question(
    text: str,
    question_id: str,
) -> None:
    result = FairpostEngine().check(text)
    assert question_id in {question.id for question in result.questions}


def test_religion_question_respects_explicit_no_restriction_phrase() -> None:
    result = FairpostEngine().check("지원자격\n종교 무관")
    assert "Q-DIST-012" not in {question.id for question in result.questions}


def test_question_includes_trigger_evidence_and_original_offset() -> None:
    text = "채용 공고\r\n지원자격\r\n세례교인에 한함"
    result = FairpostEngine().check(text)
    question = next(item for item in result.questions if item.id == "Q-DIST-012")
    assert "세례교인" in question.matched_text
    assert question.section == "자격요건"
    assert question.offset is not None
    start, end = question.offset
    assert text[start:end] == question.matched_text
    payload = result.to_dict()
    payload_question = next(
        item for item in payload["questions"] if item["id"] == "Q-DIST-012"
    )
    assert payload_question["offset"] is not None
    assert payload_question["review_scope"] == "posting"
    assert "세례교인" in question.matched_text
    assert payload_question["reference"]["publisher"] == "국가인권위원회"
    assert payload_question["reference"]["year"] == 2020


@pytest.mark.parametrize(
    "text",
    [
        "근무지: 지역 교회 부설 복지센터",
        "담당업무: 성당 시설 관리",
        "서비스 대상: 사찰 방문객 안내",
    ],
)
def test_religious_workplace_or_beneficiary_does_not_trigger_religion_review(
    text: str,
) -> None:
    result = FairpostEngine().check(text)

    assert "Q-DIST-012" not in {question.id for question in result.questions}


def test_proof_document_question_has_current_ncs_reference_and_offset() -> None:
    text = "제출서류\r\n졸업증명서와 경력증명서를 제출합니다."
    result = FairpostEngine().check(text)
    question = next(item for item in result.questions if item.id == "Q-INFO-011")

    assert question.matched_text == "졸업증명서"
    assert question.section == "제출서류"
    assert question.offset is not None
    start, end = question.offset
    assert text[start:end] == question.matched_text
    assert question.reference.source_url == (
        "https://www.ncs.go.kr/blind/rh13/bbs_lib_view.do"
        "?libDstinCd=07&libSeq=20250424140840743"
    )
    assert question.reference.accessed_at == "2026-07-29"
    assert question.reference.publisher == "한국산업인력공단"
    assert question.reference.year == 2025
    assert question.reference.pages == [7]
    assert question.reference.sections == [
        "입증자료 제출 안내 시 유의사항 (PDF 7쪽)"
    ]


def test_multi_track_application_question_is_bounded_and_traceable() -> None:
    text = (
        "📌 채용공고\r\n"
        "근무분야\r\n"
        "행정직, 연구직, 기술직\r\n"
        "학력정보\r\n"
        "학력무관, 대졸(4년), 석사\r\n"
        + ("가" * 4000)
        + "\r\n입사\u200b지원서"
    )
    result = FairpostEngine().check(text)
    question = next(item for item in result.questions if item.id == "Q-INFO-012")

    assert question.matched_text == "근무분야\r\n행정직, 연구직, 기술직"
    assert question.offset is not None
    start, end = question.offset
    assert text[start:end] == question.matched_text
    assert "Q-INFO-012" not in {item.id for item in result.findings}
    assert question.reference.title == (
        "2025년 공공기관 공정채용 모니터링 주요 위반 사례(일부 문구 수정)"
    )
    assert question.reference.publisher == "한국산업인력공단"
    assert question.reference.year == 2025
    assert question.reference.pages == [11]
    assert question.reference.accessed_at == "2026-07-29"
    assert question.reference.sections == [
        "다수 직렬 채용 시 유의사항 (PDF 11쪽)"
    ]


def test_language_score_requirement_question_is_bounded_and_traceable() -> None:
    text = "지원자격\n다음 요건을 모두 충족한 자\nTOEIC 750점 이상"
    result = FairpostEngine().check(text)
    question = next(item for item in result.questions if item.id == "Q-DIST-014")

    assert question.matched_text == "TOEIC 750점 이상"
    assert question.offset is not None
    start, end = question.offset
    assert text[start:end] == question.matched_text
    assert "Q-DIST-014" not in {item.id for item in result.findings}
    assert question.reference.publisher == "한국산업인력공단"
    assert question.reference.year == 2025
    assert question.reference.pages == [12]
    assert question.reference.accessed_at == "2026-07-29"
    assert question.reference.sections == ["지원자격 예외사례 (PDF 12쪽)"]


def test_gender_preference_question_is_review_only_and_traceable() -> None:
    text = "우대사항\n여성 우대"
    result = FairpostEngine().check(text)
    question = next(item for item in result.questions if item.id == "Q-DIST-015")

    assert question.matched_text == "여성 우대"
    assert question.offset is not None
    start, end = question.offset
    assert text[start:end] == question.matched_text
    assert "Q-DIST-015" not in {item.id for item in result.findings}
    assert question.reference.publisher == "국가인권위원회"
    assert question.reference.year == 2007
    assert question.reference.accessed_at == "2026-08-03"


@pytest.mark.parametrize(
    "text",
    [
        "지원자격\n성별 무관, 여성도 지원 가능",
        "협력대상\n여성기업 우선구매",
    ],
)
def test_gender_preference_question_protects_inclusive_or_policy_context(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-015" not in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "모집대상\n여성 참여자 우대",
        "우대사항\n여성 지원자 우대",
        "채용조건\n남성 인력 선호",
        "우대사항\n경력단절 여성 우대",
        "지원자격\n성별 무관이나 여성 우대",
    ],
)
def test_gender_preference_question_covers_observed_private_variants(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-015" in {item.id for item in result.questions}


def test_return_rule_covers_observed_private_posting_wording() -> None:
    text = "유의사항\n제출된 서류는 일체 반환하지 않습니다."
    result = FairpostEngine().check(text)
    finding = next(item for item in result.findings if item.id == "RETURN-001")

    assert finding.matched_text == "제출된 서류는 일체 반환하지 않습니다"
    assert finding.offset is not None
    start, end = finding.offset
    assert text[start:end] == finding.matched_text


def test_return_rule_does_not_treat_return_process_as_blanket_refusal() -> None:
    result = FairpostEngine().check(
        "유의사항\n전자우편으로 제출한 서류는 반환 대상이 아닙니다. "
        "종이 채용서류는 반환 청구할 수 있습니다."
    )
    assert "RETURN-001" not in {item.id for item in result.findings}


def test_electronic_return_exception_uses_review_question_not_law_finding() -> None:
    result = FairpostEngine().check(
        "접수방법\n이메일 제출\n유의사항\n"
        "제출된 서류는 반환하지 않습니다. 종이 채용서류는 반환 청구 가능합니다."
    )
    assert "RETURN-001" not in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_unrelated_email_channel_does_not_hide_blanket_return_refusal() -> None:
    result = FairpostEngine().check(
        "문의방법\n이메일로 문의 가능\n유의사항\n"
        "제출된 서류는 일체 반환하지 않습니다."
    )
    assert "RETURN-001" in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_formal_blanket_return_refusal_variant_is_law_and_review() -> None:
    result = FairpostEngine().check("제출된 채용서류는 반환하지 아니합니다.")
    assert "RETURN-001" in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_electronic_application_return_unavailable_is_review_only() -> None:
    result = FairpostEngine().check(
        "전자적으로 제출된 입사지원서는 반환이 불가합니다."
    )
    assert "RETURN-001" not in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "이메일로 접수합니다. 제출된 서류는 반환하지 않으며 개인정보를 보호합니다.",
        "온라인으로 제출한 지원서류는 반환하지 아니하며 별도 파기합니다.",
    ],
)
def test_nonreturn_conjunctive_variant_with_electronic_submission_is_review_only(
    text: str,
) -> None:
    result = FairpostEngine().check(text)

    assert "RETURN-001" not in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_nonreturn_conjunctive_variant_for_required_original_stays_finding() -> None:
    result = FairpostEngine().check(
        "온라인 지원 후 졸업증명서 원본을 방문 제출합니다. "
        "제출된 서류는 반환하지 않으며 별도 파기합니다."
    )

    assert "RETURN-001" in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_structured_email_only_submission_with_distant_nonreturn_is_review_only() -> None:
    result = FairpostEngine().check(
        "지원 방법\n이메일 접수\n접수 기간은 채용 시까지입니다.\n"
        "전형 절차\n서류전형, 면접, 최종 합격\n"
        "기타\n제출된 서류는 반환하지 않으며 개인정보를 보호합니다."
    )

    assert "RETURN-001" not in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_mixed_email_and_visit_submission_does_not_use_email_only_exclusion() -> None:
    result = FairpostEngine().check(
        "접수 방법\n이메일 또는 방문 접수\n"
        "제출된 서류는 반환하지 않으며 채용 종료 후 파기합니다."
    )

    assert "RETURN-001" in {item.id for item in result.findings}
    assert "Q-INFO-013" in {item.id for item in result.questions}


def test_photo_attachment_variant_and_protective_context() -> None:
    positive = FairpostEngine().check("제출서류\n이력서 사진 첨부")
    negative = FairpostEngine().check("블라인드 지원서에는 사진 부착이 불필요합니다")

    assert "PHOTO-001" in {item.id for item in positive.findings}
    assert "PHOTO-001" not in {item.id for item in negative.findings}


@pytest.mark.parametrize(
    "text",
    [
        "전형결과\n불합격자는 별도 통보하지 않습니다.",
        "안내사항\n미합격자에게는 개별 안내가 없습니다.",
        "합격자에 한해 개별 통보합니다.",
        "서류 미비 시 별도 통지 없이 불합격 처리할 수 있습니다.",
    ],
)
def test_nonpass_notice_gap_uses_review_question(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "Q-INFO-014" in {item.id for item in result.questions}
    assert "Q-INFO-014" not in {item.id for item in result.findings}


def test_nonpass_notice_question_cites_statutory_scope_and_notification_duty() -> None:
    result = FairpostEngine().check("합격자에 한해 개별 통보합니다.")
    question = next(item for item in result.questions if item.id == "Q-INFO-014")

    assert question.basis_type == "research"
    assert question.reference is not None
    assert question.reference.publisher == "국가법령정보센터"
    assert question.reference.sections == ["제3조 적용범위", "제10조 채용 여부의 고지"]
    assert "상시 30명 이상" in question.question
    assert any("별도 통지" in item for item in question.follow_up)


def test_all_applicants_result_notice_does_not_trigger_nonpass_gap() -> None:
    result = FairpostEngine().check(
        "전형결과\n합격자와 불합격자 모두에게 이메일로 개별 통보합니다."
    )
    assert "Q-INFO-014" not in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "지원자격\n외국인 지원 불가",
        "모집대상\n대한민국 국적자만 지원할 수 있습니다.",
        "지원 제한\n대한민국 국적이 아닌 자",
    ],
)
def test_nationality_or_foreigner_exclusion_uses_review_question(text: str) -> None:
    result = FairpostEngine().check(text)

    assert "Q-DIST-016" in {item.id for item in result.questions}
    assert "Q-DIST-016" not in {item.id for item in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "지원자격\n외국인 지원 가능",
        "지원자격\n국적 무관, 취업 가능한 체류자격 또는 비자 소지자",
        "담당업무\n외국인 고객 상담 및 통번역",
    ],
)
def test_inclusive_or_work_authorization_context_does_not_trigger_nationality_review(
    text: str,
) -> None:
    result = FairpostEngine().check(text)

    assert "Q-DIST-016" not in {item.id for item in result.questions}


def test_criminal_record_review_question_cites_current_human_rights_decision() -> None:
    result = FairpostEngine().check("지원자격\n신원조회 결과 범죄경력이 없는 자")
    question = next(item for item in result.questions if item.id == "Q-DIST-013")

    assert question.reference is not None
    assert question.reference.publisher == "국가인권위원회"
    assert "실효된 범죄경력" in str(question.reference.title)
    assert question.reference.year == 2025
    assert "Q-DIST-013" not in {item.id for item in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "지원자격: 장애인 지원 불가",
        "지원 제한: 장애인 지원이 어렵습니다.",
        "장애인은 지원할 수 없습니다.",
    ],
)
def test_direct_disability_application_exclusion_is_flagged(text: str) -> None:
    result = FairpostEngine().check(text)

    assert "DISABILITY-001" in {item.id for item in result.findings}


def test_disability_inclusive_context_is_not_flagged() -> None:
    for text in (
        "장애인 지원이 어렵지 않으며 필요한 합리적 편의를 제공합니다.",
        "장애가 없는 사람도 지원 가능합니다.",
        "장애가 없는 사람과 장애인 모두 지원할 수 있습니다.",
        "장애가 없어야 한다는 제한은 없습니다.",
    ):
        result = FairpostEngine().check(text)
        assert "DISABILITY-001" not in {item.id for item in result.findings}


def test_health_requirement_question_cites_current_disability_hiring_decision() -> None:
    result = FairpostEngine().check("지원자격: 신체 건강한 자")
    question = next(item for item in result.questions if item.id == "Q-DIST-007")

    assert question.reference is not None
    assert question.reference.publisher == "국가인권위원회"
    assert "업무 수행 능력 예단" in str(question.reference.title)
    assert question.reference.year == 2026


@pytest.mark.parametrize(
    "text",
    [
        "입사지원서에 병력 기재를 요구합니다.",
        "지원자는 과거 병력을 제출해야 합니다.",
        "지원서 항목: 질병 이력",
        "지원서 항목: 치료 이력",
    ],
)
def test_health_history_collection_remains_a_finding_candidate(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "HEALTH-001" in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "지원 단계에서는 건강정보를 제출받지 않습니다.",
        "최종 합격 후 배치 전 건강검진을 실시합니다.",
        "작업에 필요한 보호구를 지급하고 합리적 편의를 제공합니다.",
        "블라인드 채용을 위해 과거 병력은 기재하지 마세요.",
        "질병 이력 및 치료 이력은 수집 금지입니다.",
        "과거\u200b 병력은\u200b 기재하지\u200b 마세요.",
        "질병\u200b 이력\u200b 및\u200b 치료\u200b 이력은\u200b 작성하지\u200b 마세요.",
    ],
)
def test_health_protective_or_post_selection_context_is_not_flagged(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "HEALTH-001" not in {finding.id for finding in result.findings}


def test_health_protection_does_not_hide_later_collection_candidate() -> None:
    result = FairpostEngine().check(
        "과거 병력은 기재하지 마세요. 다만 치료 이력은 지원서에 제출해야 합니다."
    )
    assert "HEALTH-001" in {finding.id for finding in result.findings}


def test_later_health_protection_does_not_hide_earlier_collection_candidate() -> None:
    result = FairpostEngine().check(
        "지원서에 치료 이력을 제출해야 합니다. 단, 과거 병력은 기재하지 마세요."
    )
    assert "HEALTH-001" in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "AI가 최종 결정하지 않으며 채용담당자가 최종 판단합니다.",
        "인공지능이 최종 결정을 내리지 않고 사람 검토를 거칩니다.",
        "AI 자동 탈락은 없습니다.",
        "자동 평가로 결정하지 않고 참고자료로만 활용합니다.",
        "ＡＩ가 최종 결정하지 않으며 사람이 판단합니다.",
        "AI\u200b 자동\u200b 탈락은\u200b 없습니다.",
    ],
)
def test_ai_protective_human_decision_context_is_not_flagged(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "AI-001" not in {finding.id for finding in result.findings}


def test_ai_protection_does_not_hide_later_automated_decision_candidate() -> None:
    result = FairpostEngine().check(
        "AI가 최종 결정하지 않고 사람이 판단합니다. "
        "다만 1차 전형은 AI 자동 탈락을 적용합니다."
    )
    assert "AI-001" in {finding.id for finding in result.findings}


def test_later_ai_protection_does_not_hide_earlier_automated_candidate() -> None:
    result = FairpostEngine().check(
        "1차는 AI 자동 탈락을 적용합니다. 단, AI가 최종 결정하지 않고 "
        "최종은 사람이 판단합니다."
    )
    assert "AI-001" in {finding.id for finding in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "입사지원서 항목: 혼인 여부",
        "면접 질문: 결혼 예정 시기",
        "지원서에 임신 여부와 출산 계획을 작성해 주세요.",
        "기타사항: 자녀 유무",
    ],
)
def test_marital_pregnancy_or_child_screening_uses_review_question(text: str) -> None:
    result = FairpostEngine().check(text)

    assert "Q-DIST-017" in {item.id for item in result.questions}
    assert "Q-DIST-017" not in {item.id for item in result.findings}


def test_marital_information_prohibition_is_protected() -> None:
    for text in (
        "블라인드 안내: 혼인 여부, 가족관계, 재산은 지원서에 기재하지 마세요.",
        "블라인드 안내: 자녀 유무는 지원서에 기재하지 마세요.",
        "블라인드 안내: 임신 여부는 기재하지 마세요.",
        "출산 계획은 지원서에 작성하지 않습니다.",
    ):
        result = FairpostEngine().check(text)
        assert "Q-DIST-017" not in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "지원자격\n안전보호구 착용 가능자",
        "작업조건\n방진마스크 착용 필수인 분",
        "지원자격\n4조 3교대 근무 및 방진복 착용 가능한 자",
        "지원자격\n방진모 착용 가능자",
    ],
)
def test_ppe_requirement_uses_job_necessity_review_question(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-007" in {item.id for item in result.questions}


def test_ppe_provision_is_not_a_health_requirement() -> None:
    for text in (
        "복리후생\n안전모와 안전화를 지급합니다.",
        "작업환경\n방진복 착용 업무이며 회사에서 방진복을 지급합니다.",
    ):
        result = FairpostEngine().check(text)
        assert "Q-DIST-007" not in {item.id for item in result.questions}


def test_family_certificate_in_application_documents_is_review_only() -> None:
    result = FairpostEngine().check(
        "입사 지원 시 지원 서류로 가족관계증명서를 제출해야 합니다."
    )
    assert "FAMILY-001" not in {item.id for item in result.findings}
    assert "Q-INFO-011" in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "최종 합격자 제출서류: 가족관계증명서 1부",
        "임용일 제출서류로 가족관계증명서를 준비합니다.",
        "다문화가족 가점 증빙은 가족관계증명서로 확인합니다.",
    ],
)
def test_family_certificate_outside_initial_screening_is_not_auto_finding(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "FAMILY-001" not in {item.id for item in result.findings}
    assert "Q-INFO-011" in {item.id for item in result.questions}


def test_family_job_information_remains_a_law_finding_candidate() -> None:
    result = FairpostEngine().check("입사지원서에 부모의 직업을 기재해 주세요.")
    assert "FAMILY-001" in {item.id for item in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "입사지원서에 형제자매의 학력을 기재해 주세요.",
        "형제자매의 학력을 기재하지 않는 것은 허용되지 않습니다.",
    ],
)
def test_sibling_education_request_remains_a_law_finding_candidate(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "SCHOOL-001" in {item.id for item in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "형제자매의 학력, 직업, 재산내용을 일체 기재하지 말아주세요.",
        "개인정보 보호를 위해 형제자매의 학력은 기재받지 않습니다.",
        "지원서에는 형제자매의 학력 기재 금지",
    ],
)
def test_sibling_education_protective_notice_is_not_a_law_finding(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "SCHOOL-001" not in {item.id for item in result.findings}


@pytest.mark.parametrize(
    "text",
    [
        "채용제목\n여성직원 구인합니다.",
        "채용제목\n생산직 남성분1명 여성분2명 모집",
        "우대조건\n여성, 차량소지자",
        "우대조건\n남성 50대 미만",
        "직무내용\n여성: 검사업무 및 세정업무 남성: 폐수처리업무",
    ],
)
def test_gender_recruitment_variants_route_to_human_review(text: str) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-015" in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "여성 지적장애인 시설입니다.(여성30명) 생활지도원을 모집합니다.",
        "3등급 여자 어르신 방문요양보호사 구인",
        "여성도 쉽게 할 수 있는 포장 업무입니다.",
    ],
)
def test_client_gender_or_inclusive_context_is_not_gender_recruitment_review(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-015" not in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "requirement",
    [
        "New TEPS 268점 이상",
        "TOEFL(iBT) 85점 이상",
        "HSK 5급 이상",
    ],
)
def test_language_score_requirement_question_covers_common_score_formats(
    requirement: str,
) -> None:
    result = FairpostEngine().check(f"응시자격\n{requirement}")
    assert "Q-DIST-014" in {item.id for item in result.questions}


def test_language_score_requirement_still_checks_proportionality_for_language_work() -> None:
    result = FairpostEngine().check(
        "채용분야\n일반사무, 해외영업\n지원자격\nTOEIC 750점 이상"
    )
    assert "Q-DIST-014" in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "복리후생\nTOEIC 750점 이상 어학수당 지급",
        "우대사항\nTOEIC 750점 이상 어학수당\n제출서류 제출 필수",
    ],
)
def test_language_score_requirement_question_protects_nonqualification_or_job_context(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-014" not in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        (
            "근무분야\n연구직\n학력정보\n석사\n"
            "행정직 6급 채용 시에 한함\n입사지원서"
        ),
        "근무분야\n행정직, 연구직\n입사지원서",
        (
            "근무분야\n행정직, 연구직\n학력정보\n학력무관\n"
            "입사지원서"
        ),
        (
            "근무분야\n행정직, 연구직\n학력사항\n"
            + ("가" * 5100)
            + "\n입사지원서"
        ),
        (
            "근무분야\n행정직, 연구직\n학력사항\n"
            "직렬별로 각각 별도 입사지원서를 사용"
        ),
        (
            "근무분야\n행정직, 연구직\n"
            "학위 정보는 연구직에 한함\n입사지원서"
        ),
        "근무분야\n행정직, 연구직\n학력사항: 무관\n입사지원서",
        "근무분야\n행정직, 연구직\n최종 학력: 무관\n입사지원서",
        "근무분야\n행정직, 연구직\n학력사항: 제한 없음\n입사지원서",
        "근무분야\n행정직, 연구직\n졸업증명서: 해당 없음\n입사지원서",
        "근무분야\n행정직, 연구직\n학력사항: 학력 무관.\n입사지원서",
        "근무분야\n행정직, 연구직\n전공명: 전공무관\n입사지원서",
        "근무분야\n행정직, 연구직\n학력사항\n무관\n입사지원서",
        "근무분야\n행정직, 연구직\n최종 학력\n제한 없음\n입사지원서",
        "근무분야\n행정직, 연구직\n졸업증명서\n해당 없음\n입사지원서",
    ],
)
def test_multi_track_application_question_protects_missing_or_scoped_context(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-INFO-012" not in {item.id for item in result.questions}


def test_proxy_variable_expression_retrieves_review_question() -> None:
    result = FairpostEngine().check("지원서에 졸업 연도와 군 복무 여부를 기재")
    assert "Q-DIST-010" in {question.id for question in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "지원서에 이전 직장의 재직 기간을 기재해 주세요.",
        "근속 기간을 입사지원서에 작성해 주세요.",
    ],
)
def test_proxy_employment_duration_collection_retrieves_review_question(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-010" in {question.id for question in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "복리후생: 장기 재직 기간에 따라 포상금을 지급합니다.",
        "복리후생: 근속 기간별 장기근속자 포상을 지원합니다.",
    ],
)
def test_employee_benefit_duration_does_not_retrieve_proxy_question(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "Q-DIST-010" not in {question.id for question in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "자격요건: 이전 직장 재직 기간 3년 이상",
        "복리 후생\r\n재직 기간별 장기 근속 포상",
        "복리후생 제도를 안내합니다. 재직 기간 3년 이상 경력자를 모집합니다.",
    ],
)
def test_proxy_duration_exclusion_is_narrow_and_whitespace_safe(text: str) -> None:
    result = FairpostEngine().check(text)
    question_ids = {question.id for question in result.questions}
    if "장기 근속 포상" in text:
        assert "Q-DIST-010" not in question_ids
    else:
        assert "Q-DIST-010" in question_ids


def test_excluded_benefit_duration_does_not_hide_later_valid_proxy() -> None:
    result = FairpostEngine().check(
        "복리후생: 재직 기간별 장기근속 포상\n지원자격: 군필 또는 면제자"
    )
    assert "Q-DIST-010" in {question.id for question in result.questions}


def test_exclude_candidate_must_be_a_nonempty_string(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    proxy_rule = next(rule for rule in rules if rule["id"] == "Q-DIST-010")
    proxy_rule["trigger"]["exclude"][0]["candidate"] = 123
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match=r"exclude\.candidate"):
        load_ruleset(copied)

    rules = yaml.safe_load((DATA / "rules" / "questions.yaml").read_text(encoding="utf-8"))
    proxy_rule = next(rule for rule in rules if rule["id"] == "Q-DIST-010")
    proxy_rule["trigger"]["exclude"][0]["overlap_candidate"] = "yes"
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match=r"exclude\.overlap_candidate"):
        load_ruleset(copied)


def test_finding_offsets_reference_original_crlf_and_section() -> None:
    text = "채용 공고\r\n자격요건\r\n남성만 지원 가능\r\n문의처\r\n02-1234-5678"
    result = FairpostEngine().check(text)
    finding = next(item for item in result.findings if item.id == "SEX-001")
    assert finding.offset is not None
    start, end = finding.offset
    assert text[start:end] == finding.matched_text == "남성만"
    assert finding.section == "자격요건"


def test_output_has_no_prohibited_judgment_or_absence_phrase() -> None:
    payload = json.dumps(
        FairpostEngine().check("간단한 채용 공고").to_dict(),
        ensure_ascii=False,
    )
    for phrase in ("점수", "등급", "합격 판정", "없습니다"):
        assert phrase not in payload


def test_all_eleven_slots_are_returned_in_stable_order() -> None:
    slots = FairpostEngine().check("").slots
    assert len(slots) == 11
    assert [slot.slot for slot in slots] == sorted(slot.slot for slot in slots)


def test_statute_references_provenance_and_effective_dates() -> None:
    ruleset = load_ruleset()
    engine = FairpostEngine()
    snapshot = date.fromisoformat("2026-07-26")
    for rule in ruleset.rules:
        assert rule.get("provenance")
        assert rule.get("book_ref")
        if rule["layer"] == "law":
            statute = ruleset.statutes[rule["basis"]["statute_id"]]
            assert rule["basis"]["article"] in statute["articles"]
    for statute in ruleset.statutes.values():
        for article in statute["articles"].values():
            assert article.get("effective_date")
            assert date.fromisoformat(str(article["effective_date"])) <= snapshot
    result = engine.check("남성만 지원 가능")
    assert result.findings
    assert result.findings[0].basis.effective_date
    assert (
        result.findings[0].basis.effective_date
        <= result.findings[0].basis.snapshot_date
    )
    assert result.statute_snapshot_date == "2026-07-26"
    assert result.statute_snapshot_date in result.statute_notice


def test_required_prd_statute_article_cannot_be_removed(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "statutes" / "recruitment-procedure-act.yaml"
    statute = yaml.safe_load(path.read_text(encoding="utf-8"))
    del statute["articles"]["제10조"]
    path.write_text(
        yaml.safe_dump(statute, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(RuleLoadError, match="PRD 검수 대상 조문 누락"):
        load_ruleset(copied)


def test_matching_version_survives_statute_only_refresh(tmp_path: Path) -> None:
    baseline = load_ruleset(DATA)
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "statutes" / "recruitment-procedure-act.yaml"
    statute = yaml.safe_load(path.read_text(encoding="utf-8"))
    article = statute["articles"]["제9조"]
    article["text"] += "\n"
    article["hash"] = (
        "sha256:" + hashlib.sha256(article["text"].encode("utf-8")).hexdigest()
    )
    path.write_text(
        yaml.safe_dump(statute, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    refreshed = load_ruleset(copied)

    assert refreshed.version != baseline.version
    assert refreshed.matching_version == baseline.matching_version


def test_candidate_borne_screening_cost_is_flagged_with_article_nine() -> None:
    result = FairpostEngine().check("신체검사 비용은 최종 합격자 본인 부담입니다.")

    finding = next(item for item in result.findings if item.id == "COST-001")
    assert finding.basis.article == "제9조"
    assert finding.basis.title == "채용심사비용의 부담금지"
    assert finding.matched_text == "신체검사 비용은 최종 합격자 본인 부담"


def test_approved_screening_cost_exception_is_not_flagged() -> None:
    result = FairpostEngine().check(
        "사업장 특수성에 따라 고용노동부장관의 승인을 받아 "
        "채용심사비용 일부는 지원자 부담으로 합니다."
    )

    assert "COST-001" not in {item.id for item in result.findings}


def test_dictionary_change_requires_no_code_change(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    questions_path = copied / "rules" / "questions.yaml"
    questions = yaml.safe_load(questions_path.read_text(encoding="utf-8"))
    questions.append(
        {
            "id": "Q-TEST-999",
            "layer": "question",
            "trigger": {"type": "presence", "patterns": ["사전 교체 확인"]},
            "dimension": "절차",
            "question": "사전 교체가 반영되었습니까?",
            "follow_up": [],
            "basis": {"type": "consensus"},
            "provenance": {
                "method": "manual",
                "reviewed_by": "test",
                "reviewed_at": "2026-07-26",
            },
            "book_ref": "테스트",
        }
    )
    questions_path.write_text(
        yaml.safe_dump(questions, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    result = FairpostEngine(copied).check("사전 교체 확인")
    question = next(item for item in result.questions if item.id == "Q-TEST-999")
    assert question.review_scope == "posting"


def test_unknown_question_review_scope_is_rejected(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["review_scope"] = "unknown"
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="review_scope는 posting 또는 common"):
        load_ruleset(copied)


def test_law_rule_cannot_define_question_review_scope(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "law.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["review_scope"] = "posting"
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="law 규칙에는 review_scope"):
        load_ruleset(copied)


def test_question_severity_fails_with_clear_error(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["severity"] = "low"
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="question 규칙에는 severity"):
        load_ruleset(copied)


def test_research_question_requires_traceable_source_metadata(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    research_rule = next(
        rule for rule in rules if rule["basis"]["type"] == "research"
    )
    del research_rule["basis"]["pages"]
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match=r"basis\.pages"):
        load_ruleset(copied)


def test_web_research_question_requires_access_date(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    web_research_rule = next(
        rule
        for rule in rules
        if rule["basis"]["type"] == "research"
        and "source_url" in rule["basis"]
    )
    del web_research_rule["basis"]["accessed_at"]
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match=r"basis\.accessed_at"):
        load_ruleset(copied)


def test_presence_patterns_must_be_nonempty_strings(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    presence_rule = next(
        rule for rule in rules if rule["trigger"]["type"] == "presence"
    )
    presence_rule["trigger"]["patterns"] = [123]
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="문자열 patterns"):
        load_ruleset(copied)


def test_question_context_groups_are_candidate_relative(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules.append(
        {
            "id": "Q-TEST-CONTEXT",
            "layer": "question",
            "trigger": {
                "type": "presence",
                "patterns": ["공통 지원서"],
                "context_groups": {
                    "multiple_tracks": {
                        "patterns": ["복수 직렬"],
                        "window": 40,
                    },
                    "requested_information": {
                        "patterns": ["학력 기재"],
                        "window": 40,
                    },
                },
            },
            "dimension": "정보",
            "question": "직렬별로 필요한 정보만 받습니까?",
            "follow_up": [],
            "basis": {"type": "consensus"},
            "provenance": {
                "method": "manual",
                "reviewed_by": "test",
                "reviewed_at": "2026-07-29",
            },
            "book_ref": "테스트",
        }
    )
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    engine = FairpostEngine(copied)

    positive = engine.check("복수 직렬 채용의 공통 지원서에서 학력 기재")
    question = next(
        item for item in positive.questions if item.id == "Q-TEST-CONTEXT"
    )
    assert question.matched_text == "공통 지원서"

    missing_group = engine.check("복수 직렬 채용의 공통 지원서를 사용")
    assert "Q-TEST-CONTEXT" not in {
        item.id for item in missing_group.questions
    }

    second_candidate_text = (
        "공통 지원서"
        + ("가" * 100)
        + "복수 직렬 채용의 공통 지원서에서 학력 기재"
    )
    first_candidate_is_too_far = engine.check(second_candidate_text)
    question = next(
        item
        for item in first_candidate_is_too_far.questions
        if item.id == "Q-TEST-CONTEXT"
    )
    assert question.offset is not None
    assert question.offset[0] == second_candidate_text.rfind("공통 지원서")
    assert second_candidate_text[question.offset[0] : question.offset[1]] == (
        "공통 지원서"
    )


def test_context_groups_require_valid_named_pattern_lists(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    presence_rule = next(
        rule for rule in rules if rule["trigger"]["type"] == "presence"
    )
    presence_rule["trigger"]["context_groups"] = {
        "tracks": {"patterns": [123], "window": 100}
    }
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="context_groups"):
        load_ruleset(copied)


def test_context_window_without_groups_is_rejected(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    presence_rule = next(
        rule for rule in rules if rule["trigger"]["type"] == "presence"
    )
    presence_rule["trigger"]["context_window"] = 100
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="그룹별 window"):
        load_ruleset(copied)


@pytest.mark.parametrize("invalid_window", [True, 0, 6001])
def test_context_window_must_be_bounded_positive_integer(
    tmp_path: Path,
    invalid_window: object,
) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    presence_rule = next(
        rule for rule in rules if rule["trigger"]["type"] == "presence"
    )
    presence_rule["trigger"]["context_groups"] = {
        "tracks": {
            "patterns": ["복수 직렬"],
            "window": invalid_window,
        }
    }
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="1~6000"):
        load_ruleset(copied)


def test_context_groups_are_rejected_for_law_rules(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "law.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["trigger"]["context_groups"] = {
        "tracks": {"patterns": ["복수 직렬"], "window": 100}
    }
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="question presence"):
        load_ruleset(copied)


def test_context_groups_are_rejected_for_absence_rules(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    absence_rule = next(
        rule for rule in rules if rule["trigger"]["type"] == "absence"
    )
    absence_rule["trigger"]["context_groups"] = {
        "tracks": {"patterns": ["복수 직렬"], "window": 100}
    }
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="question presence"):
        load_ruleset(copied)


def test_unknown_section_scope_is_rejected(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    presence_rule = next(
        rule for rule in rules if rule["trigger"]["type"] == "presence"
    )
    presence_rule["trigger"]["section_scope"] = "존재하지 않는 섹션"
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="section_scope"):
        load_ruleset(copied)


def test_contact_point_has_exact_prd_components(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "slots.yaml"
    slots = yaml.safe_load(path.read_text(encoding="utf-8"))
    slots["contact_point"]["components"].append(
        {"id": "extra", "patterns": ["추가 구성요소"]}
    )
    path.write_text(
        yaml.safe_dump(slots, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="4개여야"):
        load_ruleset(copied)


def test_slots_file_must_match_all_eleven_prd_slots(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "slots.yaml"
    slots = yaml.safe_load(path.read_text(encoding="utf-8"))
    del slots["compensation"]
    path.write_text(
        yaml.safe_dump(slots, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="11개 슬롯"):
        load_ruleset(copied)


def test_local_statute_basis_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "local_rules.yaml"
    path.write_text(
        """
- id: Q-LOCAL-BAD
  layer: question
  trigger: {type: presence, patterns: [테스트]}
  dimension: 절차
  question: 테스트 질문입니까?
  basis:
    type: statute
    law: 채용절차의 공정화에 관한 법률
    article: 제4조
    statute_id: recruitment-procedure-act
  provenance: {method: manual}
  book_ref: 기관 규정
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="consensus만 허용"):
        load_ruleset(local_rules_path=path)


def test_ancestor_data_discovery_supports_isolated_wheel_layout(
    tmp_path: Path,
) -> None:
    module_file = tmp_path / "archive" / "Lib" / "site-packages" / "core" / "loader.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("", encoding="utf-8")
    data_dir = tmp_path / "archive" / "share" / "fairpost" / "data"
    (data_dir / "rules").mkdir(parents=True)
    (data_dir / "statutes").mkdir()
    (data_dir / "slots.yaml").write_text("slots: []\n", encoding="utf-8")

    assert _find_ancestor_data_dir(module_file) == data_dir


def test_default_data_dir_prefers_data_bundled_with_target_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    module_file = target / "core" / "loader.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("", encoding="utf-8")
    bundled_data = target / "share" / "fairpost" / "data"
    (bundled_data / "rules").mkdir(parents=True)
    (bundled_data / "statutes").mkdir()
    (bundled_data / "slots.yaml").write_text("{}\n", encoding="utf-8")

    prefix = tmp_path / "prefix"
    prefix_data = prefix / "share" / "fairpost" / "data"
    prefix_data.mkdir(parents=True)

    monkeypatch.delenv("FAIRPOST_DATA_DIR", raising=False)
    monkeypatch.setattr(loader_module, "__file__", str(module_file))
    monkeypatch.setattr(loader_module.sys, "prefix", str(prefix))

    assert loader_module._default_data_dir() == bundled_data


def test_default_data_dir_preserves_explicit_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured-data"
    monkeypatch.setenv("FAIRPOST_DATA_DIR", str(configured))

    assert loader_module._default_data_dir() == configured


def test_default_data_dir_prefers_source_checkout_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    module_file = checkout / "core" / "loader.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("", encoding="utf-8")
    source_data = checkout / "data"
    source_data.mkdir()

    bundled_data = checkout / "share" / "fairpost" / "data"
    (bundled_data / "rules").mkdir(parents=True)
    (bundled_data / "statutes").mkdir()
    (bundled_data / "slots.yaml").write_text("{}\n", encoding="utf-8")
    prefix = tmp_path / "prefix"
    (prefix / "share" / "fairpost" / "data").mkdir(parents=True)

    monkeypatch.delenv("FAIRPOST_DATA_DIR", raising=False)
    monkeypatch.setattr(loader_module, "__file__", str(module_file))
    monkeypatch.setattr(loader_module.sys, "prefix", str(prefix))

    assert loader_module._default_data_dir() == source_data


def test_local_rules_can_be_loaded_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "local_rules.yaml"
    path.write_text(
        """
- id: Q-LOCAL-ENV
  layer: question
  trigger: {type: presence, patterns: [환경 규칙 확인]}
  dimension: 절차
  question: 환경변수의 로컬 규칙이 적용되었습니까?
  basis: {type: consensus}
  provenance:
    method: manual
    reviewed_by: test
    reviewed_at: "2026-07-26"
  book_ref: 기관 규정
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAIRPOST_LOCAL_RULES_PATH", str(path))
    result = FairpostEngine().check("환경 규칙 확인")
    question = next(item for item in result.questions if item.id == "Q-LOCAL-ENV")
    assert question.review_scope == "posting"


def test_core_does_not_import_tools() -> None:
    for path in (ROOT / "core").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import tools" not in source
        assert "from tools" not in source


ORDINARY_POSTING = """2026년 사무직 채용

채용개요
사무행정 2명

자격요건
관련 업무 경력 2년 이상

전형절차
서류전형 후 면접전형

일정
접수 기간: 2026. 9. 1. ~ 9. 12.

근무조건
연봉 3,600만원

문의처
인사팀 02-1234-5678
"""


def test_all_four_fairness_dimensions_fire_on_ordinary_posting() -> None:
    result = FairpostEngine().check(ORDINARY_POSTING)
    dimensions = {question.dimension for question in result.questions}
    assert dimensions == {"분배", "절차", "대인", "정보"}


def test_finding_link_emits_related_question_without_its_own_trigger() -> None:
    result = FairpostEngine().check("용모 단정한 지원자를 찾습니다.")
    assert any(finding.id == "LOOK-001" for finding in result.findings)
    linked = next(item for item in result.questions if item.id == "Q-INTER-001")
    assert linked.trigger_reason == "finding"
    assert linked.linked_findings == ["LOOK-001"]
    assert linked.priority == 1
    assert linked.matched_text is None


def test_question_that_fires_on_its_own_trigger_keeps_that_reason() -> None:
    result = FairpostEngine().check("밝은 성격의 지원자를 찾습니다.")
    question = next(item for item in result.questions if item.id == "Q-INTER-001")
    assert question.trigger_reason == "presence"
    assert question.linked_findings == []
    assert question.matched_text == "밝은 성격"


def test_questions_are_ordered_by_priority_then_id() -> None:
    result = FairpostEngine().check(FULL_POSTING)
    ordering = [(item.priority, item.id) for item in result.questions]
    assert ordering == sorted(ordering)
    assert all(
        item.priority == 1 for item in result.questions if item.linked_findings
    )


def test_every_law_rule_declares_related_questions() -> None:
    rules = yaml.safe_load((DATA / "rules" / "law.yaml").read_text(encoding="utf-8"))
    missing = [rule["id"] for rule in rules if not rule.get("related_questions")]
    assert missing == []


def test_related_questions_must_reference_an_existing_question(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "law.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["related_questions"] = ["Q-DOES-NOT-EXIST"]
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="related_questions"):
        load_ruleset(copied)


def test_question_rules_may_not_declare_related_questions(tmp_path: Path) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "questions.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["related_questions"] = ["Q-INTER-001"]
    path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="law 규칙에만"):
        load_ruleset(copied)


def test_finding_link_respects_the_questions_own_protective_exclusion() -> None:
    result = FairpostEngine().check("블라인드 안내: 임신 여부는 기재하지 마세요.")
    assert any(finding.id == "PREG-001" for finding in result.findings)
    assert "Q-DIST-017" not in {item.id for item in result.questions}


@pytest.mark.parametrize(
    "text",
    [
        "2차 AI 역량검사 - AI가 최종 결정하며 사람의 재검토는 제공하지 않음",
        "AI가 최종 결정하며 재검토는 없음",
        "AI 자동 탈락을 적용하며 이의신청은 받지 않음",
    ],
)
def test_negated_human_review_does_not_protect_the_automated_decision(
    text: str,
) -> None:
    result = FairpostEngine().check(text)
    assert "AI-001" in {finding.id for finding in result.findings}
