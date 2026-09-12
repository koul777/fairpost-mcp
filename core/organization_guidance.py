from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .loader import DEFAULT_DATA_DIR, RuleLoadError


ORGANIZATION_GUIDANCE_SCHEMA_VERSION = "fairpost-organization-applicability-v1"


@dataclass(frozen=True)
class OrganizationGuidanceSource:
    id: str
    title: str
    publisher: str
    source_url: str
    published_or_updated_at: str
    source_type: str


@dataclass(frozen=True)
class OrganizationGuidanceProfile:
    id: str
    sector: str
    label: str
    applicability_label: str
    statement: str
    source_ids: list[str]


@dataclass(frozen=True)
class OrganizationGuidanceCatalog:
    schema_version: str
    catalog_version: str
    as_of: str
    authority: str
    sources: tuple[OrganizationGuidanceSource, ...]
    profiles: tuple[OrganizationGuidanceProfile, ...]
    limitations: tuple[str, ...]

    def context_for(self, profile_id: str) -> dict[str, Any] | None:
        profile = next((item for item in self.profiles if item.id == profile_id), None)
        if profile is None:
            return None
        source_ids = set(profile.source_ids)
        return {
            "profile": asdict(profile),
            "sources": [
                asdict(source) for source in self.sources if source.id in source_ids
            ],
            "authority": self.authority,
            "as_of": self.as_of,
            "limitations": list(self.limitations),
        }

    def to_web_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "catalog_version": self.catalog_version,
            "as_of": self.as_of,
            "authority": self.authority,
            "sources": [asdict(source) for source in self.sources],
            "profiles": [asdict(profile) for profile in self.profiles],
            "limitations": list(self.limitations),
        }


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
        raise RuleLoadError(f"{context}: {field}는 YYYY-MM-DD 날짜여야 합니다") from exc
    return value


def _string_list(payload: dict[str, Any], field: str, *, context: str) -> list[str]:
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


def _official_url(value: str, *, context: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    allowed = host in {"law.go.kr", "www.law.go.kr", "alio.go.kr", "www.alio.go.kr"}
    if parsed.scheme != "https" or not allowed:
        raise RuleLoadError(f"{context}: 법제처 또는 ALIO 공식 HTTPS URL이어야 합니다")
    if parsed.username or parsed.password or parsed.fragment:
        raise RuleLoadError(f"{context}: 사용자정보나 fragment가 있는 URL은 허용되지 않습니다")
    return value


def load_organization_guidance_catalog(
    data_dir: str | Path | None = None,
) -> OrganizationGuidanceCatalog:
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    path = root / "guidance" / "organization-applicability.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuleLoadError(f"{path}: 조직 적용 카탈로그를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuleLoadError(f"{path}: 최상위 값은 객체여야 합니다")
    if payload.get("schema_version") != ORGANIZATION_GUIDANCE_SCHEMA_VERSION:
        raise RuleLoadError(f"{path}: 지원하지 않는 schema_version")

    as_of = _date_text(payload, "as_of", context=str(path))
    authority = _required_text(payload, "authority", context=str(path))
    if authority != "applicability_aid_not_legal_advice":
        raise RuleLoadError(f"{path}: authority 값이 올바르지 않습니다")

    source_rows = payload.get("sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise RuleLoadError(f"{path}: sources가 비어 있습니다")
    sources: list[OrganizationGuidanceSource] = []
    seen_sources: set[str] = set()
    for index, row in enumerate(source_rows):
        context = f"{path}/sources/{index}"
        if not isinstance(row, dict):
            raise RuleLoadError(f"{context}: 객체여야 합니다")
        source_id = _required_text(row, "id", context=context)
        if source_id in seen_sources:
            raise RuleLoadError(f"{context}: 중복 source id '{source_id}'")
        seen_sources.add(source_id)
        source_type = _required_text(row, "source_type", context=context)
        if source_type not in {"law", "guidance"}:
            raise RuleLoadError(f"{context}: source_type은 law/guidance여야 합니다")
        sources.append(
            OrganizationGuidanceSource(
                id=source_id,
                title=_required_text(row, "title", context=context),
                publisher=_required_text(row, "publisher", context=context),
                source_url=_official_url(
                    _required_text(row, "source_url", context=context),
                    context=context,
                ),
                published_or_updated_at=_date_text(
                    row, "published_or_updated_at", context=context
                ),
                source_type=source_type,
            )
        )

    profile_rows = payload.get("profiles")
    if not isinstance(profile_rows, list) or not profile_rows:
        raise RuleLoadError(f"{path}: profiles가 비어 있습니다")
    profiles: list[OrganizationGuidanceProfile] = []
    seen_profiles: set[str] = set()
    for index, row in enumerate(profile_rows):
        context = f"{path}/profiles/{index}"
        if not isinstance(row, dict):
            raise RuleLoadError(f"{context}: 객체여야 합니다")
        profile_id = _required_text(row, "id", context=context)
        if profile_id in seen_profiles:
            raise RuleLoadError(f"{context}: 중복 profile id '{profile_id}'")
        seen_profiles.add(profile_id)
        sector = _required_text(row, "sector", context=context)
        if sector != "public":
            raise RuleLoadError(f"{context}: 현재 조직 적용 프로필은 public만 허용합니다")
        source_ids = _string_list(row, "source_ids", context=context)
        missing_sources = sorted(set(source_ids) - seen_sources)
        if missing_sources:
            raise RuleLoadError(
                f"{context}: 존재하지 않는 source id: {', '.join(missing_sources)}"
            )
        profiles.append(
            OrganizationGuidanceProfile(
                id=profile_id,
                sector=sector,
                label=_required_text(row, "label", context=context),
                applicability_label=_required_text(
                    row, "applicability_label", context=context
                ),
                statement=_required_text(row, "statement", context=context),
                source_ids=source_ids,
            )
        )

    limitations = _string_list(payload, "limitations", context=str(path))
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    catalog_version = "organization-guidance-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:16]
    return OrganizationGuidanceCatalog(
        schema_version=ORGANIZATION_GUIDANCE_SCHEMA_VERSION,
        catalog_version=catalog_version,
        as_of=as_of,
        authority=authority,
        sources=tuple(sources),
        profiles=tuple(profiles),
        limitations=tuple(limitations),
    )
