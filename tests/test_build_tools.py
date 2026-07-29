from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import locale
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "tools" / f"{name}.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_web_bundle_is_current() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/export_web_bundle.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_statute_snapshot_hashes_are_current() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/build_statutes.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_official_statute_article_parser_preserves_order_and_effective_date() -> None:
    module = load_tool("build_statutes")
    root = ET.fromstring(
        """
<법령>
  <기본정보><법령ID>123456</법령ID></기본정보>
  <조문>
    <조문단위>
      <조문번호>4</조문번호>
      <조문가지번호>3</조문가지번호>
      <조문여부>조문</조문여부>
      <조문제목>테스트 조문</조문제목>
      <조문시행일자>20260701</조문시행일자>
      <조문내용>제4조의3(테스트 조문) 본문</조문내용>
      <항><항내용>① 첫째 항</항내용><호><호내용>1. 첫째 호</호내용></호></항>
      <조문참고자료>[본조신설 2026.1.1]</조문참고자료>
    </조문단위>
  </조문>
</법령>
""".strip()
    )
    articles, official_id = module.official_articles(root, {"제4조의3"})
    assert official_id == "123456"
    assert articles["제4조의3"]["effective_date"] == "2026-07-01"
    assert articles["제4조의3"]["text"] == (
        "제4조의3(테스트 조문) 본문\n\n"
        "① 첫째 항\n\n"
        "1. 첫째 호\n\n"
        "[본조신설 2026.1.1]"
    )
    assert articles["제4조의3"]["hash"] == module.article_hash(
        articles["제4조의3"]["text"]
    )


def test_statute_audit_maps_articles_to_affected_rule_ids(tmp_path: Path) -> None:
    module = load_tool("build_statutes")
    rules_path = tmp_path / "law.yaml"
    rules_path.write_text(
        """
- id: TEST-002
  basis: {type: statute, statute_id: test-act, article: 제2조}
- id: TEST-001
  basis: {type: statute, statute_id: test-act, article: 제2조}
- id: QUESTION-001
  basis: {type: consensus}
""".strip(),
        encoding="utf-8",
    )

    assert module._rule_impact(rules_path) == {
        ("test-act", "제2조"): ["TEST-001", "TEST-002"]
    }


def test_evaluation_rejects_text_hash_mismatch(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    path = tmp_path / "annotations.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": "0" * 64,
                "text": "공고문",
                "expected_findings": [],
                "expected_absent_slots": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="일치하지 않습니다"):
        module.load_jsonl(path)


def test_evaluation_requires_complete_holdout_by_default(tmp_path: Path) -> None:
    text_one = "첫 번째 공고"
    text_two = "두 번째 공고"
    hash_one = hashlib.sha256(text_one.encode("utf-8")).hexdigest()
    hash_two = hashlib.sha256(text_two.encode("utf-8")).hexdigest()
    train_manifest = tmp_path / "train.json"
    holdout_manifest = tmp_path / "holdout.json"
    holdout_records = tmp_path / "records.jsonl"
    annotations = tmp_path / "annotations.jsonl"
    train_manifest.write_text('{"content_hashes":[]}', encoding="utf-8")
    holdout_manifest.write_text(
        json.dumps({"content_hashes": [hash_one, hash_two]}),
        encoding="utf-8",
    )
    holdout_records.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False)
            for record in (
                {
                    "id": "test:1",
                    "content_hash": hash_one,
                    "text": text_one,
                    "sector": "public",
                    "source": "test-public",
                },
                {
                    "id": "test:2",
                    "content_hash": hash_two,
                    "text": text_two,
                    "sector": "private",
                    "source": "test-private",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    annotations.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": hash_one,
                "text": text_one,
                "expected_findings": [],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            str(annotations),
            "--train-manifest",
            str(train_manifest),
            "--holdout-manifest",
            str(holdout_manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )
    assert completed.returncode != 0
    assert "홀드아웃 전체 라벨" in completed.stderr


def test_evaluation_metrics_are_undefined_without_denominators() -> None:
    module = load_tool("evaluate")

    result = module.metrics(0, 0, 0)

    assert result["precision"] is None
    assert result["recall"] is None


def test_evaluation_requires_expression_labels_to_match_findings(
    tmp_path: Path,
) -> None:
    module = load_tool("evaluate")
    text = "라벨 일관성 테스트"
    path = tmp_path / "annotations.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "expected_findings": ["SEX-001"],
                "expected_absent_slots": [],
                "expected_expressions": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_findings와"):
        module.load_jsonl(path)


def test_evaluation_target_gate_requires_90_records_per_sector() -> None:
    module = load_tool("evaluate")
    report = {
        "complete_holdout": True,
        "expression_detection": {"precision": 0.95, "recall": 0.50},
        "absence_detection": {"precision": 0.90, "recall": 0.90},
    }

    gate = module._target_gate(
        report,
        module.Counter({"public": 89, "private": 90}),
    )

    assert gate["passed"] is False
    assert gate["checks"]["public_holdout_at_least_90"] is False
    assert gate["checks"]["private_holdout_at_least_90"] is True


def test_holdout_records_must_exactly_match_manifest(tmp_path: Path) -> None:
    module = load_tool("evaluate")
    text = "홀드아웃 원문"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    missing_hash = "f" * 64
    path = tmp_path / "records.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "test:1",
                "content_hash": content_hash,
                "text": text,
                "sector": "public",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="누락 1"):
        module.load_holdout_records(path, {content_hash, missing_hash})


def test_evaluation_enforces_targets_on_complete_two_sector_holdout(
    tmp_path: Path,
) -> None:
    from core import FairpostEngine

    engine = FairpostEngine()
    train_manifest = tmp_path / "train.json"
    holdout_manifest = tmp_path / "manifest.json"
    holdout_records = tmp_path / "records.jsonl"
    annotations = tmp_path / "annotations.jsonl"
    output = tmp_path / "evaluation.json"
    records = []
    labels = []
    for index in range(180):
        sector = "public" if index < 90 else "private"
        text = (
            "지원자격: 남성만 지원 가능"
            if index == 0
            else f"일반 채용공고 {index}"
        )
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        record_id = f"{sector}:{index}"
        result = engine.check(text)
        finding_ids = [finding.id for finding in result.findings]
        records.append(
            {
                "id": record_id,
                "content_hash": content_hash,
                "text": text,
                "sector": sector,
                "source": f"synthetic-{sector}",
            }
        )
        labels.append(
            {
                "id": record_id,
                "content_hash": content_hash,
                "text": text,
                "expected_findings": finding_ids,
                "expected_absent_slots": [
                    slot.slot for slot in result.slots if not slot.found
                ],
                "expected_expressions": [
                    {"rule_id": rule_id} for rule_id in finding_ids
                ],
            }
        )
    hashes = [record["content_hash"] for record in records]
    train_manifest.write_text('{"content_hashes":[]}', encoding="utf-8")
    holdout_manifest.write_text(
        json.dumps({"content_hashes": hashes}),
        encoding="utf-8",
    )
    holdout_records.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )
    annotations.write_text(
        "".join(
            json.dumps(label, ensure_ascii=False) + "\n" for label in labels
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            str(annotations),
            "--train-manifest",
            str(train_manifest),
            "--holdout-manifest",
            str(holdout_manifest),
            "--holdout-records",
            str(holdout_records),
            "--enforce-targets",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["target_gate"]["passed"] is True
    assert report["holdout_by_sector"] == {"private": 90, "public": 90}
    assert report["measurement_units"]["expression_detection"] == (
        "posting_rule_pair"
    )


def test_annotation_ui_is_local_and_embeds_exact_holdout(tmp_path: Path) -> None:
    module = load_tool("build_annotation_ui")
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    text = "비식별화된 봉인 공고문"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    records_path = holdout / "records.jsonl"
    manifest_path = holdout / "manifest.json"
    records_path.write_text(
        json.dumps(
            {
                "id": "test:holdout:1",
                "content_hash": content_hash,
                "text": text,
                "sector": "public",
                "occupation": "office",
                "employment_type": "regular",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps({"content_hashes": [content_hash]}),
        encoding="utf-8",
    )

    records = module.load_records(records_path, manifest_path)
    html = module.build_html(module.build_payload(records))
    assert "connect-src 'none'" in html
    assert "http://" not in html
    assert "https://" not in html
    assert text not in html

    encoded = re.search(
        r'<script id="payload" type="application/octet-stream">([^<]+)</script>',
        html,
    )
    assert encoded
    payload = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert payload["records"] == records
    assert len(payload["law_rules"]) >= 15
    assert len(payload["slots"]) == 11


def test_annotation_ui_rejects_non_holdout_input(tmp_path: Path) -> None:
    module = load_tool("build_annotation_ui")
    train = tmp_path / "train"
    train.mkdir()
    with pytest.raises(ValueError, match="holdout 경로"):
        module.load_records(train / "records.jsonl", train / "manifest.json")


def test_combiner_preserves_fixed_public_and_private_partitions(
    tmp_path: Path,
) -> None:
    module = load_tool("combine_corpora")

    def record(record_id: str, text: str, sector: str) -> dict[str, str]:
        return {
            "id": record_id,
            "text": text,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": f"{sector}-source",
            "sector": sector,
            "occupation": "office",
            "employment_type": "regular",
        }

    def write_corpus(
        root: Path,
        train: list[dict[str, str]],
        holdout: list[dict[str, str]],
    ) -> None:
        for split, records in (("train", train), ("holdout", holdout)):
            directory = root / split
            directory.mkdir(parents=True)
            module._write_jsonl(directory / "records.jsonl", records)
            module._write_manifest(directory / "manifest.json", records)

    public_dir = tmp_path / "public"
    private_dir = tmp_path / "private"
    public_train = record("public:train", "공공 학습", "public")
    public_holdout = record("public:holdout", "공공 홀드아웃", "public")
    private_train = record("private:train", "민간 학습", "private")
    private_holdout = record("private:holdout", "민간 홀드아웃", "private")
    write_corpus(public_dir, [public_train], [public_holdout])
    write_corpus(private_dir, [private_train], [private_holdout])

    train, holdout, summary = module.combine(
        public_dir,
        private_dir,
        expected_public=2,
        expected_private=2,
        train_ratio=0.5,
    )
    assert {item["id"] for item in train} == {
        "public:train",
        "private:train",
    }
    assert {item["id"] for item in holdout} == {
        "public:holdout",
        "private:holdout",
    }
    assert summary["sectors"] == {"private": 2, "public": 2}
    assert summary["combined_from_fixed_partitions"] is True


def test_combiner_rejects_occupation_outside_prd_four_classes(
    tmp_path: Path,
) -> None:
    module = load_tool("combine_corpora")
    root = tmp_path / "corpus"
    text = "직군 미분류"
    record = {
        "id": "test:1",
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source": "test",
        "sector": "public",
        "occupation": "other",
        "employment_type": "regular",
    }
    directory = root / "train"
    directory.mkdir(parents=True)
    module._write_jsonl(directory / "records.jsonl", [record])
    module._write_manifest(directory / "manifest.json", [record])
    with pytest.raises(ValueError, match="허용되지 않은 직군"):
        module.read_partition(root, "train")


def test_prd_corpus_selection_is_exact_stratified_and_text_independent() -> None:
    module = load_tool("build_prd_corpus")
    records = []
    occupations = ("office", "tech", "research", "field")
    employment_types = ("regular", "temporary")
    for index in range(80):
        records.append(
            {
                "id": f"private:{index}",
                "occupation": occupations[index % len(occupations)],
                "employment_type": employment_types[
                    (index // len(occupations)) % len(employment_types)
                ],
                "text": f"선택에 사용하면 안 되는 원문 {index}",
            }
        )

    first = module.select_stratified(records, 24)
    modified = [
        {**record, "text": f"완전히 다른 원문 {record['id']}"}
        for record in records
    ]
    second = module.select_stratified(modified, 24)

    assert len(first) == 24
    assert [record["id"] for record in first] == [
        record["id"] for record in second
    ]
    assert {
        (record["occupation"], record["employment_type"])
        for record in first
    } == {
        (record["occupation"], record["employment_type"])
        for record in records
    }


def test_prd_corpus_selection_rejects_invalid_target() -> None:
    module = load_tool("build_prd_corpus")
    records = [
        {
            "id": "private:1",
            "occupation": "office",
            "employment_type": "regular",
        }
    ]
    with pytest.raises(ValueError, match="선택 목표"):
        module.select_stratified(records, 0)
    with pytest.raises(ValueError, match="선택 목표"):
        module.select_stratified(records, 2)


def test_reclassifier_preserves_ids_hashes_and_fixed_membership(
    tmp_path: Path,
) -> None:
    combine = load_tool("combine_corpora")
    reclassify = load_tool("reclassify_corpus")
    root = tmp_path / "corpus"
    summary_path = tmp_path / "summary.json"

    def record(record_id: str, text: str, split: str) -> dict[str, str]:
        return {
            "id": record_id,
            "text": text,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": "test",
            "sector": "public" if split == "train" else "private",
            "occupation": "other",
            "employment_type": "regular",
        }

    original = {
        "train": [
            record("train:office", "행정 사무 담당", "train"),
            record("train:tech", "소프트웨어 개발 담당", "train"),
            record("train:research", "연구 실험 담당", "train"),
            record("train:field", "생산 현장 담당", "train"),
        ],
        "holdout": [
            record("holdout:1", "직무 상세 미기재", "holdout"),
        ],
    }
    for split, records in original.items():
        directory = root / split
        directory.mkdir(parents=True)
        combine._write_jsonl(directory / "records.jsonl", records)
        combine._write_manifest(directory / "manifest.json", records)

    result = reclassify.migrate(root, summary_path)
    migrated_train = combine.read_partition(root, "train")
    migrated_holdout = combine.read_partition(root, "holdout")

    assert [item["id"] for item in migrated_train] == [
        item["id"] for item in original["train"]
    ]
    assert [item["id"] for item in migrated_holdout] == [
        item["id"] for item in original["holdout"]
    ]
    assert [item["content_hash"] for item in migrated_train] == [
        item["content_hash"] for item in original["train"]
    ]
    assert result["occupations"] == {
        "field": 2,
        "office": 1,
        "research": 1,
        "tech": 1,
    }
    assert json.loads(summary_path.read_text(encoding="utf-8"))[
        "reclassified_preserving_fixed_partitions"
    ] is True


def test_vercel_configuration_excludes_private_inputs() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["functions"]["api/index.py"]["maxDuration"] == 30
    rewrites = {
        item["source"]: item["destination"] for item in config["rewrites"]
    }
    assert rewrites == {
        "/api/claude-mcp": "/api",
        "/api/health": "/api",
        "/api/mcp": "/api",
    }
    assert (ROOT / "index.html").is_file()
    ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8")
    for private_path in (
        ".env",
        ".corpus*/",
        "answers.json",
        "data/local_rules.yaml",
    ):
        assert private_path in ignored


def test_data_validator_accepts_committed_dictionaries() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/validate_data.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr


def test_data_validator_checks_private_corpus_provenance(tmp_path: Path) -> None:
    module = load_tool("validate_data")
    public = tmp_path / "public.json"
    private = tmp_path / "private.json"
    public.write_text(
        json.dumps(
            {
                "matching_version": "test-matching-version",
                "law_rule_posting_hits": {"TEST-001": 0},
            }
        ),
        encoding="utf-8",
    )
    private.write_text(
        json.dumps(
            {
                "matching_version": "test-matching-version",
                "law_rule_posting_hits": {"TEST-001": 3},
            }
        ),
        encoding="utf-8",
    )
    rule = {
        "id": "TEST-001",
        "layer": "law",
        "provenance": {"corpus_split": "private", "corpus_hits": 2},
    }

    with pytest.raises(ValueError, match="private 코퍼스 집계"):
        module.validate_corpus_hits(
            (rule,),
            "test-matching-version",
            public,
            private,
        )


def test_corpus_reports_survive_statute_only_ruleset_change(tmp_path: Path) -> None:
    module = load_tool("validate_data")
    public = tmp_path / "public.json"
    private = tmp_path / "private.json"
    for path in (public, private):
        path.write_text(
            json.dumps(
                {
                    "ruleset_version": "older-full-ruleset-version",
                    "matching_version": "stable-matching-version",
                    "law_rule_posting_hits": {"TEST-001": 0},
                }
            ),
            encoding="utf-8",
        )
    rule = {
        "id": "TEST-001",
        "layer": "law",
        "provenance": {"corpus_split": "public", "corpus_hits": 0},
    }

    module.validate_corpus_hits(
        (rule,),
        "stable-matching-version",
        public,
        private,
    )


def test_public_reports_directory_contains_no_raw_jsonl() -> None:
    assert not list((ROOT / "reports").glob("*.jsonl"))


def test_project_mcp_config_uses_authenticated_remote_and_local_alias() -> None:
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))

    assert config == {
        "mcpServers": {
            "fairpost": {
                "type": "http",
                "url": "https://fairpost-mcp.vercel.app/api/mcp",
                "headers": {
                    "Authorization": "Bearer ${FAIRPOST_MCP_TOKEN}",
                },
            },
            "fairpost-local": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
            },
        }
    }


def test_distribution_audit_rejects_private_build_artifacts() -> None:
    module = load_tool("verify_distribution")
    assert {
        "docs/question-relevance-audit.md",
        "reports/question_relevance_audit.json",
        "reports/question_relevance_manual_review.json",
    } <= module.SDIST_REQUIRED
    names = {
        "README.md",
        "reports/summary.json",
        "reports/private_open_candidate_batches.jsonl",
        "reports/build_artifact.json",
        "reports/distribution_audit.json",
        ".corpus-final/train/records.jsonl",
        ".env",
    }

    violations = module._forbidden(names)

    assert ".env" in violations
    assert ".corpus-final/train/records.jsonl" in violations
    assert "reports/build_artifact.json" in violations
    assert "reports/distribution_audit.json" in violations
    assert "reports/private_open_candidate_batches.jsonl" in violations


def test_web_parity_auditor_rejects_holdout_path(tmp_path: Path) -> None:
    module = load_tool("verify_web_parity")
    path = tmp_path / "holdout" / "records.jsonl"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="봉인 홀드아웃"):
        module.audit(path)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js가 필요합니다")
def test_web_parity_auditor_compares_training_records(tmp_path: Path) -> None:
    module = load_tool("verify_web_parity")
    path = tmp_path / "train" / "records.jsonl"
    path.parent.mkdir()
    records = [
        {"id": "train:1", "text": "청년인턴 채용"},
        {"id": "train:2", "text": "자격요건\n남성만 지원 가능"},
    ]
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )

    report = module.audit(path)

    assert report["passed"] is True
    assert report["matched_records"] == 2
    assert report["mismatched_records"] == 0
    assert report["contains_posting_text"] is False
