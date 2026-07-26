from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import time
from typing import Iterable
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


USER_AGENT = "fairpost-corpus-builder/0.3 (+local research)"
JOB_ALIO_LIST = "https://job.alio.go.kr/recruit.do"
JOB_ALIO_DETAIL = "https://job.alio.go.kr/recruitview.do"
GOJOBS_LIST = "https://www.gojobs.go.kr/apmList.do"
GOJOBS_DETAIL = "https://www.gojobs.go.kr/apmView.do"
CLEANEYE_LIST = "https://job.cleaneye.go.kr/user/ypRecruitment.do"
CLEANEYE_AJAX_LIST = (
    "https://job.cleaneye.go.kr/user/selectYpRecruitment.do"
)
CLEANEYE_DETAIL = "https://job.cleaneye.go.kr/user/ypCareersData.do"
WORK24_API = (
    "https://www.work24.go.kr/cm/openApi/call/wk/"
    "callOpenApiSvcInfo210L01.do"
)
YOUTH_JOB_API = "https://apis.data.go.kr/1051000/recruitment/list"
YOUTH_JOB_SITE_LIST = (
    "https://opendata.alio.go.kr/new/odaApiMng/"
    "recrutInquiryAjaxList.do"
)
YOUTH_JOB_SITE_SEARCH = (
    "https://opendata.alio.go.kr/new/odaApiMng/"
    "recrutInquiryList.do"
)
SENIOR_JOB_API = "https://apis.data.go.kr/B552474/SenuriService"
JINCHEON_JOB_CSV = (
    "https://www.data.go.kr/cmm/cmm/fileDownload.do?"
    "atchFileId=FILE_000000003522912&fileDetailSn=1&insertDataPrcus=N"
)
JINCHEON_JOB_SOURCE_PAGE = (
    "https://www.data.go.kr/data/15152004/fileData.do"
)
ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- )]?\d{3,4}[- ]?\d{4}|"
    r"1(?:5|6|8)\d{2}[- ]?\d{4})(?!\d)"
)
LABELED_NAME_RE = re.compile(
    r"(?P<label>담당자|담당|문의자|채용담당)\s*[:：]?\s*"
    r"(?P<name>[가-힣]{2,4})(?=\s|$|[,/()])"
)


class ContentParser(HTMLParser):
    def __init__(
        self,
        *,
        target_classes: set[str] | None = None,
        textarea_id: str | None = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.target_classes = target_classes or set()
        self.textarea_id = textarea_id
        self.capture_depth = 0
        self.textarea_depth = 0
        self.parts: list[str] = []
        self.h2_values: list[str] = []
        self._heading_depth = 0
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if self.capture_depth:
            self.capture_depth += 1
        elif tag == "div" and classes.intersection(self.target_classes):
            self.capture_depth = 1
        if self.textarea_depth:
            self.textarea_depth += 1
        elif tag == "textarea" and values.get("id") == self.textarea_id:
            self.textarea_depth = 1
        if tag == "h2":
            self._heading_depth = 1
            self._heading_parts = []
        elif self._heading_depth:
            self._heading_depth += 1
        if (self.capture_depth or self.textarea_depth) and tag in {
            "br",
            "p",
            "tr",
            "li",
            "h3",
            "h4",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.capture_depth:
            self.capture_depth -= 1
        if self.textarea_depth:
            self.textarea_depth -= 1
        if self._heading_depth:
            self._heading_depth -= 1
            if self._heading_depth == 0:
                value = " ".join(self._heading_parts).strip()
                if value:
                    self.h2_values.append(value)

    def handle_data(self, data: str) -> None:
        if self.capture_depth or self.textarea_depth:
            value = data.strip()
            if value:
                self.parts.extend((value, "\n"))
        if self._heading_depth and data.strip():
            self._heading_parts.append(data.strip())

    def text(self) -> str:
        value = "".join(self.parts)
        lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)


def fetch(url: str, params: dict[str, str | int], *, retries: int = 3) -> str:
    target = f"{url}?{urlencode(params)}"
    request = Request(target, headers={"User-Agent": USER_AGENT})
    error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=35) as response:
                body = response.read()
                try:
                    return body.decode("utf-8")
                except UnicodeDecodeError:
                    encoding = response.headers.get_content_charset() or "euc-kr"
                    return body.decode(encoding, errors="replace")
        except Exception as exc:  # Network errors are retried at build time only.
            error = exc
            time.sleep(1.5 * (attempt + 1))
    parameter_names = ",".join(sorted(params))
    raise RuntimeError(
        f"수집 요청 실패: {url} (parameters: {parameter_names}): {error}"
    )


def fetch_post(
    url: str,
    params: dict[str, str | int],
    *,
    retries: int = 3,
) -> str:
    body = urlencode(params).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=35) as response:
                payload = response.read()
                try:
                    return payload.decode("utf-8")
                except UnicodeDecodeError:
                    encoding = response.headers.get_content_charset() or "euc-kr"
                    return payload.decode(encoding, errors="replace")
        except Exception as exc:  # Network errors are retried at build time only.
            error = exc
            time.sleep(1.5 * (attempt + 1))
    parameter_names = ",".join(sorted(params))
    raise RuntimeError(
        f"수집 요청 실패: {url} (parameters: {parameter_names}): {error}"
    )


def fetch_bytes(url: str, *, retries: int = 3) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=120) as response:
                return response.read()
        except Exception as exc:
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Download failed: {url}: {error}")


def load_local_env(path: Path = ROOT_ENV) -> None:
    """Load simple KEY=VALUE entries without adding a runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def deidentify(text: str, organization: str | None = None) -> str:
    value = text
    if organization and len(organization.strip()) >= 2:
        value = value.replace(organization.strip(), "[기관]")
    value = EMAIL_RE.sub("[EMAIL]", value)
    value = PHONE_RE.sub("[PHONE]", value)
    value = LABELED_NAME_RE.sub(
        lambda match: f"{match.group('label')}: [담당자]",
        value,
    )
    return value


OCCUPATION_CLASSES = ("office", "tech", "research", "field")
OCCUPATION_PRIORITY = ("research", "tech", "office", "field")
OCCUPATION_KEYWORDS = {
    "office": (
        "행정",
        "사무",
        "기획",
        "회계",
        "인사",
        "경영",
        "총무",
        "법무",
        "홍보",
        "마케팅",
        "영업",
        "고객상담",
        "상담원",
        "교육",
        "교사",
        "교원",
        "강사",
        "영양사",
        "사회복지사",
        "간호사",
        "보건",
        "금융",
        "보험설계사",
        "구매",
        "비서",
        "접수원",
    ),
    "tech": (
        "개발",
        "정보기술",
        "전산",
        "엔지니어",
        "기술직",
        "기술원",
        "설계",
        "전기",
        "전자",
        "기계",
        "건축",
        "토목",
        "화학",
        "품질관리",
        "품질 관리",
        "안전관리",
        "안전 관리",
        "환경관리",
        "환경 관리",
        "시스템",
        "네트워크",
        "소프트웨어",
        "하드웨어",
        "데이터베이스",
        "프로그래머",
        "유지보수",
        "생산관리",
        "생산 관리",
        "공정관리",
        "공정 관리",
        "IT",
    ),
    "research": (
        "연구",
        "실험",
        "분석",
        "연구원",
        "알앤디",
        "조사연구",
        "통계분석",
        "박사",
        "석사",
        "학술",
        "논문",
        "R&D",
    ),
    "field": (
        "현장",
        "시설",
        "운전",
        "미화",
        "경비",
        "조리",
        "정비",
        "생산",
        "제조",
        "포장",
        "조립",
        "물류",
        "배송",
        "배달",
        "창고",
        "청소",
        "보안",
        "급식",
        "요양",
        "돌봄",
        "보육",
        "농업",
        "축산",
        "용접",
        "가공",
        "검사",
        "분류",
        "설치",
        "판매",
        "매장",
        "단순노무",
        "운반",
        "상하차",
        "작업원",
        "조작원",
        "기사",
        "주방",
        "재봉",
        "선별",
    ),
}
OCCUPATION_FALLBACK = {"public": "office", "private": "field"}
OCCUPATION_FOCUS_LABELS = (
    "채용제목",
    "채용공고제목",
    "직무내용",
    "직무상세",
    "담당업무",
    "모집분야",
    "채용분야",
    "근무분야",
    "표준직무(ncs)",
    "사업내용",
    "title",
    "wantedtitle",
    "occupation",
    "description",
    "jobcont",
    "recrutpbancttl",
)


def _occupation_term_count(text: str, term: str) -> int:
    folded_text = text.casefold()
    folded_term = term.casefold()
    if term.isascii():
        return len(
            re.findall(
                rf"(?<![a-z0-9]){re.escape(folded_term)}(?![a-z0-9])",
                folded_text,
            )
        )
    return folded_text.count(folded_term)


def _occupation_focus_text(text: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    selected = list(lines[:8])
    for index, line in enumerate(lines[:-1]):
        normalized = re.sub(r"[\s._-]+", "", line).casefold()
        if any(
            normalized == label
            or normalized.endswith(f".{label}")
            or label in normalized
            for label in OCCUPATION_FOCUS_LABELS
        ):
            selected.extend(lines[index + 1 : index + 3])
    return "\n".join(dict.fromkeys(selected))


def classify_occupation(text: str, sector: str) -> str:
    if sector not in OCCUPATION_FALLBACK:
        raise ValueError(f"지원하지 않는 부문입니다: {sector}")
    focus_text = _occupation_focus_text(text)
    scores = {
        name: sum(_occupation_term_count(focus_text, term) for term in terms)
        for name, terms in OCCUPATION_KEYWORDS.items()
    }
    highest = max(scores.values(), default=0)
    if highest == 0:
        return OCCUPATION_FALLBACK[sector]
    return next(name for name in OCCUPATION_PRIORITY if scores[name] == highest)


def classify_employment(text: str) -> str:
    temporary_terms = ("기간제", "비정규직", "계약직", "인턴", "시간강사", "대체인력")
    return "temporary" if any(term in text for term in temporary_terms) else "regular"


def make_record(
    *,
    source: str,
    source_id: str,
    text: str,
    sector: str,
    organization: str | None = None,
    source_url: str,
) -> dict[str, str]:
    clean_text = deidentify(text, organization)
    content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
    return {
        "id": f"{source}:{source_id}",
        "source": source,
        "source_id": source_id,
        "source_url": source_url,
        "sector": sector,
        "occupation": classify_occupation(clean_text, sector),
        "employment_type": classify_employment(clean_text),
        "content_hash": content_hash,
        "text": clean_text,
    }


def collect_job_alio(limit: int, delay: float) -> Iterable[dict[str, str]]:
    seen: set[str] = set()
    collected = 0
    page = 1
    while collected < limit and page <= 1000:
        html = fetch(JOB_ALIO_LIST, {"pageNo": page})
        ids = re.findall(r"/recruitview\.do\?idx=(\d+)", html)
        ids = list(dict.fromkeys(ids))
        if not ids:
            break
        for source_id in ids:
            if source_id in seen:
                continue
            seen.add(source_id)
            detail = fetch(JOB_ALIO_DETAIL, {"idx": source_id})
            parser = ContentParser(target_classes={"detailTxt", "tab-content"})
            parser.feed(detail)
            organization = parser.h2_values[-1] if parser.h2_values else None
            text = parser.text()
            if len(text) >= 120:
                yield make_record(
                    source="job-alio",
                    source_id=source_id,
                    text=text,
                    sector="public",
                    organization=organization,
                    source_url=f"{JOB_ALIO_DETAIL}?idx={source_id}",
                )
                collected += 1
            if collected >= limit:
                return
            time.sleep(delay)
        page += 1


def _gojobs_organization(html: str) -> str | None:
    match = re.search(
        r"<th[^>]*>\s*기관명\s*</th>\s*<td[^>]*>(.*?)</td>",
        html,
        flags=re.DOTALL,
    )
    if not match:
        return None
    parser = ContentParser(target_classes=set())
    return re.sub(r"<[^>]+>", " ", match.group(1)).strip()


def collect_gojobs(limit: int, delay: float) -> Iterable[dict[str, str]]:
    seen: set[str] = set()
    collected = 0
    page = 1
    list_defaults: dict[str, str | int] = {
        "empmnsn": 0,
        "menuNo": 56,
        "searchBbssecode": 0,
        "searchEmpmnsecode": "e10",
    }
    while collected < limit and page <= 5000:
        html = fetch(GOJOBS_LIST, {**list_defaults, "pageIndex": page})
        ids = [
            source_id
            for _job_code, source_id in re.findall(
                r"fn_apmView\('([^']+)',\s*'(\d+)'\)",
                html,
            )
        ]
        ids = list(dict.fromkeys(ids))
        if not ids:
            break
        for source_id in ids:
            if source_id in seen:
                continue
            seen.add(source_id)
            detail = fetch(GOJOBS_DETAIL, {"empmnsn": source_id})
            parser = ContentParser(textarea_id="content")
            parser.feed(detail)
            text = parser.text()
            if len(text) >= 120:
                yield make_record(
                    source="gojobs",
                    source_id=source_id,
                    text=text,
                    sector="public",
                    organization=_gojobs_organization(detail),
                    source_url=f"{GOJOBS_DETAIL}?empmnsn={source_id}",
                )
                collected += 1
            if collected >= limit:
                return
            time.sleep(delay)
        page += 1


def collect_cleaneye(limit: int, delay: float) -> Iterable[dict[str, str]]:
    """Collect the public structured listing used by CLEANEYE Job Plus."""
    seen: set[str] = set()
    collected = 0
    page = 1
    while collected < limit and page <= 10000:
        response = fetch_post(CLEANEYE_AJAX_LIST, {"pageIndex": page})
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("클린아이 잡플러스 목록 응답이 JSON이 아닙니다") from exc
        items = payload.get("list", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise RuntimeError("클린아이 잡플러스 list 필드가 목록이 아닙니다")
        if not items:
            break

        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            empyear = str(item.get("empyear", "")).strip()
            organization_id = str(item.get("ypEntId", "")).strip()
            sequence = str(item.get("entSeq", "")).strip()
            if not empyear or not organization_id or not sequence:
                continue
            source_id = f"{empyear}:{organization_id}:{sequence}"
            if source_id in seen:
                continue
            seen.add(source_id)
            detail = fetch_post(
                CLEANEYE_DETAIL,
                {
                    "empyear": empyear,
                    "ypEntId": organization_id,
                    "entSeq": sequence,
                },
            )
            parser = ContentParser(target_classes={"detail_info_box"})
            parser.feed(detail)
            text = parser.text()
            organization = str(item.get("entName", "")).strip() or None
            if len(text) >= 120:
                yield make_record(
                    source="cleaneye",
                    source_id=source_id,
                    text=text,
                    sector="public",
                    organization=organization,
                    source_url=CLEANEYE_LIST,
                )
                collected += 1
                added += 1
            if collected >= limit:
                return
            time.sleep(delay)
        if added == 0:
            break
        page += 1


def _xml_leaf_text(root: ET.Element) -> str:
    lines: list[str] = []
    for element in root.iter():
        if len(element) == 0 and element.text and element.text.strip():
            lines.append(f"{element.tag}\n{element.text.strip()}")
    return "\n".join(lines)


def _json_leaf_text(value: object, prefix: str = "") -> str:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            child_text = _json_leaf_text(child, child_prefix)
            if child_text:
                lines.append(child_text)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            child_text = _json_leaf_text(child, child_prefix)
            if child_text:
                lines.append(child_text)
    elif value is not None and str(value).strip():
        lines.append(f"{prefix}\n{str(value).strip()}")
    return "\n".join(lines)


def _youth_job_nodes(payload: object, *, source_label: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{source_label} API 응답이 JSON 객체가 아닙니다")

    data = payload.get("data")
    if isinstance(data, dict):
        result = data.get("result", [])
    else:
        result_code = str(payload.get("resultCode", ""))
        if result_code not in {"00", "200"}:
            message = str(payload.get("resultMsg", "알 수 없는 오류"))
            raise RuntimeError(f"{source_label} API 오류 ({result_code}): {message}")
        result = payload.get("result", [])

    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        raise RuntimeError(f"{source_label} API의 result 필드가 목록이 아닙니다")
    return [item for item in result if isinstance(item, dict)]


def _youth_job_record(
    item: dict[str, object],
    *,
    source: str,
    fallback_url: str,
) -> dict[str, str] | None:
    source_id = str(item.get("recrutPblntSn", "")).strip()
    if not source_id:
        return None
    organization = str(item.get("instNm", "")).strip() or None
    text = _json_leaf_text(item)
    if not text:
        return None
    source_url = str(item.get("srcUrl", "")).strip() or fallback_url
    return make_record(
        source=source,
        source_id=source_id,
        text=text,
        sector="public",
        organization=organization,
        source_url=source_url,
    )


def _balanced_youth_limits(limit: int) -> tuple[tuple[str, int], ...]:
    return (
        ("R1060", (limit + 1) // 2),
        ("R1070", limit // 2),
    )


def collect_youth_job_api(
    limit: int,
    delay: float,
    auth_key: str,
    api_url: str = YOUTH_JOB_API,
) -> Iterable[dict[str, str]]:
    if not auth_key:
        raise ValueError(
            "청년일자리 API 수집에는 YOUTH_JOB_SERVICE_AUTH_KEY 환경변수 또는 "
            "--youth-job-auth-key가 필요합니다"
        )

    decoded_key = unquote(auth_key)
    seen: set[str] = set()
    for hire_type, type_limit in _balanced_youth_limits(limit):
        collected = 0
        page = 1
        page_size = min(100, type_limit)
        while collected < type_limit and page <= 1000:
            response = fetch(
                api_url,
                {
                    "serviceKey": decoded_key,
                    "resultType": "json",
                    "pageNo": page,
                    "numOfRows": page_size,
                    "hireTypeLst": hire_type,
                    "ongoingYn": "A",
                },
            )
            try:
                payload = json.loads(response)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "청년일자리 API 응답이 JSON이 아닙니다. 승인된 Swagger의 "
                    "Base URL과 YOUTH_JOB_SERVICE_URL을 확인하세요."
                ) from exc
            nodes = _youth_job_nodes(payload, source_label="청년일자리")
            if not nodes:
                break
            added = 0
            for item in nodes:
                record = _youth_job_record(
                    item,
                    source="youth-job",
                    fallback_url=api_url,
                )
                if record is None or record["source_id"] in seen:
                    continue
                seen.add(record["source_id"])
                yield record
                collected += 1
                added += 1
                if collected >= type_limit:
                    break
            if added == 0:
                break
            page += 1
            time.sleep(delay)


def collect_youth_job_site(
    limit: int,
    delay: float,
) -> Iterable[dict[str, str]]:
    """Collect public structured postings used by the official search page."""
    seen: set[str] = set()
    for hire_type, type_limit in _balanced_youth_limits(limit):
        collected = 0
        page = 1
        page_size = min(100, type_limit)
        while collected < type_limit and page <= 1000:
            response = fetch_post(
                YOUTH_JOB_SITE_LIST,
                {
                    "pageNo": page,
                    "numOfRows": page_size,
                    "hireTypeLst": hire_type,
                    "ongoingYn": "A",
                },
            )
            try:
                payload = json.loads(response)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "청년일자리 공식 사이트의 목록 응답이 JSON이 아닙니다"
                ) from exc
            nodes = _youth_job_nodes(payload, source_label="청년일자리 공식 사이트")
            if not nodes:
                break
            added = 0
            for item in nodes:
                record = _youth_job_record(
                    item,
                    source="youth-job-site",
                    fallback_url=YOUTH_JOB_SITE_SEARCH,
                )
                if record is None or record["source_id"] in seen:
                    continue
                seen.add(record["source_id"])
                yield record
                collected += 1
                added += 1
                if collected >= type_limit:
                    break
            if added == 0:
                break
            page += 1
            time.sleep(delay)


def collect_work24(
    limit: int,
    delay: float,
    auth_key: str,
) -> Iterable[dict[str, str]]:
    if not auth_key:
        raise ValueError(
            "고용24 수집에는 WORK24_AUTH_KEY 환경변수 또는 --work24-auth-key가 필요합니다"
        )
    collected = 0
    page = 1
    while collected < limit and page <= 1000:
        listing = fetch(
            WORK24_API,
            {
                "authKey": auth_key,
                "callTp": "L",
                "returnType": "XML",
                "startPage": page,
                "display": min(100, limit - collected),
                "sortOrderBy": "DESC",
            },
        )
        root = ET.fromstring(listing)
        api_error = root.findtext(".//error")
        _raise_work24_error(api_error)
        wanted_nodes = root.findall(".//wanted")
        if not wanted_nodes:
            break
        for wanted in wanted_nodes:
            source_id = wanted.findtext("wantedAuthNo", "").strip()
            if not source_id:
                continue
            detail_xml = fetch(
                WORK24_API,
                {
                    "authKey": auth_key,
                    "callTp": "D",
                    "returnType": "XML",
                    "wantedAuthNo": source_id,
                },
            )
            detail_root = ET.fromstring(detail_xml)
            api_error = detail_root.findtext(".//error")
            _raise_work24_error(api_error)
            text = _xml_leaf_text(detail_root)
            organization = (
                detail_root.findtext(".//company")
                or wanted.findtext("company")
                or None
            )
            if text:
                yield make_record(
                    source="work24",
                    source_id=source_id,
                    text=text,
                    sector="private",
                    organization=organization,
                    source_url=(wanted.findtext("wantedInfoUrl") or WORK24_API),
                )
                collected += 1
            if collected >= limit:
                return
            time.sleep(delay)
        page += 1


def _raise_work24_error(api_error: str | None) -> None:
    if not api_error:
        return
    message = api_error.strip()
    if "개인회원은 사용할 수 없는 OPEN-API" in message:
        raise RuntimeError(
            "고용24 API 권한 오류: "
            f"{message} 인증키는 서버에 전달됐지만 현재 개인회원 권한으로 "
            "식별됐습니다. .env 값의 바깥 따옴표는 수집기가 제거하므로 "
            "따옴표나 XML 형식 문제가 아닙니다. 고용24 신청현황에서 "
            "채용정보 API의 승인 대상 회원 유형을 확인하십시오."
        )
    raise RuntimeError(f"고용24 API 오류: {message}")


def _senior_job_error(root: ET.Element) -> str | None:
    result_code = (root.findtext(".//resultCode") or "").strip()
    if result_code in {"", "0", "00", "0000"}:
        return None
    result_message = (root.findtext(".//resultMsg") or "Unknown API error").strip()
    return f"Senior job API error ({result_code}): {result_message}"


def _senior_job_text(
    detail: ET.Element,
    listing: ET.Element,
) -> str:
    fields = (
        ("wantedTitle", "title"),
        ("age", "age"),
        ("ageLim", "age_limit"),
        ("clltPrnnum", "headcount"),
        ("emplymShpNm", "employment_type"),
        ("jobclsNm", "occupation"),
        ("workPlcNm", "workplace"),
        ("acptMthd", "application_method"),
        ("frAcptDd", "application_start"),
        ("toAcptDd", "application_end"),
        ("detCnts", "description"),
        ("etcItm", "other"),
    )
    values: list[str] = []
    for tag, label in fields:
        value = (detail.findtext(f".//{tag}") or "").strip()
        if not value:
            value = (listing.findtext(tag) or "").strip()
        if value:
            values.extend((label, value))
    return "\n".join(values)


def collect_senior_job(
    limit: int,
    delay: float,
    auth_key: str,
    api_url: str = SENIOR_JOB_API,
) -> Iterable[dict[str, str]]:
    """Collect company-registered senior job postings from the official API."""
    if not auth_key:
        raise ValueError(
            "SENIOR_JOB_SERVICE_AUTH_KEY or --senior-job-auth-key is required"
        )

    decoded_key = unquote(auth_key)
    collected = 0
    page = 1
    seen: set[str] = set()
    while collected < limit and page <= 1000:
        listing_xml = fetch(
            f"{api_url.rstrip('/')}/getJobList",
            {
                "serviceKey": decoded_key,
                "pageNo": page,
                "numOfRows": min(100, limit - collected),
            },
        )
        listing_root = ET.fromstring(listing_xml)
        api_error = _senior_job_error(listing_root)
        if api_error:
            raise RuntimeError(api_error)
        items = listing_root.findall(".//items/item")
        if not items:
            break

        added = 0
        for item in items:
            source_id = (item.findtext("jobId") or "").strip()
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            detail_url = f"{api_url.rstrip('/')}/getJobInfo"
            detail_xml = fetch(
                detail_url,
                {
                    "serviceKey": decoded_key,
                    "id": source_id,
                },
            )
            detail_root = ET.fromstring(detail_xml)
            api_error = _senior_job_error(detail_root)
            if api_error:
                raise RuntimeError(api_error)
            detail = detail_root.find(".//body/item")
            if detail is None:
                continue
            organization = (
                (detail.findtext("plbizNm") or "").strip()
                or (item.findtext("oranNm") or "").strip()
                or None
            )
            text = _senior_job_text(detail, item)
            if not text:
                continue
            yield make_record(
                source="senior-job",
                source_id=source_id,
                text=text,
                sector="private",
                organization=organization,
                source_url=detail_url,
            )
            collected += 1
            added += 1
            if collected >= limit:
                return
            time.sleep(delay)
        if added == 0:
            break
        page += 1


JINCHEON_TEXT_FIELDS = (
    ("기업형태", "기업형태"),
    ("사업내용", "사업내용"),
    ("채용제목", "채용제목"),
    ("고용형태", "고용형태"),
    ("모집인원", "모집인원"),
    ("근무형태", "근무형태"),
    ("급여조건", "급여조건"),
    ("최저급여", "최저급여"),
    ("최대급여", "최대급여"),
    ("급여결정여부", "급여결정여부"),
    ("상여금비율", "상여금비율"),
    ("우대조건", "우대조건"),
    ("경력사항", "경력사항"),
    ("최종학력", "최종학력"),
    ("성별", "성별"),
    ("복리후생", "복리후생"),
    ("근무지역", "근무지역"),
    ("직무내용", "직무내용"),
    ("접수기간 시작일", "접수기간 시작일"),
    ("접수기간 종료일", "접수기간 종료일"),
    ("채용형태", "채용형태"),
    ("접수방법", "접수방법"),
    ("업종", "업종"),
    ("근무시간", "근무시간"),
    ("카테고리", "카테고리"),
)


def _decode_csv(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _jincheon_row_text(row: dict[str, str]) -> str:
    values: list[str] = []
    for column, label in JINCHEON_TEXT_FIELDS:
        value = (row.get(column) or "").strip()
        if value:
            values.extend((label, value))
    return "\n".join(values)


def collect_jincheon_jobs(
    limit: int,
    delay: float,
    csv_url: str = JINCHEON_JOB_CSV,
) -> Iterable[dict[str, str]]:
    """Collect private-company postings from Jincheon County's open CSV."""
    del delay
    text = _decode_csv(fetch_bytes(csv_url))
    rows = list(csv.DictReader(io.StringIO(text)))
    rows.sort(
        key=lambda row: (
            (row.get("접수기간 시작일") or "").strip(),
            (row.get("채용제목") or "").strip(),
        ),
        reverse=True,
    )
    collected = 0
    for row in rows:
        organization = (row.get("회사명") or "").strip() or None
        record_text = _jincheon_row_text(row)
        if not record_text:
            continue
        identity = "\x1f".join((organization or "", record_text))
        source_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        yield make_record(
            source="jincheon-jobs",
            source_id=source_id,
            text=record_text,
            sector="private",
            organization=organization,
            source_url=JINCHEON_JOB_SOURCE_PAGE,
        )
        collected += 1
        if collected >= limit:
            return


def split_records(
    records: list[dict[str, str]],
    train_ratio: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    unique: dict[str, dict[str, str]] = {}
    for record in sorted(records, key=lambda item: item["id"]):
        unique.setdefault(record["content_hash"], record)

    strata: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for record in unique.values():
        key = (record["sector"], record["occupation"], record["employment_type"])
        strata[key].append(record)

    ordered_strata: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    allocations: dict[tuple[str, str, str], int] = {}
    ideals: dict[tuple[str, str, str], float] = {}
    for key in sorted(strata):
        values = sorted(
            strata[key],
            key=lambda item: hashlib.sha256(item["id"].encode("utf-8")).hexdigest(),
        )
        ordered_strata[key] = values
        ideals[key] = len(values) * train_ratio
        allocations[key] = (
            1
            if len(values) == 1
            else max(1, min(len(values) - 1, int(ideals[key])))
        )

    target_train = round(len(unique) * train_ratio)
    difference = target_train - sum(allocations.values())
    while difference > 0:
        candidates = [
            key
            for key, values in ordered_strata.items()
            if allocations[key] < len(values) - 1
        ]
        if not candidates:
            candidates = [
                key
                for key, values in ordered_strata.items()
                if len(values) == 1 and allocations[key] == 0
            ]
        if not candidates:
            raise ValueError("목표 학습 비율을 만족하는 층화 분할을 만들 수 없습니다")
        key = min(
            candidates,
            key=lambda item: (
                -(ideals[item] - allocations[item]),
                item,
            ),
        )
        allocations[key] += 1
        difference -= 1

    while difference < 0:
        candidates = [
            key
            for key, values in ordered_strata.items()
            if len(values) > 1 and allocations[key] > 1
        ]
        if not candidates:
            candidates = [
                key
                for key, values in ordered_strata.items()
                if len(values) == 1 and allocations[key] == 1
            ]
        if not candidates:
            raise ValueError("목표 학습 비율을 만족하는 층화 분할을 만들 수 없습니다")
        key = min(
            candidates,
            key=lambda item: (
                -(allocations[item] - ideals[item]),
                item,
            ),
        )
        allocations[key] -= 1
        difference += 1

    train: list[dict[str, str]] = []
    holdout: list[dict[str, str]] = []
    for key, values in ordered_strata.items():
        cutoff = allocations[key]
        train.extend(values[:cutoff])
        holdout.extend(values[cutoff:])
    return (
        sorted(train, key=lambda item: item["id"]),
        sorted(holdout, key=lambda item: item["id"]),
    )


def write_jsonl(path: Path, records: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def manifest(records: list[dict[str, str]]) -> dict[str, object]:
    return {
        "count": len(records),
        "ids": [record["id"] for record in records],
        "content_hashes": sorted(record["content_hash"] for record in records),
    }


def summarize(
    records: list[dict[str, str]],
    train: list[dict[str, str]],
    holdout: list[dict[str, str]],
) -> dict[str, object]:
    unique_hashes = {record["content_hash"] for record in records}
    if len(unique_hashes) != len(records):
        raise ValueError("요약 대상에 원문 해시 중복이 있습니다")
    if len(train) + len(holdout) != len(records):
        raise ValueError("학습/홀드아웃 건수 합계가 전체 고유 공고 수와 다릅니다")
    occupations = {record["occupation"] for record in records}
    unexpected_occupations = occupations - set(OCCUPATION_CLASSES)
    if unexpected_occupations:
        raise ValueError(
            "허용되지 않은 직군이 있습니다: "
            + ", ".join(sorted(unexpected_occupations))
        )

    def counts(key: str) -> dict[str, int]:
        return dict(sorted(Counter(record[key] for record in records).items()))

    return {
        "total": len(records),
        "train": len(train),
        "holdout": len(holdout),
        "sources": counts("source"),
        "sectors": counts("sector"),
        "occupations": counts("occupation"),
        "employment_types": counts("employment_type"),
        "train_holdout_hash_overlap": len(
            {record["content_hash"] for record in train}
            & {record["content_hash"] for record in holdout}
        ),
        "deidentification": ["email", "phone", "labeled_contact_name", "organization"],
        "raw_postings_committed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "공식 채용정보 출처에서 공고를 수집하고 비식별화한 뒤 "
            "사전 구축용 70%와 봉인 홀드아웃 30%로 고정 분할합니다."
        )
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=(
            "job-alio",
            "gojobs",
            "cleaneye",
            "jincheon-jobs",
            "work24",
            "senior-job",
            "youth-job",
            "youth-job-site",
        ),
        required=True,
    )
    parser.add_argument("--limit-per-source", type=int, default=300)
    parser.add_argument(
        "--source-limit",
        action="append",
        default=[],
        metavar="SOURCE=COUNT",
        help=(
            "특정 출처의 수집 건수를 덮어씁니다. 예: "
            "--source-limit job-alio=100 --source-limit work24=300"
        ),
    )
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--output-dir", type=Path, default=Path(".corpus"))
    parser.add_argument(
        "--exclude-manifest",
        action="append",
        type=Path,
        default=[],
        help="이미 고정된 코퍼스의 manifest 해시를 수집 대상에서 제외합니다.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("reports/corpus_summary.json"),
    )
    parser.add_argument(
        "--work24-auth-key",
        default=None,
        help="생략하면 루트 .env의 WORK24_AUTH_KEY를 사용합니다.",
    )
    parser.add_argument(
        "--youth-job-auth-key",
        default=None,
        help=(
            "생략하면 루트 .env의 YOUTH_JOB_SERVICE_AUTH_KEY를 사용합니다."
        ),
    )
    parser.add_argument(
        "--senior-job-auth-key",
        default=None,
        help=(
            "Defaults to SENIOR_JOB_SERVICE_AUTH_KEY in the root .env file."
        ),
    )
    parser.add_argument(
        "--senior-job-api-url",
        default=None,
        help=(
            "Defaults to SENIOR_JOB_SERVICE_URL or the official SenuriService URL."
        ),
    )
    parser.add_argument(
        "--youth-job-api-url",
        default=None,
        help=(
            "승인된 Swagger의 Base URL을 포함한 채용 목록 URL입니다. "
            "생략하면 YOUTH_JOB_SERVICE_URL 또는 공공데이터포털 기본 URL을 사용합니다."
        ),
    )
    return parser


def parse_source_limits(
    values: list[str],
    selected_sources: list[str],
    default_limit: int,
) -> dict[str, int]:
    if default_limit < 1:
        raise ValueError("--limit-per-source는 1 이상이어야 합니다")
    selected = set(selected_sources)
    limits = {source: default_limit for source in selected_sources}
    seen: set[str] = set()
    for value in values:
        source, separator, raw_count = value.partition("=")
        source = source.strip()
        if not separator or not source or not raw_count.strip():
            raise ValueError(
                f"--source-limit 형식 오류 '{value}': SOURCE=COUNT가 필요합니다"
            )
        if source not in selected:
            raise ValueError(
                f"--source-limit 출처 '{source}'가 --source에 포함되지 않았습니다"
            )
        if source in seen:
            raise ValueError(f"--source-limit 출처 '{source}'가 중복되었습니다")
        seen.add(source)
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(
                f"--source-limit 건수는 정수여야 합니다: '{value}'"
            ) from exc
        if count < 1:
            raise ValueError(
                f"--source-limit 건수는 1 이상이어야 합니다: '{value}'"
            )
        limits[source] = count
    return limits


def collect_unique_records(
    records: Iterable[dict[str, str]],
    *,
    target: int,
    existing_hashes: set[str],
    source: str,
) -> list[dict[str, str]]:
    accepted: list[dict[str, str]] = []
    for record in records:
        content_hash = record["content_hash"]
        if content_hash in existing_hashes:
            continue
        existing_hashes.add(content_hash)
        accepted.append(record)
        if len(accepted) == target:
            break
    if len(accepted) != target:
        raise RuntimeError(
            f"{source}: 중복 제거 후 목표 {target}건 중 {len(accepted)}건만 수집했습니다"
        )
    return accepted


def load_excluded_hashes(paths: list[Path]) -> set[str]:
    hashes: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("content_hashes")
        if not isinstance(values, list) or not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in values
        ):
            raise ValueError(f"{path}: content_hashes SHA-256 목록이 필요합니다")
        hashes.update(values)
    return hashes


def main() -> int:
    load_local_env()
    args = build_parser().parse_args()
    args.work24_auth_key = args.work24_auth_key or os.environ.get(
        "WORK24_AUTH_KEY", ""
    )
    args.youth_job_auth_key = args.youth_job_auth_key or os.environ.get(
        "YOUTH_JOB_SERVICE_AUTH_KEY", ""
    )
    args.youth_job_api_url = (
        args.youth_job_api_url
        or os.environ.get("YOUTH_JOB_SERVICE_URL", "")
        or YOUTH_JOB_API
    )
    args.senior_job_auth_key = args.senior_job_auth_key or os.environ.get(
        "SENIOR_JOB_SERVICE_AUTH_KEY", ""
    )
    args.senior_job_api_url = (
        args.senior_job_api_url
        or os.environ.get("SENIOR_JOB_SERVICE_URL", "")
        or SENIOR_JOB_API
    )
    if not 0.5 <= args.train_ratio < 1:
        raise SystemExit("--train-ratio는 0.5 이상 1 미만이어야 합니다")
    selected_sources = list(dict.fromkeys(args.source))
    try:
        source_limits = parse_source_limits(
            args.source_limit,
            selected_sources,
            args.limit_per_source,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    records: list[dict[str, str]] = []
    try:
        seen_hashes = load_excluded_hashes(args.exclude_manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    for source in selected_sources:
        limit = source_limits[source]
        scan_limit = max(limit * 3, limit + len(seen_hashes) + 100)
        if source == "job-alio":
            iterator = collect_job_alio(scan_limit, args.delay)
        elif source == "gojobs":
            iterator = collect_gojobs(scan_limit, args.delay)
        elif source == "cleaneye":
            iterator = collect_cleaneye(scan_limit, args.delay)
        elif source == "jincheon-jobs":
            iterator = collect_jincheon_jobs(scan_limit, args.delay)
        elif source == "work24":
            iterator = collect_work24(
                scan_limit,
                args.delay,
                args.work24_auth_key,
            )
        elif source == "senior-job":
            iterator = collect_senior_job(
                scan_limit,
                args.delay,
                args.senior_job_auth_key,
                args.senior_job_api_url,
            )
        elif source == "youth-job":
            iterator = collect_youth_job_api(
                scan_limit,
                args.delay,
                args.youth_job_auth_key,
                args.youth_job_api_url,
            )
        else:
            iterator = collect_youth_job_site(
                scan_limit,
                args.delay,
            )
        source_records = collect_unique_records(
            iterator,
            target=limit,
            existing_hashes=seen_hashes,
            source=source,
        )
        records.extend(source_records)
        print(f"{source}: {len(source_records)}건")

    train, holdout = split_records(records, args.train_ratio)
    train_path = args.output_dir / "train" / "records.jsonl"
    holdout_path = args.output_dir / "holdout" / "records.jsonl"
    write_jsonl(train_path, train)
    write_jsonl(holdout_path, holdout)
    (args.output_dir / "train" / "manifest.json").write_text(
        json.dumps(manifest(train), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "holdout" / "manifest.json").write_text(
        json.dumps(manifest(holdout), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = summarize(records, train, holdout)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"분할 완료: train={len(train)}, holdout={len(holdout)}, "
        f"summary={args.summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
