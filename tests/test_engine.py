from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from core import FairpostEngine, RuleLoadError, load_ruleset


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
    ["연령 제한없음", "연령 제한 없습니다", "연령 제한이 없다"],
)
def test_no_age_limit_phrase_is_not_flagged(phrase: str) -> None:
    result = FairpostEngine().check(f"응시자격\n{phrase}")
    assert "AGE-002" not in {finding.id for finding in result.findings}


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


def test_book_based_questions_cover_full_ai_hiring_review() -> None:
    result = FairpostEngine().check(
        "AI 채용 안내\nAI 영상면접과 AI 역량검사 결과를 자동 평가합니다."
    )
    question_ids = {question.id for question in result.questions}
    assert {
        "Q-PROC-004",
        "Q-INFO-009",
        "Q-PROC-010",
        "Q-INTER-003",
        "Q-INTER-004",
        "Q-DIST-008",
        "Q-DIST-009",
        "Q-DIST-011",
    } <= question_ids


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
        ("직무분류\n사회복지.종교", "Q-DIST-012"),
        ("기관은 지역사회 발전과 혁신을 추구합니다.", "Q-DIST-013"),
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
    assert question.matched_text == "세례"
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
    assert payload_question["reference"]["source_url"].startswith("https://www.ncs.go.kr/")
    assert payload_question["reference"]["sections"] == ["채용단계별 주요 차별 요소 - 신앙"]


def test_proxy_variable_expression_retrieves_review_question() -> None:
    result = FairpostEngine().check("지원서에 졸업 연도와 군 복무 여부를 기재")
    assert "Q-DIST-010" in {question.id for question in result.questions}


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
