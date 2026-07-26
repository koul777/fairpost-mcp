from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import yaml

from .extractor import SECTION_VERSION
from .morph import MORPH_VERSION


class RuleLoadError(ValueError):
    pass


ALLOWED_SECTIONS = {
    "전체",
    "개요",
    "자격요건",
    "우대사항",
    "전형절차",
    "일정",
    "근무조건",
    "제출서류",
    "유의사항",
    "문의처",
    "기타",
}
REQUIRED_SLOT_IDS = {
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
}
CONTACT_COMPONENT_IDS = {"department", "phone", "email", "hours"}
MATCH_ENGINE_VERSION = "engine-v1-first-match-exclude-section-slots"
REQUIRED_STATUTE_ARTICLES = {
    "age-discrimination-act": {"제4조의4", "제4조의5"},
    "disability-employment-act": {"제5조"},
    "equal-employment-act": {"제7조"},
    "national-human-rights-act": {"제2조"},
    "personal-information-act": {"제16조", "제37조의2"},
    "recruitment-procedure-act": {
        "제4조",
        "제4조의3",
        "제8조",
        "제9조",
        "제10조",
        "제11조",
    },
}


@dataclass(frozen=True)
class Ruleset:
    rules: tuple[dict[str, Any], ...]
    slots: dict[str, Any]
    statutes: dict[str, Any]
    version: str
    matching_version: str


def _default_data_dir() -> Path:
    configured = os.environ.get("FAIRPOST_DATA_DIR")
    if configured:
        return Path(configured)

    source_data = Path(__file__).resolve().parent.parent / "data"
    if source_data.exists():
        return source_data

    return Path(sys.prefix) / "share" / "fairpost" / "data"


DEFAULT_DATA_DIR = _default_data_dir()


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise RuleLoadError(f"{path}: 읽을 수 없는 YAML: {exc}") from exc


def _parse_date(value: Any, *, context: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RuleLoadError(f"{context}: 날짜 형식은 YYYY-MM-DD여야 합니다") from exc


def _validate_statutes(statutes: dict[str, Any]) -> None:
    missing_statutes = sorted(REQUIRED_STATUTE_ARTICLES.keys() - statutes.keys())
    if missing_statutes:
        raise RuleLoadError(
            "PRD 대상 법령 스냅샷 누락: " + ", ".join(missing_statutes)
        )
    for statute_id, statute in statutes.items():
        required = {"id", "name", "articles", "snapshot_date", "source", "retrieved_via"}
        missing = sorted(required - statute.keys())
        if missing:
            raise RuleLoadError(f"{statute_id}: 조문 스냅샷 필수 필드 누락: {', '.join(missing)}")
        if statute["id"] != statute_id:
            raise RuleLoadError(f"{statute_id}: 파일 id와 스냅샷 id가 일치하지 않습니다")
        if not isinstance(statute["articles"], dict) or not statute["articles"]:
            raise RuleLoadError(f"{statute_id}: articles는 비어 있지 않은 객체여야 합니다")
        required_articles = REQUIRED_STATUTE_ARTICLES.get(statute_id, set())
        missing_articles = sorted(required_articles - statute["articles"].keys())
        if missing_articles:
            raise RuleLoadError(
                f"{statute_id}: PRD 검수 대상 조문 누락: {', '.join(missing_articles)}"
            )
        snapshot_date = _parse_date(
            statute["snapshot_date"], context=f"{statute_id}/snapshot_date"
        )
        for article, payload in statute["articles"].items():
            if not isinstance(payload, dict):
                raise RuleLoadError(f"{statute_id}/{article}: 조문은 객체여야 합니다")
            missing_article = sorted(
                {"title", "text", "effective_date", "hash"} - payload.keys()
            )
            if missing_article:
                raise RuleLoadError(
                    f"{statute_id}/{article}: 필수 필드 누락: {', '.join(missing_article)}"
                )
            if not isinstance(payload["text"], str) or not payload["text"].strip():
                raise RuleLoadError(f"{statute_id}/{article}: 조문 원문이 비어 있습니다")
            effective = _parse_date(
                payload["effective_date"], context=f"{statute_id}/{article}/effective_date"
            )
            if effective > snapshot_date:
                raise RuleLoadError(f"{statute_id}/{article}: 아직 시행되지 않은 조문입니다")
            expected = "sha256:" + hashlib.sha256(payload["text"].encode("utf-8")).hexdigest()
            if payload.get("hash") != expected:
                raise RuleLoadError(f"{statute_id}/{article}: 조문 해시가 일치하지 않습니다")


def _validate_rule(
    rule: dict[str, Any],
    statutes: dict[str, Any],
    *,
    local: bool,
) -> None:
    rule_id = rule.get("id", "<id 없음>")
    for field in ("id", "layer", "trigger", "dimension", "basis", "provenance", "book_ref"):
        if field not in rule:
            raise RuleLoadError(f"{rule_id}: 필수 필드 '{field}' 누락")
    if rule["layer"] not in {"law", "question"}:
        raise RuleLoadError(f"{rule_id}: layer는 law 또는 question이어야 합니다")
    if not isinstance(rule["book_ref"], str) or not rule["book_ref"].strip():
        raise RuleLoadError(f"{rule_id}: book_ref가 비어 있습니다")
    if not isinstance(rule["trigger"], dict):
        raise RuleLoadError(f"{rule_id}: trigger는 객체여야 합니다")
    if rule["trigger"].get("type") not in {"presence", "absence"}:
        raise RuleLoadError(f"{rule_id}: trigger.type 오류")
    if rule["trigger"]["type"] == "presence":
        patterns = rule["trigger"].get("patterns")
        if (
            not isinstance(patterns, list)
            or not patterns
            or any(not isinstance(pattern, str) or not pattern for pattern in patterns)
        ):
            raise RuleLoadError(
                f"{rule_id}: presence trigger에는 비어 있지 않은 문자열 patterns가 필요합니다"
            )
    if rule["trigger"]["type"] == "absence" and not rule["trigger"].get("field"):
        raise RuleLoadError(f"{rule_id}: absence trigger에는 field가 필요합니다")
    section_scope = rule["trigger"].get("section_scope")
    if section_scope is not None and section_scope not in ALLOWED_SECTIONS:
        raise RuleLoadError(f"{rule_id}: 알 수 없는 section_scope '{section_scope}'")
    if rule["dimension"] not in {"분배", "절차", "대인", "정보"}:
        raise RuleLoadError(f"{rule_id}: 알 수 없는 dimension")
    if not isinstance(rule["basis"], dict):
        raise RuleLoadError(f"{rule_id}: basis는 객체여야 합니다")
    basis_type = rule["basis"].get("type")
    if local and basis_type != "consensus":
        raise RuleLoadError(f"{rule_id}: local_rules는 basis.type consensus만 허용합니다")
    if not isinstance(rule["provenance"], dict) or not rule["provenance"].get("method"):
        raise RuleLoadError(f"{rule_id}: provenance.method가 필요합니다")
    provenance = rule["provenance"]
    if provenance["method"] not in {"llm_mined", "manual"}:
        raise RuleLoadError(f"{rule_id}: provenance.method 오류")
    if not isinstance(provenance.get("reviewed_by"), str) or not provenance[
        "reviewed_by"
    ].strip():
        raise RuleLoadError(f"{rule_id}: provenance.reviewed_by가 필요합니다")
    if not provenance.get("reviewed_at"):
        raise RuleLoadError(f"{rule_id}: provenance.reviewed_at이 필요합니다")
    _parse_date(provenance["reviewed_at"], context=f"{rule_id}/provenance.reviewed_at")
    if rule["layer"] == "law" or provenance["method"] == "llm_mined":
        corpus_hits = provenance.get("corpus_hits")
        if (
            not isinstance(corpus_hits, int)
            or isinstance(corpus_hits, bool)
            or corpus_hits < 0
        ):
            raise RuleLoadError(f"{rule_id}: provenance.corpus_hits 오류")
        if provenance.get("corpus_split") not in {"public", "private", "both"}:
            raise RuleLoadError(f"{rule_id}: provenance.corpus_split 오류")

    excludes = rule["trigger"].get("exclude", [])
    if not isinstance(excludes, list):
        raise RuleLoadError(f"{rule_id}: trigger.exclude는 목록이어야 합니다")
    for exclusion in excludes:
        if (
            not isinstance(exclusion, dict)
            or not isinstance(exclusion.get("term"), str)
            or not exclusion["term"]
        ):
            raise RuleLoadError(f"{rule_id}: exclude.term이 필요합니다")
        window = exclusion.get("window", 0)
        if not isinstance(window, int) or isinstance(window, bool) or window < 0:
            raise RuleLoadError(f"{rule_id}: exclude.window는 0 이상의 정수여야 합니다")

    if rule["layer"] == "law":
        if basis_type != "statute":
            raise RuleLoadError(f"{rule_id}: law 규칙은 statute 근거만 허용합니다")
        if rule["trigger"]["type"] != "presence":
            raise RuleLoadError(f"{rule_id}: law 규칙은 원문 offset을 위한 presence만 허용합니다")
        statute_id = rule["basis"].get("statute_id")
        article = rule["basis"].get("article")
        if statute_id not in statutes:
            raise RuleLoadError(f"{rule_id}: 존재하지 않는 statute_id '{statute_id}'")
        if article not in statutes[statute_id]["articles"]:
            raise RuleLoadError(f"{rule_id}: 존재하지 않는 조문 '{article}'")
        if rule["basis"].get("law") != statutes[statute_id]["name"]:
            raise RuleLoadError(f"{rule_id}: 법령명이 스냅샷과 일치하지 않습니다")
        if str(rule["basis"].get("snapshot_date")) != str(
            statutes[statute_id]["snapshot_date"]
        ):
            raise RuleLoadError(f"{rule_id}: snapshot_date가 스냅샷과 일치하지 않습니다")
        if "severity" not in rule or "message" not in rule:
            raise RuleLoadError(f"{rule_id}: law 규칙에는 severity와 message가 필요합니다")
        if rule["severity"] not in {"high", "medium", "low"}:
            raise RuleLoadError(f"{rule_id}: severity 오류")
    else:
        if "severity" in rule:
            raise RuleLoadError(f"{rule_id}: question 규칙에는 severity를 둘 수 없습니다")
        if basis_type not in {"research", "consensus"}:
            raise RuleLoadError(f"{rule_id}: question 근거는 research/consensus만 허용합니다")
        if basis_type == "research":
            for field in ("title", "publisher", "year", "pages"):
                if field not in rule["basis"]:
                    raise RuleLoadError(
                        f"{rule_id}: research 근거에는 basis.{field}가 필요합니다"
                    )
            if not isinstance(rule["basis"]["title"], str) or not rule["basis"][
                "title"
            ].strip():
                raise RuleLoadError(f"{rule_id}: basis.title이 비어 있습니다")
            if not isinstance(rule["basis"]["publisher"], str) or not rule["basis"][
                "publisher"
            ].strip():
                raise RuleLoadError(f"{rule_id}: basis.publisher가 비어 있습니다")
            if (
                not isinstance(rule["basis"]["year"], int)
                or isinstance(rule["basis"]["year"], bool)
                or rule["basis"]["year"] < 2000
            ):
                raise RuleLoadError(f"{rule_id}: basis.year 오류")
            pages = rule["basis"]["pages"]
            if (
                not isinstance(pages, list)
                or not pages
                or any(
                    not isinstance(page, int)
                    or isinstance(page, bool)
                    or page < 1
                    for page in pages
                )
            ):
                raise RuleLoadError(f"{rule_id}: basis.pages는 양의 정수 목록이어야 합니다")
        if not rule.get("question"):
            raise RuleLoadError(f"{rule_id}: question 문구가 필요합니다")


def load_ruleset(
    data_dir: str | Path | None = None,
    local_rules_path: str | Path | None = None,
) -> Ruleset:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    statute_files = sorted((root / "statutes").glob("*.yaml"))
    if not statute_files:
        raise RuleLoadError(f"{root / 'statutes'}: 조문 스냅샷이 없습니다")

    statutes: dict[str, Any] = {}
    for path in statute_files:
        statute = _load_yaml(path)
        statute_id = statute.get("id")
        if not statute_id:
            raise RuleLoadError(f"{path}: statute id 누락")
        if statute_id in statutes:
            raise RuleLoadError(f"{path}: 중복 statute id '{statute_id}'")
        statutes[statute_id] = statute
    _validate_statutes(statutes)

    rules: list[dict[str, Any]] = []
    for name in ("law.yaml", "questions.yaml"):
        payload = _load_yaml(root / "rules" / name)
        if not isinstance(payload, list):
            raise RuleLoadError(f"{name}: 최상위 값은 목록이어야 합니다")
        rules.extend(payload)

    configured_local_rules = local_rules_path or os.environ.get(
        "FAIRPOST_LOCAL_RULES_PATH"
    )
    if configured_local_rules:
        local_payload = _load_yaml(Path(configured_local_rules))
        if not isinstance(local_payload, list):
            raise RuleLoadError("local_rules: 최상위 값은 목록이어야 합니다")
        for rule in local_payload:
            _validate_rule(rule, statutes, local=True)
        rules.extend(local_payload)

    seen: set[str] = set()
    for rule in rules:
        _validate_rule(rule, statutes, local=False)
        if rule["id"] in seen:
            raise RuleLoadError(f"{rule['id']}: 중복 id")
        seen.add(rule["id"])

    slots = _load_yaml(root / "slots.yaml")
    if not isinstance(slots, dict):
        raise RuleLoadError("slots.yaml: 최상위 값은 객체여야 합니다")
    if set(slots) != REQUIRED_SLOT_IDS:
        missing = ", ".join(sorted(REQUIRED_SLOT_IDS - set(slots))) or "없음"
        extra = ", ".join(sorted(set(slots) - REQUIRED_SLOT_IDS)) or "없음"
        raise RuleLoadError(
            f"slots.yaml: PRD의 11개 슬롯과 일치하지 않습니다 "
            f"(누락: {missing}, 추가: {extra})"
        )
    for slot_id, slot in slots.items():
        if not isinstance(slot, dict) or not slot.get("label"):
            raise RuleLoadError(f"{slot_id}: 슬롯 label이 필요합니다")
        if not isinstance(slot.get("accept_patterns"), list) or not slot[
            "accept_patterns"
        ]:
            raise RuleLoadError(f"{slot_id}: accept_patterns가 필요합니다")
        if any(
            not isinstance(pattern, str) or not pattern
            for pattern in slot["accept_patterns"]
        ):
            raise RuleLoadError(f"{slot_id}: accept_patterns는 비어 있지 않은 문자열이어야 합니다")
        search_sections = slot.get("search_sections", [])
        if (
            not isinstance(search_sections, list)
            or any(section not in ALLOWED_SECTIONS for section in search_sections)
        ):
            raise RuleLoadError(f"{slot_id}: search_sections 오류")
        components = slot.get("components", [])
        if not isinstance(components, list):
            raise RuleLoadError(f"{slot_id}: components는 목록이어야 합니다")
        component_ids: set[str] = set()
        for component in components:
            if (
                not isinstance(component, dict)
                or not isinstance(component.get("id"), str)
                or not isinstance(component.get("patterns"), list)
                or not component["patterns"]
            ):
                raise RuleLoadError(f"{slot_id}: component id와 patterns가 필요합니다")
            if component["id"] in component_ids:
                raise RuleLoadError(
                    f"{slot_id}: 중복 component id '{component['id']}'"
                )
            component_ids.add(component["id"])
            if any(
                not isinstance(pattern, str) or not pattern
                for pattern in component["patterns"]
            ):
                raise RuleLoadError(
                    f"{slot_id}/{component['id']}: patterns는 비어 있지 않은 문자열이어야 합니다"
                )
        if slot_id == "contact_point" and component_ids != CONTACT_COMPONENT_IDS:
            raise RuleLoadError(
                "contact_point: 구성요소는 department/phone/email/hours 4개여야 합니다"
            )
    for rule in rules:
        if rule["trigger"]["type"] == "absence" and rule["trigger"]["field"] not in slots:
            raise RuleLoadError(f"{rule['id']}: 존재하지 않는 슬롯 '{rule['trigger']['field']}'")

    canonical = json.dumps(
        {"rules": rules, "slots": slots, "statutes": statutes, "morph": MORPH_VERSION},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    version = f"2026.07-{MORPH_VERSION}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}"
    matching_canonical = json.dumps(
        {
            "rules": [
                {
                    "id": rule["id"],
                    "layer": rule["layer"],
                    "trigger": rule["trigger"],
                }
                for rule in rules
            ],
            "slots": slots,
            "morph": MORPH_VERSION,
            "sections": SECTION_VERSION,
            "engine": MATCH_ENGINE_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    matching_version = (
        "match-"
        + hashlib.sha256(matching_canonical.encode("utf-8")).hexdigest()[:16]
    )
    return Ruleset(tuple(rules), slots, statutes, version, matching_version)
