from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import yaml


LAW_API = "https://www.law.go.kr/DRF/lawService.do"
USER_AGENT = "fairpost-statute-builder/0.3 (+monthly snapshot audit)"
ARTICLE_TEXT_TAGS = {
    "조문내용",
    "항내용",
    "호내용",
    "목내용",
    "조문참고자료",
}


def article_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def process(path: Path, *, refresh: bool) -> list[str]:
    """Validate stored hashes without using the network."""
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    errors: list[str] = []
    changed = False
    for article, item in payload.get("articles", {}).items():
        expected = article_hash(item["text"])
        if item.get("hash") != expected:
            if refresh:
                item["hash"] = expected
                changed = True
            else:
                errors.append(f"{path.name}/{article}: hash mismatch")

    if changed:
        _write_yaml(path, payload)
    return errors


def _write_yaml(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(
            payload,
            handle,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )


def _article_key(node: ET.Element) -> str:
    number = (node.findtext("조문번호") or "").strip()
    branch = (node.findtext("조문가지번호") or "").strip()
    if not number:
        raise ValueError("공식 조문 응답에 조문번호가 없습니다")
    return f"제{number}조" + (f"의{branch}" if branch and branch != "0" else "")


def _official_article_text(node: ET.Element) -> str:
    parts: list[str] = []
    for child in node.iter():
        if child.tag not in ARTICLE_TEXT_TAGS or not child.text:
            continue
        value = child.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if value:
            parts.append(value)
    if not parts:
        raise ValueError(f"{_article_key(node)} 공식 조문 본문이 비어 있습니다")
    return "\n\n".join(parts)


def _iso_basic_date(value: str, *, context: str) -> str:
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"{context}: 공식 시행일자가 YYYYMMDD 형식이 아닙니다")
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def fetch_official_law(law_name: str, oc: str) -> ET.Element:
    if not oc.strip():
        raise ValueError(
            "공식 조문 조회에는 LAW_OPEN_API_OC 환경변수 또는 --oc가 필요합니다"
        )
    query = urlencode(
        {
            "OC": oc,
            "target": "law",
            "type": "XML",
            "LM": law_name,
        }
    )
    request = Request(f"{LAW_API}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read()
    except Exception as exc:
        raise RuntimeError(f"국가법령정보센터 조회 실패: {law_name}: {exc}") from exc
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise RuntimeError(f"국가법령정보센터 XML 파싱 실패: {law_name}") from exc
    returned_name = (root.findtext("./기본정보/법령명_한글") or "").strip()
    if returned_name != law_name:
        raise RuntimeError(
            f"공식 법령명 불일치: 요청 '{law_name}', 응답 '{returned_name or '없음'}'"
        )
    return root


def official_articles(
    root: ET.Element,
    requested: set[str],
) -> tuple[dict[str, dict[str, str]], str]:
    available: dict[str, ET.Element] = {}
    for node in root.findall("./조문/조문단위"):
        if (node.findtext("조문여부") or "").strip() != "조문":
            continue
        available[_article_key(node)] = node

    missing = sorted(requested - available.keys())
    if missing:
        raise ValueError(f"공식 응답에서 조문을 찾지 못했습니다: {', '.join(missing)}")

    result: dict[str, dict[str, str]] = {}
    for article in sorted(requested):
        node = available[article]
        text = _official_article_text(node)
        result[article] = {
            "title": (node.findtext("조문제목") or "").strip(),
            "text": text,
            "effective_date": _iso_basic_date(
                (node.findtext("조문시행일자") or "").strip(),
                context=article,
            ),
            "hash": article_hash(text),
        }
    official_id = (root.findtext("./기본정보/법령ID") or "").strip()
    if not official_id:
        raise ValueError("공식 응답에 법령ID가 없습니다")
    return result, official_id


def compare_official_snapshot(
    path: Path,
    *,
    oc: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        stored = yaml.safe_load(handle)
    if not isinstance(stored, dict) or not isinstance(stored.get("articles"), dict):
        raise ValueError(f"{path}: 올바른 조문 스냅샷이 아닙니다")

    root = fetch_official_law(str(stored["name"]), oc)
    official, official_id = official_articles(root, set(stored["articles"]))
    refreshed = dict(stored)
    refreshed["articles"] = {
        article: official[article] for article in stored["articles"]
    }
    refreshed["official_id"] = official_id
    refreshed["source"] = "국가법령정보센터"
    refreshed["source_url"] = str(
        stored.get("source_url")
        or f"https://www.law.go.kr/법령/{stored['name'].replace(' ', '')}"
    )
    refreshed["retrieved_via"] = "national-law-open-api"

    changes: list[dict[str, Any]] = []
    for article, official_item in refreshed["articles"].items():
        stored_item = stored["articles"][article]
        for field in ("title", "text", "effective_date", "hash"):
            if str(stored_item.get(field, "")) != str(official_item[field]):
                changes.append(
                    {
                        "statute_id": str(stored["id"]),
                        "article": article,
                        "field": field,
                    }
                )
    for field in ("official_id", "source", "source_url", "retrieved_via"):
        if str(stored.get(field, "")) != str(refreshed[field]):
            changes.append(
                {
                    "statute_id": str(stored["id"]),
                    "article": "",
                    "field": field,
                }
            )
    return stored, refreshed, changes


def _update_rule_snapshot_dates(
    rules_path: Path,
    changed_statutes: set[str],
    snapshot_date: str,
) -> None:
    if not rules_path.exists() or not changed_statutes:
        return
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    changed = False
    for rule in rules:
        basis = rule.get("basis", {})
        if basis.get("statute_id") in changed_statutes:
            basis["snapshot_date"] = snapshot_date
            changed = True
    if changed:
        _write_yaml(rules_path, rules)


def _rule_impact(
    rules_path: Path,
) -> dict[tuple[str, str], list[str]]:
    if not rules_path.exists():
        return {}
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if not isinstance(rules, list):
        raise ValueError(f"{rules_path}: 법령 규칙 목록 형식이 아닙니다")
    impact: dict[tuple[str, str], list[str]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        basis = rule.get("basis", {})
        if basis.get("type") != "statute":
            continue
        key = (str(basis.get("statute_id", "")), str(basis.get("article", "")))
        impact.setdefault(key, []).append(str(rule.get("id", "")))
    return {key: sorted(rule_ids) for key, rule_ids in impact.items()}


def audit_official(
    statutes_dir: Path,
    *,
    oc: str,
    refresh: bool,
    snapshot_date: str,
    report_path: Path | None,
) -> bool:
    files = sorted(statutes_dir.glob("*.yaml"))
    changes: list[dict[str, Any]] = []
    changed_statutes: set[str] = set()
    refreshed_payloads: list[tuple[Path, dict[str, Any]]] = []
    rules_path = statutes_dir.parent / "rules" / "law.yaml"
    rule_impact = _rule_impact(rules_path)

    for path in files:
        stored, official, statute_changes = compare_official_snapshot(path, oc=oc)
        if statute_changes:
            official["snapshot_date"] = snapshot_date
            changed_statutes.add(str(stored["id"]))
            refreshed_payloads.append((path, official))
            changes.extend(statute_changes)

    for change in changes:
        change["rule_ids"] = rule_impact.get(
            (change["statute_id"], change["article"]),
            [],
        )
    affected_rule_ids = sorted(
        {
            rule_id
            for change in changes
            for rule_id in change["rule_ids"]
            if rule_id
        }
    )
    report = {
        "checked_at": snapshot_date,
        "statutes": len(files),
        "changed_statutes": sorted(changed_statutes),
        "affected_rule_ids": affected_rule_ids,
        "review_required": bool(changes),
        "changes": changes,
        "contains_statute_text": False,
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    if changes and refresh:
        for path, payload in refreshed_payloads:
            _write_yaml(path, payload)
        _update_rule_snapshot_dates(rules_path, changed_statutes, snapshot_date)
    return bool(changes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "동봉 조문 해시를 오프라인 검증하거나 국가법령정보센터의 "
            "현행 조문과 비교·갱신합니다."
        )
    )
    parser.add_argument(
        "--statutes-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "statutes",
    )
    parser.add_argument(
        "--refresh-hashes",
        action="store_true",
        help="현재 저장된 text를 기준으로 hash 필드만 갱신합니다.",
    )
    official_group = parser.add_mutually_exclusive_group()
    official_group.add_argument(
        "--check-official",
        action="store_true",
        help="공식 현행 조문과 비교하고 차이가 있으면 종료코드 1을 반환합니다.",
    )
    official_group.add_argument(
        "--refresh-official",
        action="store_true",
        help="공식 현행 조문으로 스냅샷과 규칙 snapshot_date를 갱신합니다.",
    )
    parser.add_argument(
        "--oc",
        default=None,
        help="국가법령정보 공동활용 인증값. 생략하면 LAW_OPEN_API_OC를 사용합니다.",
    )
    parser.add_argument(
        "--snapshot-date",
        default=date.today().isoformat(),
        help="공식 조회 기준일(YYYY-MM-DD)",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    files = sorted(args.statutes_dir.glob("*.yaml"))
    if not files:
        print(f"스냅샷 파일이 없습니다: {args.statutes_dir}", file=sys.stderr)
        return 2

    try:
        date.fromisoformat(args.snapshot_date)
        if args.check_official or args.refresh_official:
            oc = args.oc or os.environ.get("LAW_OPEN_API_OC", "")
            changed = audit_official(
                args.statutes_dir,
                oc=oc,
                refresh=args.refresh_official,
                snapshot_date=args.snapshot_date,
                report_path=args.report,
            )
            if changed and args.check_official:
                print("공식 현행 조문과 다른 스냅샷이 있습니다", file=sys.stderr)
                return 1
            if changed:
                print(f"{len(files)}개 법령 스냅샷 공식 원문 갱신 완료")
            else:
                print(f"{len(files)}개 법령 스냅샷이 공식 현행 조문과 일치합니다")
            return 0

        errors: list[str] = []
        for path in files:
            errors.extend(process(path, refresh=args.refresh_hashes))
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(f"{len(files)}개 스냅샷 해시 검증 완료")
        return 0
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
