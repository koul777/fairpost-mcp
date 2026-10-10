from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml

from core import FairpostEngine
from core.loader import load_ruleset
from tools import export_posting_templates as exporter
from tools.verify_distribution import ASSET_PRIVACY_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TEMPLATES_PATH = DATA_DIR / "posting_templates.yaml"
BUNDLE_PATH = ROOT / "web" / "posting-templates.js"
BUNDLE_PREFIX = "window.FAIRPOST_POSTING_TEMPLATES="

WEB_DATA_PATH = ROOT / "web" / "data.js"
WEB_DATA_PREFIX = "window.FAIRPOST_DATA="
EXPECTED_NOTICE = (
    "예시 문장입니다. ○○ 빈칸을 조직의 실제 사실로 바꾸고, "
    "운영하지 않는 내용은 넣지 마세요."
)
SLOT_IDS = [
    "selection_stages",
    "evaluation_criteria",
    "schedule",
    "result_notice",
    "appeal_channel",
    "contact_point",
    "document_return",
    "ai_disclosure",
    "compensation",
    "preference_items",
    "qualification_rationale",
]
# The UI uses these single-component supplements for partial guidance.
UI_REQUIRED_COMPONENTS = [
    ("compensation", "amount_or_range"),
    ("result_notice", "notice_time"),
    ("result_notice", "notice_audience"),
    ("schedule", "application_date"),
    ("schedule", "assessment_date"),
]
# Words that would turn an example into a verdict or an assurance.
VERDICT_WORDS = ["통과", "적합 판정", "점수", "공정함을 보증"]

# Short postings that contain none of the 11 slots, so any slot reported as found
# after appending a template comes from the template itself.
BASE_POSTINGS = {
    "minimal": "2026년 사무직 채용\n담당 업무: 문서 관리",
    "sectioned": (
        "채용개요\n사무행정 2명 채용\n\n자격요건\n관련 학과 졸업자\n\n근무조건\n근무지 본사"
    ),
}

# Cross-detection is a defect for the person pasting a template: it would make the
# engine stop reporting a gap that the pasted text does not actually fill. Anything
# that is unavoidable has to be listed here on purpose.
ALLOWED_CROSS_DETECTION: dict[str, set[str]] = {}

# Placeholders cannot satisfy components that need real digits or addresses (phone,
# e-mail, an amount, application and assessment dates), so those are checked after
# substituting synthetic values built at run time. The values are assembled from
# parts so that no literal looks like personal data.
SYNTHETIC_FILLS = {
    ("contact_point", "phone"): "-".join(["02", "123", "4567"]),
    ("contact_point", "email"): "".join(["name", "@", "example", ".org"]),
    ("compensation", "amount_or_range"): "3,000만",
    ("schedule", "application_date"): "10",
    ("schedule", "assessment_date"): "10",
}


def _load_document() -> dict[str, Any]:
    return yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))


DOCUMENT = _load_document()
COMPONENT_CASES = [
    (slot_id, component_id)
    for slot_id in SLOT_IDS
    for component_id in DOCUMENT["slots"][slot_id]["components"]
]


@pytest.fixture(scope="module")
def engine() -> FairpostEngine:
    return FairpostEngine()


def _web_data_version() -> str:
    text = WEB_DATA_PATH.read_text(encoding="utf-8")
    assert text.startswith(WEB_DATA_PREFIX)
    assert text.endswith(";\n")
    return json.loads(text[len(WEB_DATA_PREFIX) : -2])["version"]


def _parse_bundle(text: str) -> dict[str, Any]:
    assert text.startswith(BUNDLE_PREFIX)
    assert text.endswith(";\n")
    return json.loads(text[len(BUNDLE_PREFIX) : -2])


def _found_slots(result) -> set[str]:
    return {slot.slot for slot in result.slots if slot.found}


def _slot_status(result, slot_id: str):
    return next(slot for slot in result.slots if slot.slot == slot_id)


def _finding_ids(result) -> set[str]:
    return {finding.id for finding in result.findings}


def _fill_component(slot_id: str, component_id: str, sentence: str) -> str:
    fill = SYNTHETIC_FILLS.get((slot_id, component_id))
    if fill is None:
        return sentence
    return sentence.replace(exporter.PLACEHOLDER, fill)


def _append(base: str, addition: str) -> str:
    return f"{base}\n\n{addition}"


# --- 1. contract: validation, freshness, shape ---------------------------------


def test_template_file_passes_validation(engine: FairpostEngine) -> None:
    assert exporter.validate_document(_load_document(), engine.ruleset) == []


def test_generated_bundle_is_current() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/export_posting_templates.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    document = _load_document()
    expected = exporter.render_bundle(exporter.build_payload(document))
    assert BUNDLE_PATH.read_text(encoding="utf-8") == expected
    assert "\r" not in expected


def test_check_fails_without_touching_a_stale_or_missing_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "posting-templates.js"
    assert exporter.main(["--output", str(output), "--check"]) == 1
    assert not output.exists()

    stale = BUNDLE_PATH.read_text(encoding="utf-8").replace("예시", "견본", 1)
    output.write_text(stale, encoding="utf-8", newline="\n")
    capsys.readouterr()
    assert exporter.main(["--output", str(output), "--check"]) == 1
    assert output.read_text(encoding="utf-8") == stale
    assert "tools/export_posting_templates.py" in capsys.readouterr().err

    assert exporter.main(["--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == BUNDLE_PATH.read_text(encoding="utf-8")
    assert exporter.main(["--output", str(output), "--check"]) == 0


def test_bundle_matches_the_ui_data_contract(engine: FairpostEngine) -> None:
    bundle = _parse_bundle(BUNDLE_PATH.read_text(encoding="utf-8"))
    document = _load_document()

    assert set(bundle) == {"schema_version", "version", "notice", "placeholder", "slots"}
    assert bundle["schema_version"] == "fairpost-posting-templates-v1"
    assert bundle["notice"] == EXPECTED_NOTICE
    assert bundle["placeholder"] == "○○" == "○○"
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert bundle["version"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    assert sorted(bundle["slots"]) == sorted(SLOT_IDS)
    assert len(bundle["slots"]) == 11
    for slot_id, entry in bundle["slots"].items():
        assert set(entry) == {"label", "text", "condition", "components", "sources"}
        assert entry["label"] == engine.ruleset.slots[slot_id]["label"]
        assert isinstance(entry["text"], str) and entry["text"].strip()
        assert entry["condition"] is None or isinstance(entry["condition"], str)
        component_ids = {c["id"] for c in engine.ruleset.slots[slot_id]["components"]}
        assert set(entry["components"]) <= component_ids
        for source in entry["sources"]:
            assert set(source) == {"title", "locator"}
            assert isinstance(source["title"], str) and source["title"]
            assert isinstance(source["locator"], str)


@pytest.mark.parametrize(("slot_id", "component_id"), UI_REQUIRED_COMPONENTS)
def test_components_the_ui_depends_on_are_provided(
    slot_id: str, component_id: str
) -> None:
    bundle = _parse_bundle(BUNDLE_PATH.read_text(encoding="utf-8"))
    sentence = bundle["slots"][slot_id]["components"][component_id]
    assert sentence.strip()


def test_ai_disclosure_is_conditional_and_notice_is_fixed() -> None:
    document = _load_document()
    assert (
        document["slots"]["ai_disclosure"]["condition"]
        == "AI·자동화 도구를 실제로 쓰는 경우에만 넣으세요."
    )
    assert document["notice"] == EXPECTED_NOTICE


# --- 2. the exporter rejects documents that break the contract ----------------


def _mutated(mutate) -> list[str]:
    document = copy.deepcopy(_load_document())
    mutate(document)
    return exporter.validate_document(document, load_ruleset(DATA_DIR))


def _drop_slot(document: dict[str, Any]) -> None:
    del document["slots"]["compensation"]


def _extra_slot(document: dict[str, Any]) -> None:
    document["slots"]["extra_slot"] = copy.deepcopy(document["slots"]["schedule"])


def _wrong_label(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["label"] = "일정"


def _unknown_component(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["components"]["not_a_component"] = "접수 기간은 ○○입니다."


def _missing_required_component(document: dict[str, Any]) -> None:
    del document["slots"]["result_notice"]["components"]["notice_time"]


def _empty_text(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["text"] = "  "


def _no_blank(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["text"] = "접수 기간을 안내합니다."


def _long_blank_run(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["text"] += " " + "○" * 3


def _text_too_long(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["text"] = "접수 기간 ○○ " + "가" * 600


def _component_too_long(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["components"]["change_notice"] = "별도 공지 ○○ " + "가" * 200


def _email_in_text(document: dict[str, Any]) -> None:
    address = "".join(["name", "@", "example", ".org"])
    document["slots"]["contact_point"]["text"] += f"\n이메일: {address} ○○"


def _phone_in_component(document: dict[str, Any]) -> None:
    number = "-".join(["02", "123", "4567"])
    document["slots"]["contact_point"]["components"]["phone"] = f"문의 전화: {number}"


def _verdict_word(document: dict[str, Any]) -> None:
    document["slots"]["evaluation_criteria"]["text"] += "\n합격 점수는 ○○입니다."


def _condition_not_text(document: dict[str, Any]) -> None:
    document["slots"]["ai_disclosure"]["condition"] = 3


def _unknown_source(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["sources"].append(
        {"title": "존재하지 않는 자료", "locator": ""}
    )


def _invented_article(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["sources"][0]["locator"] = "제99조(채용일정 및 채용과정의 고지)"


def _invented_pages(document: dict[str, Any]) -> None:
    document["slots"]["selection_stages"]["sources"][0]["locator"] = "999쪽"


def _extra_key(document: dict[str, Any]) -> None:
    document["slots"]["schedule"]["score"] = 3


def _wrong_placeholder(document: dict[str, Any]) -> None:
    document["placeholder"] = "OO"


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (_drop_slot, "누락: compensation"),
        (_extra_slot, "추가: extra_slot"),
        (_wrong_label, "label이 data/slots.yaml과 다릅니다"),
        (_unknown_component, "없는 component id"),
        (_missing_required_component, "필수 component 누락"),
        (_empty_text, "schedule/text"),
        (_no_blank, "빈칸"),
        (_long_blank_run, "세 개 이상"),
        (_text_too_long, "600자를 넘습니다"),
        (_component_too_long, "200자를 넘습니다"),
        (_email_in_text, "개인정보 패턴"),
        (_phone_in_component, "개인정보 패턴"),
        (_verdict_word, "쓸 수 없는 표현"),
        (_condition_not_text, "ai_disclosure/condition"),
        (_unknown_source, "저장소 data에 없는 자료명"),
        (_invented_article, "조문이 없습니다"),
        (_invented_pages, "근거 쪽수로 기록되지 않은 쪽"),
        (_extra_key, "label, text, condition, components, sources"),
        (_wrong_placeholder, "placeholder"),
    ],
)
def test_validation_rejects_contract_violations(mutate, fragment: str) -> None:
    errors = _mutated(mutate)
    assert any(fragment in error for error in errors), errors


def test_exporter_exits_with_failure_for_an_invalid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_dir)
    broken = _load_document()
    broken["slots"]["schedule"]["label"] = "일정"
    (data_dir / "posting_templates.yaml").write_text(
        yaml.safe_dump(broken, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    output = tmp_path / "out.js"

    assert exporter.main(["--data-dir", str(data_dir), "--output", str(output)]) == 1
    assert not output.exists()
    assert "label" in capsys.readouterr().err


# --- 3. the example file must not disturb the frozen ruleset ------------------


def test_ruleset_version_matches_the_web_bundle(engine: FairpostEngine) -> None:
    # The candidate version is read from the generated web bundle rather than
    # hard-coded, so a deliberate rule change cannot silently diverge from it.
    assert engine.ruleset.version == _web_data_version()


def test_loader_neither_reads_nor_depends_on_the_template_file(
    engine: FairpostEngine, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, data_dir)
    (data_dir / "posting_templates.yaml").unlink()

    without = load_ruleset(data_dir)
    with_templates = load_ruleset(DATA_DIR)
    assert without.version == with_templates.version == engine.ruleset.version
    assert without.matching_version == with_templates.matching_version
    assert without.rules == with_templates.rules
    assert without.slots == with_templates.slots


def test_data_validation_tool_still_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/validate_data.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr


# --- 4. detection: the engine must recognise what a template adds -------------


@pytest.mark.parametrize("base_name", sorted(BASE_POSTINGS))
def test_base_postings_contain_no_slot(engine: FairpostEngine, base_name: str) -> None:
    result = engine.check(BASE_POSTINGS[base_name])
    assert _found_slots(result) == set()
    assert result.findings == []


@pytest.mark.parametrize("base_name", sorted(BASE_POSTINGS))
@pytest.mark.parametrize("slot_id", SLOT_IDS)
def test_template_makes_its_slot_found_without_new_law_findings(
    engine: FairpostEngine, slot_id: str, base_name: str
) -> None:
    base = BASE_POSTINGS[base_name]
    before = engine.check(base)
    assert slot_id not in _found_slots(before)

    text = DOCUMENT["slots"][slot_id]["text"]
    after = engine.check(_append(base, text))

    assert slot_id in _found_slots(after), (
        f"{slot_id}: 템플릿 문구가 탐지되지 않습니다"
    )
    assert _finding_ids(after) - _finding_ids(before) == set(), (
        f"{slot_id}: 새 법령 finding {sorted(_finding_ids(after) - _finding_ids(before))}"
    )
    leaked = _found_slots(after) - {slot_id} - ALLOWED_CROSS_DETECTION.get(slot_id, set())
    assert leaked == set(), f"{slot_id}: 다른 슬롯까지 채운 것으로 탐지됩니다 {sorted(leaked)}"


@pytest.mark.parametrize("slot_id", SLOT_IDS)
def test_template_text_alone_is_detected(engine: FairpostEngine, slot_id: str) -> None:
    result = engine.check(DOCUMENT["slots"][slot_id]["text"])
    assert slot_id in _found_slots(result)
    assert result.findings == []


# --- 5. components: a supplement adds exactly the element it names ------------


@pytest.mark.parametrize(("slot_id", "component_id"), COMPONENT_CASES)
def test_component_sentence_is_detected_as_its_component(
    engine: FairpostEngine, slot_id: str, component_id: str
) -> None:
    entry = DOCUMENT["slots"][slot_id]
    sentence = _fill_component(slot_id, component_id, entry["components"][component_id])
    base = BASE_POSTINGS["minimal"]
    before = engine.check(base)

    alone = engine.check(_append(base, sentence))
    assert component_id in _slot_status(alone, slot_id).components_found
    assert _finding_ids(alone) - _finding_ids(before) == set()
    leaked = _found_slots(alone) - {slot_id}
    assert leaked == set(), f"{slot_id}/{component_id}: 다른 슬롯 탐지 {sorted(leaked)}"

    # The realistic partial-fix flow: the slot text is already in the posting but
    # lacks this component, and the supplement is added.
    partial = engine.check(_append(base, entry["text"]))
    if component_id in _slot_status(partial, slot_id).components_found:
        return  # the slot text already carries it, nothing is missing
    supplemented = engine.check(_append(_append(base, entry["text"]), sentence))
    assert slot_id in _found_slots(supplemented)
    assert component_id in _slot_status(supplemented, slot_id).components_found
    assert _finding_ids(supplemented) - _finding_ids(before) == set()


def test_templates_leave_digit_backed_components_for_the_organisation(
    engine: FairpostEngine,
) -> None:
    # Phone, e-mail, amount and dates need real values, which a template must not
    # carry; the engine must therefore still report them as missing until filled.
    for slot_id, component_id in SYNTHETIC_FILLS:
        text = DOCUMENT["slots"][slot_id]["text"]
        result = engine.check(_append(BASE_POSTINGS["minimal"], text))
        assert component_id not in _slot_status(result, slot_id).components_found


@pytest.mark.parametrize("slot_id", SLOT_IDS)
def test_every_declared_component_can_be_completed_from_the_template(
    engine: FairpostEngine, slot_id: str
) -> None:
    # Text plus all of its supplements (with real values substituted for the three
    # blanks that need digits or addresses) must cover every component the slot defines.
    entry = DOCUMENT["slots"][slot_id]
    parts = [entry["text"]] + [
        _fill_component(slot_id, component_id, sentence)
        for component_id, sentence in entry["components"].items()
    ]
    result = engine.check(_append(BASE_POSTINGS["minimal"], "\n".join(parts)))
    defined = {c["id"] for c in engine.ruleset.slots[slot_id]["components"]}
    assert set(_slot_status(result, slot_id).components_found) == defined


# --- 6. the JavaScript engine must see exactly what the Python engine sees ----


def _web_check(text: str) -> dict[str, Any]:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        ["node", "tests/js_runner.cjs", encoded],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _full_text(slot_id: str) -> str:
    entry = DOCUMENT["slots"][slot_id]
    parts = [entry["text"]] + [
        _fill_component(slot_id, component_id, sentence)
        for component_id, sentence in entry["components"].items()
    ]
    return _append(BASE_POSTINGS["minimal"], "\n".join(parts))


@pytest.mark.requires_node
@pytest.mark.parametrize("slot_id", SLOT_IDS)
def test_web_engine_agrees_on_template_text(engine: FairpostEngine, slot_id: str) -> None:
    text = _append(BASE_POSTINGS["minimal"], DOCUMENT["slots"][slot_id]["text"])
    web_result = _web_check(text)
    python_result = engine.check(text).to_dict()

    web_found = {slot["slot"]: slot["found"] for slot in web_result["slots"]}
    python_found = {slot.slot: slot.found for slot in engine.check(text).slots}
    assert web_found == python_found
    assert web_found[slot_id] is True
    assert web_result == python_result


@pytest.mark.requires_node
@pytest.mark.parametrize("slot_id", SLOT_IDS)
def test_web_engine_agrees_on_template_with_all_supplements(
    engine: FairpostEngine, slot_id: str
) -> None:
    text = _full_text(slot_id)
    assert _web_check(text) == engine.check(text).to_dict()


@pytest.mark.requires_node
def test_generated_bundle_loads_in_a_browser_like_context() -> None:
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "const context={window:{}};context.globalThis=context.window;"
        "vm.createContext(context);"
        "vm.runInContext(fs.readFileSync('web/posting-templates.js','utf8'),context);"
        "process.stdout.write(JSON.stringify(context.window.FAIRPOST_POSTING_TEMPLATES));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(completed.stdout) == _parse_bundle(
        BUNDLE_PATH.read_text(encoding="utf-8")
    )


# --- 7. wording and data-hygiene guards ----------------------------------------


def _all_copy(bundle: dict[str, Any]) -> list[str]:
    strings = [bundle["notice"]]
    for entry in bundle["slots"].values():
        strings.append(entry["label"])
        strings.append(entry["text"])
        if entry["condition"]:
            strings.append(entry["condition"])
        strings.extend(entry["components"].values())
        for source in entry["sources"]:
            strings.extend([source["title"], source["locator"]])
    return strings


def test_no_personal_data_patterns_in_sources_or_bundle() -> None:
    for path in (TEMPLATES_PATH, BUNDLE_PATH, Path(__file__)):
        payload = path.read_bytes()
        hits = [name for name, pattern in ASSET_PRIVACY_PATTERNS.items() if pattern.search(payload)]
        assert hits == [], f"{path.name}: {hits}"


def test_no_verdict_or_assurance_wording() -> None:
    bundle = _parse_bundle(BUNDLE_PATH.read_text(encoding="utf-8"))
    for word in VERDICT_WORDS:
        assert word in exporter.FORBIDDEN_TERMS
        for copy_text in _all_copy(bundle):
            assert word not in copy_text, f"{word!r} in {copy_text!r}"


def test_blanks_are_exactly_two_circles_and_bounded() -> None:
    bundle = _parse_bundle(BUNDLE_PATH.read_text(encoding="utf-8"))
    for slot_id, entry in bundle["slots"].items():
        assert "○○" in entry["text"], slot_id
        assert len(entry["text"]) <= 600, slot_id
        for sentence in entry["components"].values():
            assert len(sentence) <= 200
        for copy_text in [entry["text"], *entry["components"].values()]:
            assert "○○○" not in copy_text
            assert "○" * 3 not in copy_text


def test_sources_cite_only_material_already_in_the_repository(
    engine: FairpostEngine,
) -> None:
    bundle = _parse_bundle(BUNDLE_PATH.read_text(encoding="utf-8"))
    known = exporter.known_sources(engine.ruleset)
    for slot_id, entry in bundle["slots"].items():
        for source in entry["sources"]:
            assert source["title"] in known, (slot_id, source)

    def locators(slot_id: str) -> set[tuple[str, str]]:
        return {(s["title"], s["locator"]) for s in bundle["slots"][slot_id]["sources"]}

    act = "채용절차의 공정화에 관한 법률"
    assert (act, "제10조(채용 여부의 고지)") in locators("result_notice")
    assert (act, "제11조(채용서류의 반환 등)") in locators("document_return")
    assert (act, "제8조(채용일정 및 채용과정의 고지)") in locators("schedule")
    assert ("개인정보 보호법", "제37조의2(자동화된 결정에 대한 정보주체의 권리 등)") in locators(
        "ai_disclosure"
    )
