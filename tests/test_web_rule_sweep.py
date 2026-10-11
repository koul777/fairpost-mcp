"""Sweep every rule pattern and exclusion term through both engines at once.

The hand-written parity cases cover the rules someone thought of. This sweep is
derived from the loaded ruleset instead, so a pattern or exclusion added later is
exercised without anyone writing a case for it:

* every literal pattern, and for every ``re:`` pattern the strings that can be
  derived from its parse tree (alternatives, optional parts, character classes);
  a sample counts only if the engine's own matcher accepts it;
* every exclusion term next to such a sample (before, after, adjacent), and at
  and just beyond its window, with filler made of astral characters so a window
  measured in UTF-16 units instead of code points would disagree;
* every context-group pattern, with and without the group;
* every slot pattern under the section heading the slot searches.

All inputs go through ``core.FairpostEngine`` and, in one Node process, through
``web/engine.js``; the full results must be equal. The inputs are built from
rule text only, no postings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from re import _constants as sre_constants
from re import _parser as sre_parser

import pytest

from core import FairpostEngine, load_ruleset
from core.morph import find_first
from web_engine import web_check_batch

pytestmark = pytest.mark.requires_node

# Characters used where a regex needs "any" or "not this".
FILLER_CHARACTERS = ("가", "a", "1", "!", "나")
MAX_SAMPLES_PER_NODE = 6
MAX_SAMPLES_PER_PATTERN = 3
# An astral filler: one code point, two UTF-16 code units.
ASTRAL_FILLER = "\U0001f600"

# Samples for rules whose regex the generator cannot invert (look-aheads that
# all constrain the same text). The key is the rule ID.
HAND_SAMPLES: dict[str, list[str]] = {
    "Q-INFO-012": ["근무분야\n행정직, 연구직"],
}


# --- samples from regular expressions -------------------------------------


def _class_contains(members: list, character: str) -> bool:
    word = character.isalnum() or character == "_"
    for op, value in members:
        if op is sre_constants.LITERAL and chr(value) == character:
            return True
        if op is sre_constants.RANGE and value[0] <= ord(character) <= value[1]:
            return True
        if op is sre_constants.CATEGORY and (
            (value is sre_constants.CATEGORY_DIGIT and character.isdigit())
            or (value is sre_constants.CATEGORY_NOT_DIGIT and not character.isdigit())
            or (value is sre_constants.CATEGORY_SPACE and character.isspace())
            or (value is sre_constants.CATEGORY_NOT_SPACE and not character.isspace())
            or (value is sre_constants.CATEGORY_WORD and word)
            or (value is sre_constants.CATEGORY_NOT_WORD and not word)
        ):
            return True
    return False


def _class_samples(items: list) -> list[str]:
    negated = any(op is sre_constants.NEGATE for op, _ in items)
    members = [(op, value) for op, value in items if op is not sre_constants.NEGATE]
    if negated:
        return [next(c for c in FILLER_CHARACTERS if not _class_contains(members, c))]
    categories = {
        sre_constants.CATEGORY_DIGIT: "1",
        sre_constants.CATEGORY_SPACE: " ",
        sre_constants.CATEGORY_WORD: "a",
        sre_constants.CATEGORY_NOT_DIGIT: "가",
        sre_constants.CATEGORY_NOT_SPACE: "가",
        sre_constants.CATEGORY_NOT_WORD: "!",
    }
    found: list[str] = []
    for op, value in members:
        if op is sre_constants.LITERAL:
            found.append(chr(value))
        elif op is sre_constants.RANGE:
            found.append(chr(value[0]))
        elif op is sre_constants.CATEGORY and value in categories:
            found.append(categories[value])
    return found[:MAX_SAMPLES_PER_NODE]


# A fragment is (lead, text, trail): text is consumed by the pattern, while a
# positive look-behind adds to lead and a positive look-ahead to trail.
Fragment = tuple[str, str, str]


def _sequence(nodes: list) -> list[Fragment]:
    results: list[Fragment] = [("", "", "")]
    for op, value in nodes:
        options = _node(op, value)
        if not options:
            return []
        results = [
            (a[0] + b[0], a[1] + b[1], b[2] + a[2]) for a in results for b in options
        ][:MAX_SAMPLES_PER_NODE]
    return results


def _node(op, value) -> list[Fragment]:
    if op is sre_constants.LITERAL:
        return [("", chr(value), "")]
    if op is sre_constants.NOT_LITERAL:
        return [("", next(c for c in FILLER_CHARACTERS if c != chr(value)), "")]
    if op is sre_constants.ANY:
        return [("", "가", "")]
    if op is sre_constants.IN:
        return [("", character, "") for character in _class_samples(value)]
    if op is sre_constants.BRANCH:
        options: list[Fragment] = []
        for alternative in value[1]:
            options.extend(_sequence(list(alternative)))
        return options[: MAX_SAMPLES_PER_NODE * 2]
    if op is sre_constants.SUBPATTERN:
        return _sequence(list(value[3]))
    if op is sre_constants.ATOMIC_GROUP:
        return _sequence(list(value))
    if op in (
        sre_constants.MAX_REPEAT,
        sre_constants.MIN_REPEAT,
        sre_constants.POSSESSIVE_REPEAT,
    ):
        low, high, inner_nodes = value
        inner = _sequence(list(inner_nodes))
        if not inner:
            return [("", "", "")] if low == 0 else []
        counts = sorted({low, max(low, 1)} if high >= 1 else {low})
        return [("", item[1] * count, "") for count in counts for item in inner[:2]]
    if op is sre_constants.AT or op is sre_constants.ASSERT_NOT:
        return [("", "", "")]
    if op is sre_constants.ASSERT:
        direction, inner_nodes = value
        inner = _sequence(list(inner_nodes))
        if not inner:
            return []
        return [("", "", inner[0][1])] if direction > 0 else [(inner[0][1], "", "")]
    return []  # back-references and anything unforeseen: no sample


def samples(pattern: str) -> list[str]:
    """Strings the engine's own matcher accepts for a rule ``pattern``."""
    if not pattern.startswith("re:"):
        return [pattern]
    tree = sre_parser.parse(pattern[3:], re.IGNORECASE)
    found: list[str] = []
    for lead, text, trail in _sequence(list(tree)):
        candidate = lead + text + trail
        if candidate not in found and find_first(candidate, [pattern]) is not None:
            found.append(candidate)
    return found[:MAX_SAMPLES_PER_PATTERN]


# --- the cases ----------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    label: str
    text: str = field(repr=False)
    rule_id: str | None = None  # the rule this case is built around
    # pattern | in-section | exclusion-near | exclusion-edge | context | context-edge | slot
    kind: str = "pattern"


def _rule_samples(rule: dict) -> list[tuple[str, str]]:
    """(label, sample) for every pattern of a presence rule, hand-written last."""
    found: list[tuple[str, str]] = []
    for index, pattern in enumerate(rule["trigger"]["patterns"]):
        for position, sample in enumerate(samples(pattern)):
            found.append((f"pattern[{index}].{position}", sample))
    for position, sample in enumerate(HAND_SAMPLES.get(rule["id"], [])):
        found.append((f"hand[{position}]", sample))
    return found


def _first_sample(patterns: list[str]) -> str | None:
    for pattern in patterns:
        for sample in samples(pattern):
            return sample
    return None


def build_cases() -> list[Case]:
    ruleset = load_ruleset()
    cases: list[Case] = []
    for rule in ruleset.rules:
        trigger = rule["trigger"]
        rule_id = rule["id"]
        if trigger["type"] != "presence":
            continue
        own = _rule_samples(rule)
        for name, sample in own:
            cases.append(Case(f"{rule_id}:{name}", sample, rule_id))
            cases.append(
                Case(f"{rule_id}:{name}:in-section", f"자격요건\n{sample}\n", rule_id, "in-section")
            )
        base = own[0][1] if own else None

        for index, exclusion in enumerate(trigger.get("exclude", [])):
            candidate = exclusion.get("candidate")
            anchor = next(
                (s for _, s in own if candidate is None or find_first(s, [candidate]) is not None),
                None,
            )
            terms = samples(exclusion["term"])
            if anchor is None or not terms:
                continue
            term = terms[0]
            tag = f"{rule_id}:exclude[{index}]"
            near = {
                "before": f"{term} {anchor}",
                "after": f"{anchor} {term}",
                "adjacent-before": f"{term}{anchor}",
                "adjacent-after": f"{anchor}{term}",
            }
            for name, text in near.items():
                cases.append(Case(f"{tag}:{name}", text, rule_id, "exclusion-near"))
            # The term lies wholly inside the window with `inside` filler code
            # points between it and the candidate, and sticks out with one more.
            inside = int(exclusion.get("window", 0)) - len(term)
            if inside >= 0:
                for label, count in (("in", inside), ("out", inside + 1)):
                    gap = ASTRAL_FILLER * count
                    for side, text in (
                        ("before", f"{term}{gap}{anchor}"),
                        ("after", f"{anchor}{gap}{term}"),
                    ):
                        cases.append(
                            Case(f"{tag}:{side}-{label}", text, rule_id, "exclusion-edge")
                        )

        groups = trigger.get("context_groups") or {}
        if groups and base is not None:
            group_samples = {name: _first_sample(g["patterns"]) for name, g in groups.items()}
            if all(group_samples.values()):
                present = [group_samples[name] for name in groups]
                cases.append(
                    Case(f"{rule_id}:context:all", "\n".join([*present, base]), rule_id, "context")
                )
                for omitted in groups:
                    kept = [group_samples[name] for name in groups if name != omitted]
                    cases.append(
                        Case(
                            f"{rule_id}:context:without-{omitted}",
                            "\n".join([*kept, base]),
                            rule_id,
                            "context",
                        )
                    )
                # One group at a time sits exactly at the edge of its window,
                # wholly inside it or one code point beyond; the other groups
                # stay next to the candidate on the opposite side.
                for name, group in groups.items():
                    own_sample = group_samples[name]
                    inside = group["window"] - len(own_sample)
                    if inside < 0:
                        continue
                    others = "\n".join(
                        group_samples[other] for other in groups if other != name
                    )
                    for label, count in (("in", inside), ("out", inside + 1)):
                        gap = ASTRAL_FILLER * count
                        for side, text in (
                            ("before", f"{own_sample}{gap}{base}\n{others}"),
                            ("after", f"{others}\n{base}{gap}{own_sample}"),
                        ):
                            cases.append(
                                Case(
                                    f"{rule_id}:context:{name}-{side}-{label}",
                                    text,
                                    rule_id,
                                    "context-edge",
                                )
                            )

    for slot_id, slot in sorted(ruleset.slots.items()):
        heading = slot["search_sections"][0]
        slot_patterns = list(slot["accept_patterns"])
        for component in slot.get("components", []):
            slot_patterns.extend(component["patterns"])
        for index, pattern in enumerate(slot_patterns):
            for position, sample in enumerate(samples(pattern)):
                cases.append(
                    Case(
                        f"slot:{slot_id}:pattern[{index}].{position}",
                        f"{heading}\n{sample}\n",
                        None,
                        "slot",
                    )
                )
    labels = [case.label for case in cases]
    assert len(set(labels)) == len(labels), "case labels must be unique"
    return cases


@pytest.fixture(scope="module")
def sweep() -> tuple[list[Case], list[dict], list[dict]]:
    cases = build_cases()
    texts = [case.text for case in cases]
    engine = FairpostEngine()
    python = [engine.check(text).to_dict() for text in texts]
    web = web_check_batch(texts)
    return cases, python, web


def _fired(result: dict) -> set[str]:
    return {item["id"] for item in result["findings"]} | {
        item["id"] for item in result["questions"]
    }


def test_python_and_web_agree_on_every_rule_input(sweep) -> None:
    cases, python, web = sweep
    differing = [
        case.label for case, py, js in zip(cases, python, web, strict=True) if py != js
    ]
    assert not differing, (
        f"{len(differing)} of {len(cases)} sweep inputs differ between the engines; "
        f"first: {differing[:15]}"
    )


# The checks below look only at Python results. They keep the sweep honest: if
# the inputs stopped reaching the rules, equal results would prove nothing.


def test_every_presence_rule_fires_on_one_of_its_own_patterns(sweep) -> None:
    cases, python, _ = sweep
    presence = {
        rule["id"] for rule in load_ruleset().rules if rule["trigger"]["type"] == "presence"
    }
    # A rule with context groups needs them as well: its "context:all" input.
    fired = {
        case.rule_id
        for case, result in zip(cases, python, strict=True)
        if (case.kind == "pattern" or case.label.endswith(":context:all"))
        and case.rule_id in _fired(result)
    }
    assert not presence - fired, (
        f"no sample fires {sorted(presence - fired)}; add a HAND_SAMPLES entry "
        "or improve the sample generator"
    )


def test_most_patterns_yield_a_sample(sweep) -> None:
    cases, _, _ = sweep
    patterns = sum(
        len(rule["trigger"]["patterns"])
        for rule in load_ruleset().rules
        if rule["trigger"]["type"] == "presence"
    )
    sampled = {
        case.label.rsplit(".", 1)[0]
        for case in cases
        if case.kind == "pattern" and ":pattern[" in case.label
    }
    assert len(sampled) >= 0.95 * patterns, f"samples for {len(sampled)} of {patterns} patterns"


def test_exclusion_terms_suppress_their_rule_at_the_window_edge(sweep) -> None:
    cases, python, _ = sweep
    fired = {case.label: _fired(result) for case, result in zip(cases, python, strict=True)}

    suppressing = [
        case
        for case in cases
        if case.kind == "exclusion-near"
        and case.label.endswith(":before")
        and case.rule_id not in fired[case.label]
    ]
    nearby = [c for c in cases if c.kind == "exclusion-near" and c.label.endswith(":before")]
    assert len(suppressing) >= 0.6 * len(nearby), (
        f"only {len(suppressing)} of {len(nearby)} adjacent exclusion terms suppressed their rule"
    )
    inside_ok = outside_ok = checked = 0
    for case in suppressing:
        stem = case.label.removesuffix(":before")
        if f"{stem}:before-in" not in fired:
            continue
        checked += 1
        inside_ok += case.rule_id not in fired[f"{stem}:before-in"]
        outside_ok += case.rule_id in fired[f"{stem}:before-out"]
    assert checked >= 20, checked
    # Not every exclusion is a clean probe (a term that contains the pattern
    # itself stays a candidate outside the window; a rule with context groups
    # does not fire on its anchor alone), so most, not all, must behave.
    assert inside_ok >= 0.9 * checked, f"{inside_ok} of {checked} suppress inside the window"
    assert outside_ok >= 0.75 * checked, f"{outside_ok} of {checked} release outside the window"


def test_sweep_size_is_in_the_expected_range(sweep) -> None:
    cases, _, _ = sweep
    assert 1000 <= len(cases) <= 4000, len(cases)


def test_context_groups_fire_inside_their_window_and_not_beyond(sweep) -> None:
    cases, python, _ = sweep
    fired = {case.label: _fired(result) for case, result in zip(cases, python, strict=True)}
    # The group before the candidate is a clean probe. After the candidate, a
    # pattern that keeps matching to the end of its line moves the window edge.
    edge = [c for c in cases if c.kind == "context-edge" and "-before-" in c.label]
    inside = [c for c in edge if c.label.endswith("-in")]
    outside = [c for c in edge if c.label.endswith("-out")]
    assert len(inside) >= 3 and len(inside) == len(outside), (len(inside), len(outside))
    assert all(c.rule_id in fired[c.label] for c in inside), "a group inside its window is not satisfied"
    assert all(c.rule_id not in fired[c.label] for c in outside), (
        "a group one code point beyond its window is still satisfied"
    )
