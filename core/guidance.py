from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .loader import DEFAULT_DATA_DIR, RuleLoadError


GUIDANCE_SCHEMA_VERSION = "fairpost-ncs-guidance-v1"


@dataclass(frozen=True)
class GuidanceSource:
    id: str
    title: str
    publisher: str
    source_url: str
    published_or_updated_at: str | None
    accessed_at: str


@dataclass(frozen=True)
class GuidanceControl:
    id: str
    title: str
    statement: str
    scope: str
    question_ids: list[str]
    source_ids: list[str]


@dataclass(frozen=True)
class GuidanceContext:
    schema_version: str
    catalog_version: str
    as_of: str
    authority: str
    controls: list[GuidanceControl]
    sources: list[GuidanceSource]
    limitations: list[str]


@dataclass(frozen=True)
class GuidanceCatalog:
    schema_version: str
    catalog_version: str
    as_of: str
    authority: str
    controls: tuple[GuidanceControl, ...]
    sources: tuple[GuidanceSource, ...]
    limitations: tuple[str, ...]

    def context_for_questions(self, question_ids: set[str]) -> GuidanceContext:
        controls = [
            control
            for control in self.controls
            if question_ids.intersection(control.question_ids)
        ]
        source_ids = {
            source_id for control in controls for source_id in control.source_ids
        }
        sources = [source for source in self.sources if source.id in source_ids]
        return GuidanceContext(
            schema_version=self.schema_version,
            catalog_version=self.catalog_version,
            as_of=self.as_of,
            authority=self.authority,
            controls=controls,
            sources=sources,
            limitations=list(self.limitations),
        )


def _required_text(payload: dict[str, Any], field: str, *, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuleLoadError(f"{context}: {field}가 비어 있습니다")
    return value.strip()


def _date_text(payload: dict[str, Any], field: str, *, context: str) -> str:
    value = _required_text(payload, field, context=context)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise RuleLoadError(
            f"{context}: {field}는 YYYY-MM-DD 날짜여야 합니다"
        ) from exc
    return value


def _string_list(
    payload: dict[str, Any], field: str, *, context: str
) -> list[str]:
    value = payload.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise RuleLoadError(f"{context}: {field}는 비어 있지 않은 문자열 목록이어야 합니다")
    values = [item.strip() for item in value]
    if len(values) != len(set(values)):
        raise RuleLoadError(f"{context}: {field}에 중복 값이 있습니다")
    return values


def _validate_ncs_url(value: str, *, context: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (
        host == "ncs.go.kr" or host.endswith(".ncs.go.kr")
    ):
        raise RuleLoadError(f"{context}: NCS 공식 HTTPS URL이어야 합니다")
    if parsed.username or parsed.password or parsed.fragment:
        raise RuleLoadError(f"{context}: 사용자정보나 fragment가 있는 URL은 허용되지 않습니다")
    return value


def load_guidance_catalog(
    data_dir: str | Path | None = None,
    *,
    question_ids: set[str] | None = None,
) -> GuidanceCatalog:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    path = root / "guidance" / "ncs-fair-hiring.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuleLoadError(f"{path}: NCS 가이드 카탈로그를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuleLoadError(f"{path}: 최상위 값은 객체여야 합니다")
    if payload.get("schema_version") != GUIDANCE_SCHEMA_VERSION:
        raise RuleLoadError(f"{path}: 지원하지 않는 schema_version")

    as_of = _date_text(payload, "as_of", context=str(path))
    authority = _required_text(payload, "authority", context=str(path))
    if authority != "guidance_not_law":
        raise RuleLoadError(f"{path}: authority는 guidance_not_law여야 합니다")

    source_rows = payload.get("sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise RuleLoadError(f"{path}: sources가 비어 있습니다")
    sources: list[GuidanceSource] = []
    seen_sources: set[str] = set()
    for index, row in enumerate(source_rows):
        context = f"{path}/sources/{index}"
        if not isinstance(row, dict):
            raise RuleLoadError(f"{context}: 객체여야 합니다")
        source_id = _required_text(row, "id", context=context)
        if source_id in seen_sources:
            raise RuleLoadError(f"{context}: 중복 source id '{source_id}'")
        seen_sources.add(source_id)
        published = row.get("published_or_updated_at")
        if published is not None:
            if not isinstance(published, str):
                raise RuleLoadError(f"{context}: published_or_updated_at 형식 오류")
            try:
                date.fromisoformat(published)
            except ValueError as exc:
                raise RuleLoadError(
                    f"{context}: published_or_updated_at 날짜 형식 오류"
                ) from exc
        sources.append(
            GuidanceSource(
                id=source_id,
                title=_required_text(row, "title", context=context),
                publisher=_required_text(row, "publisher", context=context),
                source_url=_validate_ncs_url(
                    _required_text(row, "source_url", context=context),
                    context=context,
                ),
                published_or_updated_at=published,
                accessed_at=_date_text(row, "accessed_at", context=context),
            )
        )

    control_rows = payload.get("controls")
    if not isinstance(control_rows, list) or not control_rows:
        raise RuleLoadError(f"{path}: controls가 비어 있습니다")
    allowed_scopes = {"all_employers_guidance", "public_sector_guidance"}
    controls: list[GuidanceControl] = []
    seen_controls: set[str] = set()
    for index, row in enumerate(control_rows):
        context = f"{path}/controls/{index}"
        if not isinstance(row, dict):
            raise RuleLoadError(f"{context}: 객체여야 합니다")
        control_id = _required_text(row, "id", context=context)
        if control_id in seen_controls:
            raise RuleLoadError(f"{context}: 중복 control id '{control_id}'")
        seen_controls.add(control_id)
        scope = _required_text(row, "scope", context=context)
        if scope not in allowed_scopes:
            raise RuleLoadError(f"{context}: 알 수 없는 scope '{scope}'")
        linked_questions = _string_list(row, "question_ids", context=context)
        if question_ids is not None:
            missing = sorted(set(linked_questions) - question_ids)
            if missing:
                raise RuleLoadError(
                    f"{context}: 존재하지 않는 question id: {', '.join(missing)}"
                )
        linked_sources = _string_list(row, "source_ids", context=context)
        missing_sources = sorted(set(linked_sources) - seen_sources)
        if missing_sources:
            raise RuleLoadError(
                f"{context}: 존재하지 않는 source id: {', '.join(missing_sources)}"
            )
        controls.append(
            GuidanceControl(
                id=control_id,
                title=_required_text(row, "title", context=context),
                statement=_required_text(row, "statement", context=context),
                scope=scope,
                question_ids=linked_questions,
                source_ids=linked_sources,
            )
        )

    limitations = _string_list(payload, "limitations", context=str(path))
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    catalog_version = "guidance-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:16]
    return GuidanceCatalog(
        schema_version=GUIDANCE_SCHEMA_VERSION,
        catalog_version=catalog_version,
        as_of=as_of,
        authority=authority,
        controls=tuple(controls),
        sources=tuple(sources),
        limitations=tuple(limitations),
    )
