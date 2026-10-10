from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loader import Ruleset, load_ruleset  # noqa: E402
from tools.verify_distribution import ASSET_PRIVACY_PATTERNS  # noqa: E402

SCHEMA_VERSION = "fairpost-posting-templates-v1"
PLACEHOLDER = "○○"
TEMPLATES_FILENAME = "posting_templates.yaml"
DEFAULT_OUTPUT = ROOT / "web" / "posting-templates.js"

MAX_TEXT_CHARS = 600
MAX_TEXT_LINES = 8
MAX_COMPONENT_CHARS = 200

DOCUMENT_KEYS = {"schema_version", "notice", "placeholder", "slots"}
SLOT_KEYS = {"label", "text", "condition", "components", "sources"}
SOURCE_KEYS = {"title", "locator"}

# The UI relies on these to offer partial (single-component) supplements.
REQUIRED_COMPONENTS: dict[str, frozenset[str]] = {
    "compensation": frozenset({"amount_or_range"}),
    "result_notice": frozenset({"notice_time", "notice_audience"}),
    "schedule": frozenset({"application_date", "assessment_date"}),
}

# Examples are starting points for facts, never verdicts or assurances. These
# words are rejected anywhere in the generated copy. Protected-attribute words
# are rejected too; the engine's law layer is checked separately by the tests.
FORBIDDEN_TERMS: tuple[str, ...] = (
    "통과",
    "적합 판정",
    "점수",
    "공정함을 보증",
    "보증",
    "판정",
    "적법",
    "합격 보장",
    "성별",
    "연령",
    "나이",
    "혼인",
    "결혼",
    "출신",
)

_BLANK_RUN = re.compile(re.escape(PLACEHOLDER[0]) + "{3,}")
_STATUTE_LOCATOR = re.compile(
    r"^(제\d+조(?:의\d+)?)\(([^()]+)\)(?: 제\d+항)?$"
)
_PAGES_LOCATOR = re.compile(r"^(\d+(?:, \d+)*)쪽$")


class TemplateError(ValueError):
    """Raised when the template file does not satisfy the data contract."""


def load_templates(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateError(f"{path}: 읽을 수 없는 YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise TemplateError(f"{path}: 최상위 값은 객체여야 합니다")
    return document


def known_sources(ruleset: Ruleset) -> dict[str, dict[str, Any]]:
    """Collect the source titles that already exist in the data directory.

    Statutes map to their articles; research bases map to the pages recorded by
    the questions that cite them. Nothing is invented here, so a template can
    only point at material the repository already carries.
    """
    sources: dict[str, dict[str, Any]] = {}
    for statute in ruleset.statutes.values():
        sources[statute["name"]] = {
            "kind": "statute",
            "articles": {
                article: payload["title"]
                for article, payload in statute["articles"].items()
            },
        }
    for rule in ruleset.rules:
        if rule["layer"] != "question":
            continue
        basis = rule["basis"]
        title = basis.get("title")
        if basis.get("type") != "research" or not title:
            continue
        entry = sources.setdefault(title, {"kind": "research", "pages": set()})
        if entry["kind"] == "research":
            entry["pages"].update(basis.get("pages") or [])
    return sources


def _privacy_kinds(value: str) -> list[str]:
    payload = value.encode("utf-8")
    return sorted(
        name
        for name, pattern in ASSET_PRIVACY_PATTERNS.items()
        if pattern.search(payload)
    )


def _check_copy(value: Any, context: str, errors: list[str]) -> bool:
    """Common checks for any user-facing string. Returns False when unusable."""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{context}: 비어 있지 않은 문자열이어야 합니다")
        return False
    if value != value.strip():
        errors.append(f"{context}: 앞뒤 공백이나 줄바꿈이 있습니다")
    if "\r" in value or "\t" in value:
        errors.append(f"{context}: 탭이나 캐리지 리턴을 쓸 수 없습니다")
    kinds = _privacy_kinds(value)
    if kinds:
        errors.append(f"{context}: 개인정보 패턴이 감지되었습니다 ({', '.join(kinds)})")
    for term in FORBIDDEN_TERMS:
        if term in value:
            errors.append(f"{context}: 쓸 수 없는 표현 '{term}'")
    return True


def _check_blanks(
    value: str, context: str, errors: list[str], *, required: bool
) -> None:
    if required and PLACEHOLDER not in value:
        errors.append(f"{context}: 빈칸 '{PLACEHOLDER}'이 한 개 이상 필요합니다")
    if _BLANK_RUN.search(value):
        errors.append(f"{context}: '{PLACEHOLDER[0]}'을 세 개 이상 이어 쓸 수 없습니다")


def _check_source(
    source: Any,
    context: str,
    known: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        errors.append(f"{context}: title과 locator만 가진 객체여야 합니다")
        return
    title = source["title"]
    locator = source["locator"]
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{context}: title이 비어 있습니다")
        return
    if not isinstance(locator, str):
        errors.append(f"{context}: locator는 문자열이어야 합니다")
        return
    for field, value in (("title", title), ("locator", locator)):
        if value and _privacy_kinds(value):
            errors.append(f"{context}: {field}에 개인정보 패턴이 있습니다")
    entry = known.get(title)
    if entry is None:
        errors.append(f"{context}: 저장소 data에 없는 자료명입니다: {title}")
        return
    if not locator:
        return
    if entry["kind"] == "statute":
        parsed = _STATUTE_LOCATOR.match(locator)
        if parsed is None:
            errors.append(f"{context}: 법령 locator는 '제N조(조문 제목)' 형식이어야 합니다")
        elif entry["articles"].get(parsed.group(1)) != parsed.group(2):
            errors.append(
                f"{context}: {title}에 '{parsed.group(1)}({parsed.group(2)})' 조문이 없습니다"
            )
    else:
        parsed = _PAGES_LOCATOR.match(locator)
        if parsed is None:
            errors.append(f"{context}: 자료 locator는 '39, 47쪽' 형식이어야 합니다")
        else:
            pages = {int(page) for page in parsed.group(1).split(", ")}
            unknown = sorted(pages - entry["pages"])
            if unknown:
                errors.append(
                    f"{context}: 저장소에 근거 쪽수로 기록되지 않은 쪽입니다: {unknown}"
                )


def validate_document(document: dict[str, Any], ruleset: Ruleset) -> list[str]:
    """Return every contract violation; an empty list means the file is valid."""
    errors: list[str] = []
    if set(document) != DOCUMENT_KEYS:
        errors.append(
            "최상위 키는 schema_version, notice, placeholder, slots여야 합니다 "
            f"(현재: {', '.join(sorted(map(str, document)))})"
        )
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version은 {SCHEMA_VERSION}이어야 합니다")
    if document.get("placeholder") != PLACEHOLDER:
        errors.append(f"placeholder는 '{PLACEHOLDER}'이어야 합니다")
    _check_copy(document.get("notice"), "notice", errors)

    slots = document.get("slots")
    if not isinstance(slots, dict):
        errors.append("slots는 객체여야 합니다")
        return errors
    expected_ids = set(ruleset.slots)
    if set(slots) != expected_ids:
        missing = ", ".join(sorted(expected_ids - set(slots))) or "없음"
        extra = ", ".join(sorted(map(str, set(slots) - expected_ids))) or "없음"
        errors.append(f"slots: 슬롯 11개와 정확히 일치해야 합니다 (누락: {missing}, 추가: {extra})")

    known = known_sources(ruleset)
    for slot_id in sorted(expected_ids & set(slots)):
        entry = slots[slot_id]
        definition = ruleset.slots[slot_id]
        if not isinstance(entry, dict) or set(entry) != SLOT_KEYS:
            errors.append(
                f"{slot_id}: label, text, condition, components, sources를 가진 객체여야 합니다"
            )
            continue
        if entry["label"] != definition["label"]:
            errors.append(
                f"{slot_id}: label이 data/slots.yaml과 다릅니다 "
                f"({entry['label']!r} != {definition['label']!r})"
            )

        text = entry["text"]
        if _check_copy(text, f"{slot_id}/text", errors):
            _check_blanks(text, f"{slot_id}/text", errors, required=True)
            if len(text) > MAX_TEXT_CHARS:
                errors.append(f"{slot_id}/text: {MAX_TEXT_CHARS}자를 넘습니다 ({len(text)}자)")
            if len(text.split("\n")) > MAX_TEXT_LINES:
                errors.append(f"{slot_id}/text: {MAX_TEXT_LINES}줄을 넘습니다")

        condition = entry["condition"]
        if condition is not None:
            _check_copy(condition, f"{slot_id}/condition", errors)

        components = entry["components"]
        component_ids = {component["id"] for component in definition.get("components", [])}
        if not isinstance(components, dict):
            errors.append(f"{slot_id}/components: 객체여야 합니다")
        else:
            unknown = sorted(map(str, set(components) - component_ids))
            if unknown:
                errors.append(
                    f"{slot_id}/components: data/slots.yaml에 없는 component id: {', '.join(unknown)}"
                )
            missing_required = sorted(
                REQUIRED_COMPONENTS.get(slot_id, frozenset()) - set(components)
            )
            if missing_required:
                errors.append(
                    f"{slot_id}/components: 필수 component 누락: {', '.join(missing_required)}"
                )
            for component_id, sentence in components.items():
                context = f"{slot_id}/components/{component_id}"
                if _check_copy(sentence, context, errors):
                    # A supplement may state a procedure with nothing to fill in.
                    _check_blanks(sentence, context, errors, required=False)
                    if len(sentence) > MAX_COMPONENT_CHARS:
                        errors.append(
                            f"{context}: {MAX_COMPONENT_CHARS}자를 넘습니다 ({len(sentence)}자)"
                        )

        sources = entry["sources"]
        if not isinstance(sources, list):
            errors.append(f"{slot_id}/sources: 목록이어야 합니다")
        else:
            for index, source in enumerate(sources):
                _check_source(source, f"{slot_id}/sources[{index}]", known, errors)
    return errors


def template_version(document: dict[str, Any]) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def build_payload(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": document["schema_version"],
        "version": template_version(document),
        "notice": document["notice"],
        "placeholder": document["placeholder"],
        "slots": {
            slot_id: {
                "label": entry["label"],
                "text": entry["text"],
                "condition": entry["condition"],
                "components": dict(entry["components"]),
                "sources": [
                    {"title": source["title"], "locator": source["locator"]}
                    for source in entry["sources"]
                ],
            }
            for slot_id, entry in document["slots"].items()
        },
    }


def render_bundle(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"window.FAIRPOST_POSTING_TEMPLATES={serialized};\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="채용공고 예시 문장 파일을 검증하고 정적 웹용 JavaScript로 내보냅니다."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="파일을 수정하지 않고 현재 예시 문장 파일과 일치하는지 확인합니다.",
    )
    args = parser.parse_args(argv)

    source_path = args.data_dir / TEMPLATES_FILENAME
    try:
        ruleset = load_ruleset(args.data_dir)
        document = load_templates(source_path)
        errors = validate_document(document, ruleset)
    except ValueError as exc:
        print(f"데이터 검증 실패: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"데이터 검증 실패: {source_path}", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    payload = build_payload(document)
    output = render_bundle(payload)
    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{args.output}: 읽을 수 없습니다: {exc}", file=sys.stderr)
            return 1
        if current != output:
            print(
                f"{args.output}: 현재 예시 문장 파일과 다릅니다. "
                "tools/export_posting_templates.py를 실행하십시오.",
                file=sys.stderr,
            )
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8", newline="\n")
    print(f"{args.output} ({payload['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
