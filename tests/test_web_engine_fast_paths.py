"""Adversarial parity for the web engine's normalization fast paths.

web/engine.js memoizes per-character NFKC, skips offset arrays for text the
rewrite leaves unchanged, rewrites morph sources with one alternation, reuses
compiled pattern regexes and scans sentence marks outwards from a match. Each
shortcut must give exactly what the Python core gives, so these strings aim at
the edges: compatibility characters, U+318D, compatibility and decomposed
jamo (per-character NFKC must not compose them), combining marks, zero-width
characters, non-ASCII whitespace and line separators, CRLF, surrogate pairs,
and every morph rewrite source at text, line and sentence edges.
"""

from __future__ import annotations

from pathlib import Path
import random

import pytest

from core import FairpostEngine
from core.morph import MORPH_REWRITES
from web_engine import web_check_batch

ZWSP, ZWNJ, ZWJ, BOM = chr(0x200B), chr(0x200C), chr(0x200D), chr(0xFEFF)
NBSP, IDEOGRAPHIC_SPACE = chr(0x00A0), chr(0x3000)
LINE_SEPARATOR, PARAGRAPH_SEPARATOR, NEXT_LINE = chr(0x2028), chr(0x2029), chr(0x85)
ARAEA = chr(0x318D)  # HANGUL LETTER ARAEA; NFKC maps it to U+119E
MIDDLE_DOT = chr(0x00B7)
ACUTE = chr(0x0301)  # combining acute accent
FULLWIDTH_AI = chr(0xFF21) + chr(0xFF29)  # "ＡＩ"
FULLWIDTH_20 = chr(0xFF12) + chr(0xFF10)  # "２０"
FULLWIDTH_COLON, FULLWIDTH_TILDE = chr(0xFF1A), chr(0xFF5E)
CIRCLED_ONE, PARENTHESIZED_CORP = chr(0x2460), chr(0x3231)  # "①", "㈱"
ROMAN_TWO, SQUARE_M2, LIGATURE_FI = chr(0x2161), chr(0x33A1), chr(0xFB01)
ARABIC_LIGATURE = chr(0xFDFA)  # NFKC expands one code point to 18
# "가족" as compatibility jamo and as conjoining jamo: per-character NFKC
# turns the first into the second and must not compose either into "가".
COMPAT_GA = chr(0x3131) + chr(0x314F)
DECOMPOSED_GA = chr(0x1100) + chr(0x1161)
# Whole-string normalization would compose these into "자" (a heading alias
# "자격요건") and "여" (the candidate "여성만"); per character it must not.
COMPAT_JA, DECOMPOSED_JA = chr(0x3148) + chr(0x314F), chr(0x110C) + chr(0x1161)
COMPAT_YEO, DECOMPOSED_YEO = chr(0x3147) + chr(0x3155), chr(0x110B) + chr(0x1167)
YEO_PLUS_FINAL = "여" + chr(0x11BC)  # NFC would compose this into "영"
EMOJI = chr(0x1F600)
PIN = chr(0x1F4CC)
MATH_DIGIT_ONE = chr(0x1D7CF)  # an astral decimal digit

MORPH_SOURCES = [source for source, _target in MORPH_REWRITES]


def _fixed_cases() -> dict[str, str]:
    cases = {
        "fullwidth-ai": f"전형절차\n{FULLWIDTH_AI}가 최종 결정합니다.",
        "fullwidth-age": f"자격요건\n{FULLWIDTH_20}대 지원자 우대",
        "fullwidth-heading-colon": f"지원자격{FULLWIDTH_COLON}\n남성에 한합니다.",
        "fullwidth-tilde-schedule": f"접수기간\n2026. 8. 1. {FULLWIDTH_TILDE} 8. 12.",
        "circled-heading": f"{CIRCLED_ONE} 자격요건\n여성만 지원 가능",
        "corp-and-units": f"{PARENTHESIZED_CORP}페어포스트 {ROMAN_TWO}팀 {SQUARE_M2} {LIGATURE_FI}eld",
        "araea-heading": f"채용서류 반환{ARAEA}파기 안내\n채용서류는 반환하지 않습니다.",
        "middle-dot-heading": f"채용서류 반환{MIDDLE_DOT}파기 안내\n반환 청구 기간은 14일입니다.",
        "araea-in-list": f"우대사항\n지식{ARAEA}기술{ARAEA}태도 우수자",
        "compat-jamo-family": f"입사지원서 항목\n{COMPAT_GA}족 관계 기재",
        "decomposed-jamo-family": f"입사지원서 항목\n{DECOMPOSED_GA}족 관계 기재",
        "compat-jamo-heading": f"{COMPAT_JA}격요건\n남성만 지원 가능",
        "decomposed-jamo-heading": f"{DECOMPOSED_JA}격요건\n남성만 지원 가능",
        "compat-jamo-heading-zero-width": f"{COMPAT_JA}{ZWSP}격요건\n남성만 지원 가능",
        "compat-jamo-candidate": f"자격요건\n{COMPAT_YEO}성만 지원 가능",
        "decomposed-jamo-candidate": f"자격요건\n{DECOMPOSED_YEO}성만 지원 가능",
        "compat-jamo-candidate-fullwidth": f"자격요건\n{COMPAT_YEO}성만 지원 가능 {FULLWIDTH_AI}",
        "trailing-final-jamo": f"우대사항\n{YEO_PLUS_FINAL}어 능통자",
        "combining-mark-family": f"입사지원서 항목\n가{ACUTE}족 관계",
        "combining-mark-alone": f"{ACUTE}자격요건\n남성만{ACUTE} 지원 가능",
        "arabic-ligature-expansion": f"{ARABIC_LIGATURE} 자격요건\n여성만 지원 가능 {ARABIC_LIGATURE}",
        "zero-width-in-heading": f"자격{ZWSP}요건\n남성만 지원 가능",
        "zero-width-in-word": f"자격요건\n남{ZWNJ}성만 지원{ZWJ} 가능합{ZWSP}니다.",
        "bom-heading": f"{BOM}문의처\n인사팀 02-1234-5678",
        "zero-width-only": ZWSP + ZWNJ + ZWJ + BOM,
        "nbsp-phrase": f"자격요건\n남성{NBSP}지원자만 가능",
        "ideographic-space-phrase": f"자격요건\n여성{IDEOGRAPHIC_SPACE}지원자만 가능",
        "line-separator-lines": f"자격요건{LINE_SEPARATOR}남성만 지원 가능{PARAGRAPH_SEPARATOR}전형절차{NEXT_LINE}서류전형",
        "vertical-tab-and-form-feed": "자격요건\v남성만 지원 가능\f전형절차\x1c면접",
        "crlf-sections": "자격요건\r\n남성만 지원 가능\r\n전형절차\r\n서류전형 → 면접",
        "lone-cr": "자격요건\r남성만 지원 가능\r전형절차\r서류전형",
        "emoji-heading": f"{PIN} 채용 안내\n자격요건\n여성만 지원 가능 {EMOJI}",
        "emoji-before-mark": f"자격요건\n{EMOJI}. 남성만 지원 가능{EMOJI}! 혼인 여부 기재?",
        "astral-digit-before-dot": f"전형절차\n{MATH_DIGIT_ONE}. 서류전형 후 면접을 실시합니다.",
        # A greedy "[^\r\n]{1,40}" match on the normalized view (the fullwidth
        # colon changes it) ends on the last unit of a surrogate pair, so its
        # source end must be the end of the whole pair.
        "regex-match-ends-on-emoji": f"모집 인원\n여성{FULLWIDTH_COLON} 2명 {EMOJI}",
        "regex-match-ends-on-emoji-morph": f"모집 인원\n여성{FULLWIDTH_COLON} 채용합니다 {EMOJI}{EMOJI}",
        "regex-match-ends-on-astral-digit": f"모집 인원\n남성{FULLWIDTH_COLON} {MATH_DIGIT_ONE}{MATH_DIGIT_ONE}",
        "decimal-and-version-dots": "자격요건\n경력 3.5년 이상, Python 3.12 사용. 남성만 지원 가능.",
        "cjk-marks": "자격요건\n남성만 지원 가능。혼인 여부 기재！가족 관계？",
        "long-emoji-line": (EMOJI * 150) + " 남성만 지원 가능합니다. " + ("가" * 150),
        "long-line-marks": ("가나다. " * 60) + "혼인 여부는 기재하지 마세요. " + ("라마바! " * 60),
        "empty": "",
        "space-only": " ",
        "newline-only": "\n",
    }
    for index, source in enumerate(MORPH_SOURCES):
        cases[f"morph-{index}-alone"] = source
        cases[f"morph-{index}-text-edges"] = f"{source}책임감 있는 분{source}"
        cases[f"morph-{index}-line-edges"] = f"자격요건\n{source}\n남성만 지원 가능{source}\n{source}"
        cases[f"morph-{index}-zero-width"] = f"우대사항\n{source[0]}{ZWSP}{source[1:]} 경력자"
        cases[f"morph-{index}-nbsp"] = f"우대사항\n{NBSP}{source}{IDEOGRAPHIC_SPACE}경력자"
        cases[f"morph-{index}-fullwidth"] = f"전형절차\n{FULLWIDTH_AI} 면접{source}."
        cases[f"morph-{index}-emoji"] = f"자격요건\n{EMOJI}{source}{EMOJI} 여성만 지원 가능"
    cases["morph-chain"] = "".join(MORPH_SOURCES) + "\n" + "".join(reversed(MORPH_SOURCES))
    cases["morph-overlap"] = "있으신신 하시는신 합니다다 이신으신 않습니다니다"
    cases["morph-in-pattern"] = "자격요건\n남성에 한합니다. 남성에 한함"
    return cases


def _seeded_cases(count: int) -> list[str]:
    rng = random.Random(20261011)
    headings = [
        "자격요건", "지원자격", "우대사항", "전형절차", "전형일정", "접수기간",
        "근무조건", "제출서류", "유의사항", "문의처", "담당업무", "기타",
        f"채용서류 반환{ARAEA}파기 안내", "합류 여정", "What You’ll Do",
    ]
    decorations = ["", "■ ", "1. ", "[", "【", "※ ", "## ", "○ ", f"{CIRCLED_ONE} ", " ", IDEOGRAPHIC_SPACE]
    tails = ["", "]", "】", ":", FULLWIDTH_COLON, " "]
    phrases = [
        "남성만 지원 가능", "여성 지원자만", f"{FULLWIDTH_20}대 지원자 우대", "혼인 여부",
        "가족 관계", "과거 병력은 기재하지 마세요", "출신학교 기재 금지", "AI 면접으로 최종 결정",
        f"{FULLWIDTH_AI} 자동 탈락", "서류전형 → 면접 → 최종 합격", "1차 면접", "인터뷰",
        "접수 기간 8월 1일까지", "연봉 3,000만원", "인사팀 02-1234-5678", "hr@example.com",
        "추후 안내", "채용시까지", "장애인 지원 불가", "세례교인에 한함", "경력 3년 이상",
        "단, ", "다만", "무관", ", ", ". ", "! ", "? ", "。", "<b>", "</b>",
    ]
    noise = [
        ZWSP, ZWNJ, ZWJ, BOM, NBSP, IDEOGRAPHIC_SPACE, LINE_SEPARATOR, PARAGRAPH_SEPARATOR,
        NEXT_LINE, "\r\n", "\r", "\t", "\v", ARAEA, MIDDLE_DOT, ACUTE, COMPAT_GA, DECOMPOSED_GA,
        EMOJI, MATH_DIGIT_ONE, FULLWIDTH_AI, PARENTHESIZED_CORP, ROMAN_TWO, LIGATURE_FI,
        ARABIC_LIGATURE, COMPAT_JA, DECOMPOSED_JA, COMPAT_YEO, DECOMPOSED_YEO, *MORPH_SOURCES,
    ]
    texts = []
    for _ in range(count):
        parts = []
        for _ in range(rng.randint(2, 24)):
            roll = rng.random()
            if roll < 0.2:
                parts.append(rng.choice(decorations) + rng.choice(headings) + rng.choice(tails) + "\n")
            elif roll < 0.7:
                phrase = rng.choice(phrases)
                if rng.random() < 0.3:
                    at = rng.randint(0, len(phrase))
                    phrase = phrase[:at] + rng.choice(noise) + phrase[at:]
                parts.append(phrase)
            else:
                parts.append(rng.choice(noise))
            parts.append(rng.choice(["", " ", "\n"]))
        texts.append("".join(parts))
    return texts




@pytest.mark.requires_node
def test_adversarial_normalization_edges_match_python_core(tmp_path: Path) -> None:
    cases = _fixed_cases()
    labels = list(cases)
    texts = [cases[label] for label in labels]
    # One batch shares the web engine's caches across postings, and the second
    # pass re-runs every text against warm caches.
    web = web_check_batch(texts + texts, tmp_path)
    engine = FairpostEngine()
    for index, (label, text) in enumerate(zip(labels, texts, strict=True)):
        python = engine.check(text).to_dict()
        assert web[index] == python, label
        assert web[index + len(texts)] == python, f"{label} (warm caches)"


@pytest.mark.requires_node
def test_seeded_adversarial_postings_match_python_core(tmp_path: Path) -> None:
    texts = _seeded_cases(160)
    web = web_check_batch(texts, tmp_path)
    engine = FairpostEngine()
    for index, text in enumerate(texts):
        assert web[index] == engine.check(text).to_dict(), f"seeded case {index} {text!r}"
