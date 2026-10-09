"""Keep the actual qualification name in a review question's source span."""
import pytest
from core import FairpostEngine


@pytest.mark.parametrize("qualification", ["전문학사 이상", "전문학사  이상", "전문학사\u200b 이상"])
def test_associate_degree_question_keeps_complete_name(qualification):
    text = "지원자격\n" + qualification + "으로 관련 전공 이수자"
    result = FairpostEngine().check(text).to_dict()
    question = next(q for q in result["questions"] if q["id"] == "Q-DIST-005")
    assert question["matched_text"] == qualification
    start, end = question["offset"]
    assert text[start:end] == qualification


def test_degree_requirement_question_remains_suppressed_when_unrestricted():
    result = FairpostEngine().check("지원자격\n학력 제한 없음. 경력 무관.").to_dict()
    assert not any(q["id"] == "Q-DIST-005" for q in result["questions"])
