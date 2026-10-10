"""Regression cases for HTML around the recruitment body, not site-wide text."""
import pytest

from tools.collect_corpus import ContentParser


@pytest.mark.parametrize("void_tag", ["br", "img", "input", "hr", "wbr", "source"])
def test_void_elements_do_not_extend_recruitment_body(void_tag):
    parser = ContentParser(target_classes={"detail_info_box"})
    parser.feed(f'<div class="detail_info_box">지원자격<br>경력 무관<{void_tag}></div>'
                '<footer>사이트 운영자 소개</footer><script>function footerOnly() {}</script>')
    assert "지원자격" in parser.text()
    assert "경력 무관" in parser.text()
    assert "사이트 운영자" not in parser.text()
    assert "footerOnly" not in parser.text()


@pytest.mark.parametrize("tag", ["script", "style", "template", "noscript"])
def test_non_content_inside_target_is_excluded(tag):
    parser = ContentParser(target_classes={"detailTxt"})
    parser.feed(f'<div class="detailTxt">서류 접수<{tag}>배제할 코드 내용</{tag}>'
                '<p>면접 후 최종합격</p></div>')
    assert "배제할 코드" not in parser.text()
    assert "서류 접수" in parser.text()
    assert "면접 후 최종합격" in parser.text()


def test_nested_targets_and_self_closing_markup_keep_later_target():
    parser = ContentParser(target_classes={"detailTxt", "tab-content"})
    parser.feed('<div class="tab-content"><div class="detailTxt">가점 안내<br/>'
                '<span>장애인 우대</span></div></div><p>회사 홍보</p>'
                '<div class="detailTxt">이의신청 안내</div><p>저작권 안내</p>')
    assert parser.text().splitlines() == ["가점 안내", "장애인 우대", "이의신청 안내"]


def test_unmatched_closing_tag_does_not_close_target():
    parser = ContentParser(target_classes={"detailTxt"})
    parser.feed('<div class="detailTxt">지원 안내</span><p>접수 마감 10월 30일</p></div>푸터')
    assert "접수 마감 10월 30일" in parser.text()
    assert "푸터" not in parser.text()


def test_textarea_boundaries_and_entity_content_remain_literal():
    parser = ContentParser(textarea_id="content")
    parser.feed('<textarea id="other">다른 입력</textarea>'
                '<textarea id="content">지원 &amp; 문의&lt;br&gt;안내</textarea>외부 내용')
    assert parser.text() == "지원 & 문의<br>안내"


def test_heading_image_does_not_capture_following_navigation():
    parser = ContentParser(target_classes={"detailTxt"})
    parser.feed('<h2>기관명<img src="logo"></h2><nav>다른 메뉴</nav>'
                '<div class="detailTxt">공고 본문</div>')
    assert parser.h2_values == ["기관명"]
    assert parser.text() == "공고 본문"
