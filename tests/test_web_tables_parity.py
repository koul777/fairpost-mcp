"""The hand-copied tables of web/engine.js must equal the Python ones they mirror.

web/engine.js is a hand-maintained port of core/morph.py, core/extractor.py and
core/engine.py. The behavioural parity tests only notice a divergent table when
one of their inputs happens to hit it; a section alias, a protective phrase or
a contact term changed on one side only goes unseen. These tests compare the
tables themselves, live: they read the values out of the running modules and out
of the loaded engine (tests/js_tables.cjs), never a snapshot, so an edit that
reaches both languages passes and one that reaches only one fails.

Three layers:

* every named table (``TABLES``) and every translated regex (``REGEXES``) is
  compared one by one, so a failure names the table and the differing entries;
* every array of plain strings in either language, named or inline in a
  function, must exist in the other (``PYTHON_ONLY_LISTS`` and
  ``JS_ONLY_LISTS`` record the few that have a reason not to);
* a JS table that is not on either list fails, so a new table cannot be added
  without deciding whether it mirrors a Python one.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

import pytest

from core import engine as core_engine
from core import extractor, morph
from web_engine import ROOT, run_node

pytestmark = pytest.mark.requires_node

TABLES_RUNNER = ROOT / "tests" / "js_tables.cjs"
ENGINE_JS = ROOT / "web" / "engine.js"


@dataclass(frozen=True)
class Table:
    """A JS binding and the Python value it mirrors."""

    js: str
    python: Callable[[], Any]
    # "exact": same entries in the same order. "unordered": the Python side is
    # a set (or only membership matters), so compare sorted entries.
    compare: str = "exact"

    def __str__(self) -> str:  # test id
        return self.js


def _x(name: str) -> Callable[[], Any]:
    return lambda: getattr(extractor, name)


TABLES: tuple[Table, ...] = (
    Table("DISCLAIMER", lambda: core_engine.DISCLAIMER),
    Table("MORPH_REWRITES", lambda: morph.MORPH_REWRITES),
    Table("ZERO_WIDTH", lambda: morph.ZERO_WIDTH, "unordered"),
    Table("SECTION_ALIASES", lambda: extractor.SECTION_ALIASES),
    Table("DUTY_HEADINGS", _x("_DUTY_HEADINGS")),
    Table("HIRING_CONTEXT", _x("_HIRING_CONTEXT")),
    Table("DUTY_CONTEXT", _x("_DUTY_CONTEXT")),
    Table("CONTACT_CONTEXT", _x("_CONTACT_CONTEXT")),
    Table("CONTACT_NON_CHANNEL", _x("_CONTACT_NON_CHANNEL")),
    Table("CONTACT_CHANNEL", _x("_CONTACT_CHANNEL")),
    Table("DEFERRED_BODY", _x("_DEFERRED_BODY")),
    Table("DATE_CONTENT", _x("_DATE_CONTENT")),
    Table("STAGE_EVENTS", _x("_STAGE_EVENTS")),
    Table("BARE_STAGE_TERMS", _x("_BARE_STAGE_TERMS")),
    Table("BARE_INTERVIEW_STAGE", _x("_BARE_INTERVIEW_STAGE")),
    Table("STAGE_SECTIONS", _x("_STAGE_SECTIONS"), "unordered"),
    Table("STAGE_MARKERS", _x("_STAGE_MARKERS")),
    Table("SENTENCE_END", _x("_SENTENCE_END")),
    Table("SENSITIVE_CANDIDATE", _x("_SENSITIVE_CANDIDATE")),
    Table("NOT_AN_INFORMATION_ITEM", _x("_NOT_AN_INFORMATION_ITEM")),
    Table("PROTECTIVE", _x("_PROTECTIVE")),
    Table("PROTECTIVE_INDIFFERENT", _x("_PROTECTIVE_INDIFFERENT")),
    Table("PROTECTIVE_CONTRAST", _x("_PROTECTIVE_CONTRAST")),
    Table("PROTECTIVE_OMISSION_PENALTY", _x("_PROTECTIVE_OMISSION_PENALTY")),
    Table("PROTECTIVE_REQUIREMENT", _x("_PROTECTIVE_REQUIREMENT")),
    Table("PROTECTIVE_CLAUSE_END", _x("_PROTECTIVE_CLAUSE_END")),
    Table("PROTECTIVE_COMMA", _x("_PROTECTIVE_COMMA")),
    Table("PROTECTIVE_CONDITIONAL_TAIL", _x("_PROTECTIVE_CONDITIONAL_TAIL")),
    Table("PROTECTIVE_PREDICATE_TAIL", _x("_PROTECTIVE_PREDICATE_TAIL")),
    Table("PROTECTIVE_NON_LIST_HEAD", _x("_PROTECTIVE_NON_LIST_HEAD")),
    Table("PROTECTIVE_DIRECT_GAP", _x("_PROTECTIVE_DIRECT_GAP")),
    Table("PROTECTIVE_TOKEN", _x("_PROTECTIVE_TOKEN")),
    Table("PROTECTIVE_LIST_ITEM_TOKENS", _x("_PROTECTIVE_LIST_ITEM_TOKENS")),
)

# JS bindings built with pythonRegex(source, flags) from a Python pattern.
REGEXES: tuple[tuple[str, Callable[[], re.Pattern[str]]], ...] = (
    ("HEADING_DECORATION", _x("_HEADING_DECORATION")),
    ("WHITESPACE_RUN", _x("_WHITESPACE")),
    ("LINE_MARKER", _x("_LINE_MARKER")),
    ("ATTACHMENT_SUFFIX", _x("_ATTACHMENT_FILENAME")),
)

# Module-level constants of engine.js that are not tables mirrored from Python:
# lookup caches and the machinery behind pythonRegex / Python whitespace, which
# the tests below check against Python's own behaviour instead.
JS_ONLY_CONSTANTS = frozenset(
    {
        "PY_SPACE_CLASS",  # checked through isPythonWhitespaceCode / \s below
        "PY_NON_SPACE_CLASS",
        "PYTHON_LINE_BREAKS",  # checked against str.splitlines below
        "PYTHON_LINE_BREAK_CODES",  # derived from PYTHON_LINE_BREAKS
        "CHAR_KNOWN",  # bit flags of the per-character state table
        "CHAR_KEEP_TEXT",
        "CHAR_KEEP_HEADING",
        "NO_SCAN",  # marks a pattern whose scan was skipped because a required literal is missing
        "SCAN_MEMO_MIN",  # shortest text whose per-pattern scans are kept with its views
        # Wording lists that candidateUnits, contextAllowed and sourceCandidateAllowed
        # wrote inline; hoisted so the pattern-list cache finds them by identity.
        # Their contents are compared with core/extractor.py by the string-list tests.
        "INTERVIEW_TEASERS",
        "EXPERIENCE_TERMS",
        "PARTICIPATION_TERMS",
        "PAY_TERMS",
        "EMERGENCY_CONTACT_TERMS",
        "APPLICATION_FORM_TERMS",
        "INQUIRY_TERMS",
        "CONTACT_ASK_TERMS",
        "DEFECTOR_TERMS",
        "BONUS_ROW_TERMS",
        "NO_ENTRY_TERMS",
        "EXCLUSION_TERMS",
        "CRIMINAL_TERMS",
        "APPLICATION_PERIOD_TERMS",
        "OPEN_ENDED_TERMS",
        "CONTACT_RANK_TERMS",
        "CRITERIA_RANK_TERMS",
    }
)

# Arrays of plain strings that exist in only one language, with the reason.
# Keys are the sorted entries, so moving a list or reordering it changes nothing.
PYTHON_ONLY_LISTS: dict[tuple[str, ...], str] = {
    ("면접",): "appended to _STAGE_CONTEXT_PATTERNS; the JS spreads the same string inline",
    ("-", "~", "～"): "str.endswith() argument; the JS tests the last character instead",
    ("bracket", "mark"): "labels returned by _line_marker; the JS compares other tokens",
    ("\t", "\n", "\r"): "_KEPT_CONTROLS; the JS keeps them through its per-character state table",
    ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"): "re._constants names used to prefilter patterns",
}
JS_ONLY_LISTS: dict[tuple[str, ...], str] = {
    tuple(
        sorted(
            ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]
        )
    ): "PYTHON_LINE_BREAKS; str.splitlines has no list, test_line_break_set_matches_str_splitlines checks it",
}


def _short(value: object, limit: int = 700) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"


def _canonical(value: Any) -> Any:
    """Plain lists/strings/numbers; a set becomes {"$set": sorted entries}."""
    if isinstance(value, dict) and set(value) == {"$set"}:
        return {"$set": sorted(_canonical(item) for item in value["$set"])}
    if isinstance(value, (set, frozenset)):
        return {"$set": sorted(_canonical(item) for item in value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _entries(value: Any) -> list[Any]:
    value = _canonical(value)
    if isinstance(value, dict):
        value = value["$set"]
    return sorted(value, key=repr)


def _describe(js: Any, py: Any) -> str:
    if isinstance(js, list) and isinstance(py, list):
        only_js = [item for item in js if item not in py]
        only_py = [item for item in py if item not in js]
        if only_js or only_py:
            return (
                f"only in web/engine.js: {_short(only_js)}; "
                f"only in core: {_short(only_py)}"
            )
        return f"same entries in a different order: js={_short(js)} py={_short(py)}"
    return f"web/engine.js={_short(js)} core={_short(py)}"


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    """Everything read out of web/engine.js, in one Node process."""
    request = {
        "names": [table.js for table in TABLES] + ["PYTHON_LINE_BREAKS"],
        "regexes": [
            {
                "name": name,
                "source": getter().pattern,
                "flags": "u" + ("i" if getter().flags & re.IGNORECASE else ""),
            }
            for name, getter in REGEXES
        ],
        "whitespace": True,
    }
    with tempfile.TemporaryDirectory(prefix="fairpost-js-tables-") as directory:
        path = Path(directory) / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        return json.loads(run_node([str(TABLES_RUNNER), str(path)]))


@pytest.mark.parametrize("table", TABLES, ids=str)
def test_table_matches_python(table: Table, probe: dict[str, Any]) -> None:
    found = probe["tables"][table.js]
    assert "error" not in found, (
        f"web/engine.js has no readable binding {table.js}: {found['error']}. "
        "If the table was renamed, update TABLES in tests/test_web_tables_parity.py."
    )
    python = table.python()
    if table.compare == "unordered":
        js_value, py_value = _entries(found["value"]), _entries(python)
    else:
        js_value, py_value = _canonical(found["value"]), _canonical(python)
    assert js_value == py_value, f"{table.js} differs: {_describe(js_value, py_value)}"


@pytest.mark.parametrize("name", [name for name, _ in REGEXES])
def test_translated_regex_matches_python(name: str, probe: dict[str, Any]) -> None:
    found = probe["regexes"][name]
    assert "error" not in found, f"web/engine.js binding {name}: {found.get('error')}"
    assert found["actual"] == found["expected"], (
        f"{name}: the JS regex is not the translation of the Python pattern. "
        f"web/engine.js={_short(found['actual'])} expected={_short(found['expected'])}"
    )
    # g (stateful scan) is a JS-only choice; u is always on; i must follow re.I.
    assert set(found["actualFlags"]) - {"g", "y"} == set(found["expectedFlags"]), name


def test_python_whitespace_and_line_breaks_match_the_engine(probe: dict[str, Any]) -> None:
    python_space = [
        code
        for code in range(0x110000)
        if not 0xD800 <= code <= 0xDFFF and chr(code).isspace()
    ]
    assert probe["whitespace"] == python_space, (
        "isPythonWhitespaceCode disagrees with str.isspace(): "
        f"only JS {sorted(set(probe['whitespace']) - set(python_space))!r}, "
        f"only Python {sorted(set(python_space) - set(probe['whitespace']))!r}"
    )
    assert probe["regexSpace"] == python_space, (
        "the translation of Python \\s disagrees with str.isspace()"
    )


def test_line_break_set_matches_str_splitlines(probe: dict[str, Any]) -> None:
    python = {
        chr(code)
        for code in range(0x10000)
        if not 0xD800 <= code <= 0xDFFF and len(f"a{chr(code)}b".splitlines()) > 1
    }
    found = probe["tables"]["PYTHON_LINE_BREAKS"]
    assert "error" not in found, found
    assert set(_entries(found["value"])) == python


def _python_string_lists() -> dict[tuple[str, ...], list[str]]:
    """Every list/tuple/set of plain strings in the mirrored Python modules."""
    lists: dict[tuple[str, ...], list[str]] = {}
    for module in (extractor, morph, core_engine):
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple, ast.Set)) or not node.elts:
                continue
            if all(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in node.elts
            ):
                key = tuple(sorted(item.value for item in node.elts))
                lists.setdefault(key, []).append(f"{path.name}:{node.lineno}")
    return lists


def _js_string_lists(probe: dict[str, Any]) -> dict[tuple[str, ...], list[str]]:
    lists: dict[tuple[str, ...], list[str]] = {}
    for literal in probe["literals"]:
        key = tuple(sorted(literal["values"]))
        lists.setdefault(key, []).append(f"engine.js:{literal['line']}")
    return lists


def test_every_python_string_list_has_a_js_twin(probe: dict[str, Any]) -> None:
    js = _js_string_lists(probe)
    missing = {
        key: places
        for key, places in _python_string_lists().items()
        if key not in js and key not in PYTHON_ONLY_LISTS
    }
    assert not missing, (
        "These lists of strings exist in core/ but not in web/engine.js. Mirror "
        "them there, or add them to PYTHON_ONLY_LISTS with the reason:\n"
        + "\n".join(f"  {places}: {_short(key)}" for key, places in missing.items())
    )


def test_every_js_string_list_has_a_python_twin(probe: dict[str, Any]) -> None:
    python = _python_string_lists()
    missing = {
        key: places
        for key, places in _js_string_lists(probe).items()
        if key not in python and key not in JS_ONLY_LISTS
    }
    assert not missing, (
        "These lists of strings exist in web/engine.js but not in core/. Mirror "
        "them there, or add them to JS_ONLY_LISTS with the reason:\n"
        + "\n".join(f"  {places}: {_short(key)}" for key, places in missing.items())
    )


def test_every_listed_exception_is_still_needed(probe: dict[str, Any]) -> None:
    python, js = _python_string_lists(), _js_string_lists(probe)
    stale = [key for key in PYTHON_ONLY_LISTS if key not in python or key in js]
    stale += [key for key in JS_ONLY_LISTS if key not in js or key in python]
    assert not stale, f"drop these from PYTHON_ONLY_LISTS/JS_ONLY_LISTS: {stale!r}"


CONSTANT = re.compile(
    r"^  const ([A-Z][A-Z0-9_]*) =(?:[ \t]*$|[ \t]*(?:\[|new Set\(|[\"'0-9]))", re.M
)


def test_every_js_table_is_compared_or_explained() -> None:
    declared = set(CONSTANT.findall(ENGINE_JS.read_text(encoding="utf-8")))
    known = {table.js for table in TABLES} | {name for name, _ in REGEXES}
    unknown = sorted(declared - known - JS_ONLY_CONSTANTS)
    assert not unknown, (
        f"New constants in web/engine.js: {unknown}. If they mirror a Python table, "
        "add them to TABLES; if they are JS-only, add them to JS_ONLY_CONSTANTS."
    )
    # The list itself must not rot: every name in it still exists.
    assert not JS_ONLY_CONSTANTS - declared, JS_ONLY_CONSTANTS - declared
