from __future__ import annotations

import importlib.util
import json
import locale
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_collect_module():
    spec = importlib.util.spec_from_file_location(
        "collect_corpus",
        ROOT / "tools" / "collect_corpus.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_analyze_module():
    spec = importlib.util.spec_from_file_location(
        "analyze_corpus",
        ROOT / "tools" / "analyze_corpus.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deidentification_and_split_hash_isolation() -> None:
    module = load_collect_module()
    text = "담당자: 홍길동 / 02-1234-5678 / recruit@example.com / 테스트기관"
    clean = module.deidentify(text, "테스트기관")
    assert "홍길동" not in clean
    assert "02-1234-5678" not in clean
    assert "recruit@example.com" not in clean
    assert "테스트기관" not in clean

    records = [
        module.make_record(
            source="test",
            source_id=str(index),
            text=f"행정 사무 정규직 공고 {index}",
            sector="public",
            source_url="https://example.test",
        )
        for index in range(10)
    ]
    train, holdout = module.split_records(records, 0.7)
    assert {item["content_hash"] for item in train}.isdisjoint(
        item["content_hash"] for item in holdout
    )
    assert len(train) + len(holdout) == 10
    assert module.summarize(records, train, holdout)[
        "train_holdout_hash_overlap"
    ] == 0


@pytest.mark.parametrize(
    ("text", "sector", "expected"),
    [
        ("일반 행정과 회계 담당자를 채용합니다", "public", "office"),
        ("소프트웨어 개발 및 네트워크 유지보수", "private", "tech"),
        ("석사급 연구원으로 실험과 분석 수행", "public", "research"),
        ("생산 현장 포장 및 물류 업무", "private", "field"),
        ("별도 직무 설명 없음", "public", "office"),
        ("별도 직무 설명 없음", "private", "field"),
    ],
)
def test_occupation_classifier_uses_only_prd_four_classes(
    text: str,
    sector: str,
    expected: str,
) -> None:
    module = load_collect_module()
    assert module.classify_occupation(text, sector) == expected
    assert expected in module.OCCUPATION_CLASSES


def test_occupation_classifier_uses_explicit_tie_priority() -> None:
    module = load_collect_module()
    assert module.classify_occupation("연구 개발", "public") == "research"
    assert module.classify_occupation("기술직 행정", "public") == "tech"


def test_occupation_classifier_uses_title_and_duty_instead_of_boilerplate() -> None:
    module = load_collect_module()
    text = "\n".join(
        (
            "기업형태",
            "제조업",
            "채용제목",
            "생산 작업원 모집",
            "고용형태",
            "정규직",
            "복리후생",
            "4대 보험 가입",
            "접수방법",
            "온라인 접수",
            "문의",
            "경영지원팀",
        )
    )
    assert module.classify_occupation(text, "private") == "field"


def test_occupation_classifier_reads_structured_api_title_fields() -> None:
    module = load_collect_module()
    text = "\n".join(
        (
            "pbadmsStdInstCd",
            "B0001",
            "aplyQlfcCn",
            "학력 제한 없음",
            "recrutPbancTtl",
            "소프트웨어 개발자 채용",
        )
    )
    assert module.classify_occupation(text, "public") == "tech"


def test_occupation_classifier_recognizes_spaced_management_terms() -> None:
    module = load_collect_module()
    assert module.classify_occupation("생산 관리 담당자", "private") == "tech"


def test_occupation_classifier_rejects_unknown_sector() -> None:
    module = load_collect_module()
    with pytest.raises(ValueError, match="지원하지 않는 부문"):
        module.classify_occupation("사무", "unknown")


def test_split_hits_exact_global_ratio_across_small_strata() -> None:
    module = load_collect_module()
    records = []
    for index in range(10):
        record = module.make_record(
            source="test",
            source_id=str(index),
            text=f"서로 다른 층 공고 {index}",
            sector="public",
            source_url="https://example.test",
        )
        record["occupation"] = f"occupation-{index}"
        records.append(record)

    train, holdout = module.split_records(records, 0.7)
    assert len(train) == 7
    assert len(holdout) == 3


def test_source_specific_limits_support_prd_600_record_mix() -> None:
    module = load_collect_module()
    sources = ["job-alio", "cleaneye", "gojobs", "work24"]
    limits = module.parse_source_limits(
        [
            "job-alio=100",
            "cleaneye=100",
            "gojobs=100",
            "work24=300",
        ],
        sources,
        100,
    )
    assert limits == {
        "job-alio": 100,
        "cleaneye": 100,
        "gojobs": 100,
        "work24": 300,
    }
    assert sum(limits.values()) == 600


def test_source_specific_limit_rejects_unselected_or_duplicate_source() -> None:
    module = load_collect_module()
    with pytest.raises(ValueError, match="포함되지 않았습니다"):
        module.parse_source_limits(["work24=300"], ["job-alio"], 100)
    with pytest.raises(ValueError, match="중복"):
        module.parse_source_limits(
            ["job-alio=100", "job-alio=200"],
            ["job-alio"],
            100,
        )


def test_cross_source_duplicates_are_replaced_to_keep_exact_targets() -> None:
    module = load_collect_module()
    first = module.make_record(
        source="first",
        source_id="1",
        text="같은 공고",
        sector="public",
        source_url="https://example.test/1",
    )
    duplicate = module.make_record(
        source="second",
        source_id="1",
        text="같은 공고",
        sector="private",
        source_url="https://example.test/2",
    )
    replacement = module.make_record(
        source="second",
        source_id="2",
        text="다른 민간 공고",
        sector="private",
        source_url="https://example.test/3",
    )
    seen = {first["content_hash"]}
    accepted = module.collect_unique_records(
        iter([duplicate, replacement]),
        target=1,
        existing_hashes=seen,
        source="second",
    )
    assert accepted == [replacement]
    assert len(seen) == 2


def test_summary_rejects_duplicate_or_dropped_records() -> None:
    module = load_collect_module()
    record = module.make_record(
        source="test",
        source_id="1",
        text="고유 공고",
        sector="public",
        source_url="https://example.test/1",
    )
    with pytest.raises(ValueError, match="해시 중복"):
        module.summarize([record, record], [record], [])
    with pytest.raises(ValueError, match="건수 합계"):
        module.summarize([record], [], [])


def test_exclude_manifests_seed_cross_corpus_deduplication(tmp_path: Path) -> None:
    module = load_collect_module()
    first_hash = "a" * 64
    second_hash = "b" * 64
    train_manifest = tmp_path / "train-manifest.json"
    holdout_manifest = tmp_path / "holdout-manifest.json"
    train_manifest.write_text(
        json.dumps({"content_hashes": [first_hash]}),
        encoding="utf-8",
    )
    holdout_manifest.write_text(
        json.dumps({"content_hashes": [second_hash]}),
        encoding="utf-8",
    )
    assert module.load_excluded_hashes(
        [train_manifest, holdout_manifest]
    ) == {first_hash, second_hash}


def test_candidate_miner_rejects_holdout_path(tmp_path: Path) -> None:
    holdout = tmp_path / "holdout" / "records.jsonl"
    holdout.parent.mkdir()
    holdout.write_text("", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "tools/mine_candidates.py",
            "--input",
            str(holdout),
            "--output",
            str(tmp_path / "tasks.jsonl"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )
    assert completed.returncode != 0
    assert "홀드아웃" in completed.stderr


def test_corpus_analyzer_rejects_holdout_path(tmp_path: Path) -> None:
    module = load_analyze_module()
    holdout = tmp_path / "holdout" / "records.jsonl"
    holdout.parent.mkdir()
    holdout.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="홀드아웃"):
        module.load_training_records(holdout)


def test_work24_membership_error_is_clear_and_does_not_expose_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    secret = "secret-auth-key"
    monkeypatch.setattr(
        module,
        "fetch",
        lambda *_args, **_kwargs: (
            "<GO24><error>개인회원은 사용할 수 없는 OPEN-API입니다.</error></GO24>"
        ),
    )
    with pytest.raises(RuntimeError, match="개인회원") as error:
        list(module.collect_work24(1, 0, secret))
    assert secret not in str(error.value)
    assert "따옴표나 XML 형식 문제가 아닙니다" in str(error.value)


def test_local_env_removes_outer_quotes_from_work24_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    path = tmp_path / ".env"
    path.write_text('WORK24_AUTH_KEY="1234567890"\n', encoding="utf-8")
    monkeypatch.delenv("WORK24_AUTH_KEY", raising=False)

    module.load_local_env(path)

    assert module.os.environ["WORK24_AUTH_KEY"] == "1234567890"


def test_work24_list_and_detail_xml_are_collected_and_deidentified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    responses = iter(
        [
            """
<wantedRoot>
  <wanted>
    <wantedAuthNo>K123</wantedAuthNo>
    <company>테스트기업</company>
    <title>개발자 채용</title>
    <wantedInfoUrl>https://example.test/K123</wantedInfoUrl>
  </wanted>
</wantedRoot>
""",
            """
<wantedRoot>
  <wantedAuthNo>K123</wantedAuthNo>
  <company>테스트기업</company>
  <title>개발자 채용</title>
  <salTpNm>연봉</salTpNm>
  <minSal>40000000</minSal>
  <jobsCd>133200</jobsCd>
</wantedRoot>
""",
        ]
    )
    calls: list[dict[str, str | int]] = []

    def fake_fetch(_url, params, **_kwargs):
        calls.append(dict(params))
        return next(responses)

    monkeypatch.setattr(module, "fetch", fake_fetch)
    records = list(module.collect_work24(1, 0, "secret-auth-key"))
    assert len(records) == 1
    assert records[0]["id"] == "work24:K123"
    assert records[0]["sector"] == "private"
    assert "테스트기업" not in records[0]["text"]
    assert "minSal\n40000000" in records[0]["text"]
    assert calls[0]["callTp"] == "L"
    assert calls[1]["callTp"] == "D"
    assert calls[1]["wantedAuthNo"] == "K123"
    assert "secret-auth-key" not in str(records[0])


def test_senior_job_collects_detail_body_without_contact_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    responses = iter(
        [
            """
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <jobId>RECR_101</jobId>
    <oranNm>Example Company</oranNm>
    <recrtTitle>Kitchen assistant</recrtTitle>
    <emplymShpNm>Part-time</emplymShpNm>
    <jobclsNm>Food service</jobclsNm>
    <workPlcNm>Seoul</workPlcNm>
  </item></items></body>
</response>
""",
            """
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><item>
    <jobId>RECR_101</jobId>
    <wantedTitle>Kitchen assistant</wantedTitle>
    <age>60</age>
    <ageLim>Required</ageLim>
    <detCnts>Example Company needs a kitchen assistant.</detCnts>
    <etcItm>Senior applicants preferred.</etcItm>
    <plbizNm>Example Company</plbizNm>
    <plDetAddr>Exact private address</plDetAddr>
    <clerk>Private Contact</clerk>
    <clerkContt>010-1234-5678</clerkContt>
  </item></body>
</response>
""",
        ]
    )
    calls: list[tuple[str, dict[str, str | int]]] = []

    def fake_fetch(url, params, **_kwargs):
        calls.append((url, dict(params)))
        return next(responses)

    monkeypatch.setattr(module, "fetch", fake_fetch)
    records = list(
        module.collect_senior_job(
            1,
            0,
            "abc%2Bdef%3D",
        )
    )

    assert len(records) == 1
    assert records[0]["id"] == "senior-job:RECR_101"
    assert records[0]["sector"] == "private"
    assert "Example Company" not in records[0]["text"]
    assert "kitchen assistant" in records[0]["text"]
    assert "Private Contact" not in records[0]["text"]
    assert "010-1234-5678" not in records[0]["text"]
    assert "Exact private address" not in records[0]["text"]
    assert calls[0][0].endswith("/getJobList")
    assert calls[1][0].endswith("/getJobInfo")
    assert calls[0][1]["serviceKey"] == "abc+def="
    assert calls[1][1]["id"] == "RECR_101"
    assert "abc%2Bdef%3D" not in str(records[0])


def test_jincheon_jobs_collects_recent_private_rows_and_excludes_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    csv_text = (
        "회사명,기업형태,채용제목,고용형태,경력사항,최종학력,성별,"
        "근무지역,직무내용,접수기간 시작일,회사주소,상세주소,카테고리\n"
        "Old Co,법인,Old job,정규직,경력 무관,학력 무관,성별무관,"
        "충북,Old description,2025-01-01,Old address,Old detail,민간일자리\n"
        "New Co,법인,New job,계약직,신입,고졸,여성,"
        "충북,New Co call 010-1234-5678,2026-01-01,"
        "Private address,Private detail,민간일자리\n"
    )
    monkeypatch.setattr(
        module,
        "fetch_bytes",
        lambda *_args, **_kwargs: csv_text.encode("utf-8-sig"),
    )

    records = list(module.collect_jincheon_jobs(1, 0))

    assert len(records) == 1
    assert records[0]["source"] == "jincheon-jobs"
    assert records[0]["sector"] == "private"
    assert "New job" in records[0]["text"]
    assert "Old job" not in records[0]["text"]
    assert "New Co" not in records[0]["text"]
    assert "010-1234-5678" not in records[0]["text"]
    assert "Private address" not in records[0]["text"]
    assert "Private detail" not in records[0]["text"]
    assert "성별\n여성" in records[0]["text"]


def test_decode_csv_supports_cp949() -> None:
    module = load_collect_module()
    assert module._decode_csv("채용제목".encode("cp949")) == "채용제목"


def test_senior_job_api_error_is_clear_and_does_not_expose_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    secret = "secret-senior-key"
    monkeypatch.setattr(
        module,
        "fetch",
        lambda *_args, **_kwargs: (
            "<response><header><resultCode>30</resultCode>"
            "<resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg>"
            "</header></response>"
        ),
    )

    with pytest.raises(RuntimeError, match="SERVICE KEY") as error:
        list(module.collect_senior_job(1, 0, secret))
    assert secret not in str(error.value)


def test_youth_job_api_collects_json_and_decodes_service_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    calls: list[tuple[str, dict[str, str | int]]] = []

    def fake_fetch(url, params, **_kwargs):
        calls.append((url, dict(params)))
        return json.dumps(
            {
                "resultCode": 200,
                "resultMsg": "NORMAL SERVICE",
                "result": [
                    {
                        "recrutPblntSn": 303077,
                        "instNm": "테스트공공기관",
                        "recrutPbancTtl": "체험형 청년인턴 채용",
                        "aplyQlfcCn": "지원자격 안내",
                        "srcUrl": "https://example.test/303077",
                        "hireTypeNmLst": ["체험형 청년인턴"],
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(module, "fetch", fake_fetch)
    records = list(
        module.collect_youth_job_api(
            1,
            0,
            "abc%2Bdef%3D",
            "https://api.example.test/recruitment/list",
        )
    )

    assert len(records) == 1
    assert records[0]["id"] == "youth-job:303077"
    assert records[0]["source_url"] == "https://example.test/303077"
    assert records[0]["sector"] == "public"
    assert "테스트공공기관" not in records[0]["text"]
    assert "aplyQlfcCn\n지원자격 안내" in records[0]["text"]
    assert calls[0][1]["serviceKey"] == "abc+def="
    assert calls[0][1]["hireTypeLst"] == "R1060"
    assert "abc%2Bdef%3D" not in str(records[0])


def test_youth_job_api_error_is_clear_and_does_not_expose_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    secret = "secret-youth-key"
    monkeypatch.setattr(
        module,
        "fetch",
        lambda *_args, **_kwargs: json.dumps(
            {"resultCode": "03", "resultMsg": "SERVICE KEY IS NOT REGISTERED"}
        ),
    )

    with pytest.raises(RuntimeError, match="SERVICE KEY") as error:
        list(module.collect_youth_job_api(1, 0, secret))
    assert secret not in str(error.value)


def test_youth_job_site_balances_intern_types_and_deidentifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    calls: list[dict[str, str | int]] = []

    def fake_fetch_post(_url, params, **_kwargs):
        calls.append(dict(params))
        source_id = "101" if params["hireTypeLst"] == "R1060" else "202"
        return json.dumps(
            {
                "data": {
                    "result": [
                        {
                            "recrutPblntSn": source_id,
                            "instNm": "테스트기관",
                            "recrutPbancTtl": "청년인턴 채용",
                            "hireTypeNmLst": ["청년인턴"],
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(module, "fetch_post", fake_fetch_post)
    records = list(module.collect_youth_job_site(2, 0))

    assert [call["hireTypeLst"] for call in calls] == ["R1060", "R1070"]
    assert [record["source_id"] for record in records] == ["101", "202"]
    assert all("테스트기관" not in record["text"] for record in records)
    assert all(
        record["source_url"] == module.YOUTH_JOB_SITE_SEARCH
        for record in records
    )


def test_youth_job_site_keeps_page_size_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    calls: list[dict[str, str | int]] = []

    def fake_fetch_post(_url, params, **_kwargs):
        calls.append(dict(params))
        page = int(params["pageNo"])
        base = 0 if params["hireTypeLst"] == "R1060" else 1000
        start = base + ((page - 1) * 100)
        return json.dumps(
            {
                "data": {
                    "result": [
                        {
                            "recrutPblntSn": str(start + index),
                            "recrutPbancTtl": "청년인턴 채용",
                        }
                        for index in range(100)
                    ]
                }
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(module, "fetch_post", fake_fetch_post)
    records = list(module.collect_youth_job_site(300, 0))

    assert len(records) == 300
    assert [call["numOfRows"] for call in calls] == [100, 100, 100, 100]
    assert [call["pageNo"] for call in calls] == [1, 2, 1, 2]


def test_cleaneye_collects_public_detail_and_deidentifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_collect_module()
    calls: list[tuple[str, dict[str, str | int]]] = []

    def fake_fetch_post(url, params, **_kwargs):
        calls.append((url, dict(params)))
        if url == module.CLEANEYE_AJAX_LIST:
            return json.dumps(
                {
                    "cnt": 1,
                    "list": [
                        {
                            "empyear": "2026",
                            "ypEntId": "B000001",
                            "entSeq": "000001",
                            "entName": "테스트지방공기업",
                            "entTitle": "행정직 채용",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        return """
<html><body>
  <div class="detail_info_box">
    <h2>테스트지방공기업</h2>
    <h3>행정직 채용</h3>
    <h4>전형절차</h4>
    <p>서류전형 후 면접전형을 실시하며 각 단계의 결과는 이메일로 안내합니다.</p>
    <h4>근무조건</h4>
    <p>연봉 4,000만원이며 주 5일 근무합니다.</p>
    <h4>문의처</h4>
    <p>인사팀 02-1234-5678 recruit@example.com</p>
  </div>
  <div class="organ_info">기관 소개는 수집하지 않습니다.</div>
</body></html>
"""

    monkeypatch.setattr(module, "fetch_post", fake_fetch_post)
    records = list(module.collect_cleaneye(1, 0))

    assert len(records) == 1
    assert records[0]["id"] == "cleaneye:2026:B000001:000001"
    assert records[0]["sector"] == "public"
    assert records[0]["source_url"] == module.CLEANEYE_LIST
    assert "테스트지방공기업" not in records[0]["text"]
    assert "02-1234-5678" not in records[0]["text"]
    assert "recruit@example.com" not in records[0]["text"]
    assert "기관 소개" not in records[0]["text"]
    assert calls[0][1] == {"pageIndex": 1}
    assert calls[1][1] == {
        "empyear": "2026",
        "ypEntId": "B000001",
        "entSeq": "000001",
    }
