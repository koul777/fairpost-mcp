"""Rule id shape and result ordering (CODE-6, 2026-10-11).

Results are sorted by rule id. Python compares str by code point, so the web
engine must not use locale collation (localeCompare puts "Q-a-001" before
"Q-B-001", Python does the opposite), and the loader fixes the id shape so the
order stays predictable for every rule file, local rules included.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from core import RuleLoadError, load_ruleset
from core.loader import RULE_ID_PATTERN


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RULE_FILES = [
    DATA / "rules" / "law.yaml",
    DATA / "rules" / "questions.yaml",
    DATA / "local_rules.example.yaml",
]


def test_every_shipped_and_example_rule_id_has_the_validated_shape() -> None:
    ids = [rule["id"] for path in RULE_FILES for rule in yaml.safe_load(path.read_text(encoding="utf-8"))]
    assert ids
    assert [rule_id for rule_id in ids if not RULE_ID_PATTERN.fullmatch(rule_id)] == []
    # The example local rules load with the shipped ones.
    ruleset = load_ruleset(local_rules_path=DATA / "local_rules.example.yaml")
    assert {"AGE-002", "Q-DIST-017", "Q-LOCAL-001"} <= {rule["id"] for rule in ruleset.rules}


@pytest.mark.parametrize(
    "bad_id",
    ["age-002", "AGE-02", "AGE-0002", "AGE_002", "AGE-002 ", "Q--DIST-001", "-AGE-002",
     "연령-002", "AGE-００２", "ÄGE-002", 2],
)
def test_rule_id_with_another_shape_is_rejected_in_korean(tmp_path: Path, bad_id: object) -> None:
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    path = copied / "rules" / "law.yaml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules[0]["id"] = bad_id
    path.write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(RuleLoadError, match="규칙 id는 .*세 자리 번호"):
        load_ruleset(copied)


def test_local_rule_id_shape_is_validated_too(tmp_path: Path) -> None:
    path = tmp_path / "local_rules.yaml"
    path.write_text(
        """
- id: Q-LOCAL-ENV
  layer: question
  trigger: {type: presence, patterns: [환경 규칙 확인]}
  dimension: 절차
  question: 로컬 규칙입니까?
  basis: {type: consensus}
  provenance: {method: manual, reviewed_by: test, reviewed_at: "2026-10-11"}
  book_ref: 기관 규정
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(RuleLoadError, match="Q-LOCAL-ENV: 규칙 id는"):
        load_ruleset(local_rules_path=path)


_ORDER_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const context = { window: {} };
context.globalThis = context.window;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const ids = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const rule = (id) => ({
  id, layer: "question", trigger: { type: "presence", patterns: ["순서 확인"] },
  dimension: "절차", question: "순서를 확인합니까?", basis: { type: "consensus" },
  provenance: { method: "manual" }, book_ref: "테스트",
});
const data = {
  rules: ids.map(rule), slots: {}, statutes: { s: { snapshot_date: "2026-07-26" } }, version: "test",
};
const result = context.window.FairpostEngine.check("순서 확인", {}, data);
process.stdout.write(JSON.stringify(result.questions.map((question) => question.id)));
"""


@pytest.mark.requires_node
def test_web_engine_orders_ids_by_code_point_like_python(tmp_path: Path) -> None:
    # Lower case after upper case, a fullwidth letter (U+FF21) before an astral
    # one (U+1D400, a surrogate pair in UTF-16), a prefix before its extension.
    ids = ["Q-a-001", "Q-B-001", "Q-\U0001d400-001", "Q-Ａ-001", "Q-B-001X", "Q-É-001", "Q-A-001"]
    script = tmp_path / "order.cjs"
    script.write_text(_ORDER_SCRIPT, encoding="utf-8")
    payload = tmp_path / "ids.json"
    payload.write_text(json.dumps(ids), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(script), str(ROOT / "web" / "engine.js"), str(payload)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    assert json.loads(completed.stdout) == sorted(ids)
    assert sorted(ids)[:2] == ["Q-A-001", "Q-B-001"]


def test_web_engine_does_not_sort_with_locale_collation() -> None:
    source = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    assert "localeCompare" not in source
