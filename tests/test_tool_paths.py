"""tools/_paths.py and the tools that share it: holdout refusal and output aliasing.

Every corpus, audit and report tool used to carry its own copy of these checks,
and the copies disagreed: three accepted ``private-holdout/records.jsonl``
because they compared whole path components, one never looked at the resolved
path, and the alias test differed from tool to tool. The matrices below run the
same hostile paths against ``tools._paths`` and against each tool that uses it.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
from typing import Callable

import pytest

from tools._paths import (
    atomic_write_text,
    holdout_components,
    paths_alias,
    reject_holdout_path,
)

# --- reject_holdout_path -----------------------------------------------------

HOLDOUT_NAMES = [
    "holdout",
    "Holdout",
    "HOLDOUT",
    "private-holdout",
    "holdout-2026",
    "sealed_HoldOut_set",
    "holdout.d",
]
CLEAN_NAMES = ["train", "hold-out", "hold_out", "heldout", "hold", "out"]


@pytest.mark.parametrize("name", HOLDOUT_NAMES)
def test_holdout_directory_is_refused_in_any_case_or_affix(name: str, tmp_path: Path) -> None:
    path = tmp_path / name / "records.jsonl"
    assert holdout_components(path)
    with pytest.raises(ValueError, match="홀드아웃"):
        reject_holdout_path(path)


@pytest.mark.parametrize("name", HOLDOUT_NAMES)
def test_holdout_filename_is_refused(name: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        reject_holdout_path(tmp_path / "train" / f"{name}.jsonl")


@pytest.mark.parametrize("name", CLEAN_NAMES)
def test_other_names_are_not_holdout(name: str, tmp_path: Path) -> None:
    reject_holdout_path(tmp_path / name / "records.jsonl")
    assert holdout_components(tmp_path / name / "records.jsonl") == []


def test_the_caller_chooses_the_message(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="my own message"):
        reject_holdout_path(tmp_path / "holdout", "my own message")


def test_a_path_that_does_not_exist_is_judged_by_its_name(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir" / "Holdout" / "x.jsonl"
    assert not missing.parent.exists()
    with pytest.raises(ValueError):
        reject_holdout_path(missing)


def test_dot_dot_cannot_hide_a_holdout_directory(tmp_path: Path) -> None:
    (tmp_path / "train").mkdir()
    # The supplied spelling names holdout even though it resolves to train.
    with pytest.raises(ValueError):
        reject_holdout_path(tmp_path / "holdout" / ".." / "train" / "x.jsonl")


def _symlink_or_skip(link: Path, target: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError, AttributeError) as error:
        pytest.skip(f"symbolic links are not available here: {error}")


def _directory_link(link: Path, target: Path) -> None:
    """A symlink to a directory; on Windows without the privilege, a junction.

    A junction is the same kind of reparse point to the path resolver, and any
    user may create one, so the resolved-path checks run on every platform.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("symbolic links are not available here")
    import _winapi  # Windows only

    _winapi.CreateJunction(str(target), str(link))


def test_symlink_into_a_holdout_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "sealed-holdout"
    real.mkdir()
    (real / "records.jsonl").write_text("{}\n", encoding="utf-8")
    link = tmp_path / "harmless-name"
    _directory_link(link, real)
    path = link / "records.jsonl"
    assert holdout_components(path), "the resolved path leads into the holdout directory"
    with pytest.raises(ValueError):
        reject_holdout_path(path)


# --- paths_alias -------------------------------------------------------------


def _file(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_same_path_is_an_alias(tmp_path: Path) -> None:
    source = _file(tmp_path / "in.json")
    assert paths_alias(source, source)


def test_another_spelling_of_the_same_path_is_an_alias(tmp_path: Path) -> None:
    source = _file(tmp_path / "a" / "in.json")
    assert paths_alias(source, tmp_path / "a" / ".." / "a" / "in.json")


def test_a_path_differing_only_in_case_is_an_alias(tmp_path: Path) -> None:
    source = _file(tmp_path / "in.json")
    assert paths_alias(source, tmp_path / "IN.JSON")
    assert paths_alias(tmp_path / "new.json", tmp_path / "NEW.json")  # neither exists yet


def test_a_hard_link_is_an_alias(tmp_path: Path) -> None:
    source = _file(tmp_path / "in.json")
    link = tmp_path / "out.json"
    try:
        os.link(source, link)
    except OSError as error:  # pragma: no cover - file systems without hard links
        pytest.skip(f"hard links are not available here: {error}")
    assert paths_alias(source, link)


def test_a_symlink_to_the_input_is_an_alias(tmp_path: Path) -> None:
    source = _file(tmp_path / "in.json")
    link = tmp_path / "out.json"
    _symlink_or_skip(link, source, directory=False)
    assert paths_alias(source, link)


def test_different_files_are_not_aliases(tmp_path: Path) -> None:
    first, second = _file(tmp_path / "one.json"), _file(tmp_path / "two.json")
    assert not paths_alias(first, second)
    assert not paths_alias(first, tmp_path / "three.json")  # one missing
    assert not paths_alias(tmp_path / "x.json", tmp_path / "y.json")  # both missing


# --- atomic_write_text -------------------------------------------------------


def test_atomic_write_creates_parents_and_keeps_bytes_exact(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "report.json"
    atomic_write_text(target, "한글\r\n줄\n")
    assert target.read_bytes() == "한글\r\n줄\n".encode("utf-8")  # no newline translation


def test_atomic_write_replaces_the_previous_content(tmp_path: Path) -> None:
    target = _file(tmp_path / "report.json", "old")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


def test_a_failed_atomic_write_keeps_the_old_file_and_leaves_no_debris(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _file(tmp_path / "report.json", "previous\n")

    def fail(_source: object, _target: object) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="simulated publish failure"):
        atomic_write_text(target, "replacement\n")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "previous\n"
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


# --- the tools ----------------------------------------------------------------


def _tool(name: str):
    return importlib.import_module(f"tools.{name}")


def _reads(module: str, function: str) -> Callable[[Path], object]:
    return lambda path: getattr(_tool(module), function)(path)


# Each entry rejects a path before opening it. Tools that also demand a train
# component get a path that has one, so only "holdout" can be the reason.
HOLDOUT_GUARDS: dict[str, Callable[[Path], object]] = {
    "analyze_corpus": _reads("analyze_corpus", "load_training_records"),
    "mine_candidates": _reads("mine_candidates", "read_jsonl"),
    "verify_web_parity": _reads("verify_web_parity", "_load_training_records"),
    "audit_question_relevance": _reads("audit_question_relevance", "load_training_records"),
    "benchmark_engine": _reads("benchmark_engine", "validate_train_input"),
    "audit_private_fairness": _reads("audit_private_fairness", "_validate_train_path"),
    "audit_private_fairness (non-evaluation)": lambda path: _tool(
        "audit_private_fairness"
    )._validate_non_evaluation_path(path, argument="--output"),
    "build_private_monitoring_snapshot": lambda path: _tool(
        "build_private_monitoring_snapshot"
    ).reject_non_train_path(path, argument="--input"),
    "check_private_fairness_drift": lambda path: _tool(
        "check_private_fairness_drift"
    ).reject_disallowed_path(path, argument="--current"),
    "summarize_private_review": lambda path: _tool(
        "summarize_private_review"
    )._reject_forbidden_path(path, argument="--input"),
    "build_private_review_queue": lambda path: _tool(
        "build_private_review_queue"
    ).validate_paths(path, path.with_name("queue-out.jsonl")),
}


@pytest.mark.parametrize("tool", HOLDOUT_GUARDS)
@pytest.mark.parametrize("name", HOLDOUT_NAMES)
def test_every_train_only_tool_refuses_any_holdout_directory(
    tool: str, name: str, tmp_path: Path
) -> None:
    # "train" is present too, as in .corpus/private-holdout/train/records.jsonl.
    path = tmp_path / name / "train" / "records.jsonl"
    with pytest.raises(ValueError):
        HOLDOUT_GUARDS[tool](path)


@pytest.mark.parametrize(
    "tool",
    [
        "benchmark_engine",
        "audit_private_fairness",
        "build_private_monitoring_snapshot",
        "check_private_fairness_drift",
        "summarize_private_review",
        "build_private_review_queue",
    ],
)
def test_the_guards_still_accept_an_ordinary_train_path(tool: str, tmp_path: Path) -> None:
    path = _file(tmp_path / "corpus" / "train" / "records.jsonl")
    HOLDOUT_GUARDS[tool](path)  # must not raise


# Output validators: (input, output) -> raises ValueError if output would overwrite input.
def _validators() -> dict[str, Callable[[Path, Path], object]]:
    return {
        "audit_corpus_diversity": lambda i, o: _tool("audit_corpus_diversity").validate_output_path(i, o),
        "audit_question_relevance": lambda i, o: _tool("audit_question_relevance").validate_output_path(i, o),
        "benchmark_engine": lambda i, o: _tool("benchmark_engine").validate_output_path(i, o),
        "summarize_pilot_feedback": lambda i, o: _tool("summarize_pilot_feedback").validate_output_path(i, o),
        "build_annotation_ui": lambda i, o: _tool("build_annotation_ui").validate_paths(
            i, i.with_name("manifest.json"), o
        ),
        "build_human_labeling_handoff": lambda i, o: _tool("build_human_labeling_handoff").validate_paths(
            i, i.with_name("m.json"), i.with_name("l.html"), i.with_name("a.jsonl"), o
        ),
        "build_release_report": lambda i, o: _tool("build_release_report").validate_paths(i, o),
        "evaluate": lambda i, o: _tool("evaluate").validate_output_paths(
            output=o, receipt=None, inputs={"--records": i}
        ),
        "verify_distribution": lambda i, o: _tool("verify_distribution")._validate_paths(
            i, i.with_name("package.whl"), o
        ),
        "check_private_fairness_drift": lambda i, o: _tool("check_private_fairness_drift").validate_paths(
            i, i.with_name("baseline.json"), o
        ),
        "build_private_review_queue": lambda i, o: _tool("build_private_review_queue").validate_paths(i, o),
        "summarize_private_review": lambda i, o: _tool("summarize_private_review").validate_paths(i, o),
    }


VALIDATORS = _validators()


def _alias_of(kind: str, source: Path) -> Path:
    if kind == "same":
        return source
    if kind == "dot-dot":
        return source.parent / ".." / source.parent.name / source.name
    if kind == "case":
        return source.with_name(source.name.upper())
    if kind == "hard-link":
        link = source.with_name("hard-link.jsonl")
        try:
            os.link(source, link)
        except OSError as error:  # pragma: no cover
            pytest.skip(f"hard links are not available here: {error}")
        return link
    if kind == "symlink":
        link = source.with_name("sym-link.jsonl")
        _symlink_or_skip(link, source, directory=False)
        return link
    if kind == "linked-directory":
        # The output is spelled through a link to the input's directory.
        link = source.parent.parent / "linked-train"
        _directory_link(link, source.parent)
        return link / source.name
    raise AssertionError(kind)


@pytest.mark.parametrize("tool", VALIDATORS)
@pytest.mark.parametrize(
    "kind", ["same", "dot-dot", "case", "hard-link", "symlink", "linked-directory"]
)
def test_every_output_validator_refuses_to_overwrite_its_input(
    tool: str, kind: str, tmp_path: Path
) -> None:
    source = _file(tmp_path / "corpus" / "train" / "in.jsonl")
    with pytest.raises(ValueError):
        VALIDATORS[tool](source, _alias_of(kind, source))


@pytest.mark.parametrize("tool", VALIDATORS)
def test_every_output_validator_accepts_a_different_file(tool: str, tmp_path: Path) -> None:
    source = _file(tmp_path / "corpus" / "train" / "in.jsonl")
    VALIDATORS[tool](source, tmp_path / "reports" / "out.json")  # must not raise


def test_evidence_audit_output_cannot_replace_another_report(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    other = _file(reports / "corpus_diversity_audit.json")
    module = _tool("check_evidence_versions")
    for output in (other, reports / ".." / "reports" / "corpus_diversity_audit.json"):
        with pytest.raises(ValueError):
            module.validate_output_path(reports, output)
    module.validate_output_path(reports, reports / "evidence_version_audit.json")


def test_tools_write_reports_through_the_shared_atomic_writer() -> None:
    # A copy of the writer in a tool would escape the failure-handling above.
    for name in (
        "audit_corpus_diversity",
        "audit_question_relevance",
        "benchmark_engine",
        "build_annotation_ui",
        "build_human_labeling_handoff",
        "build_release_report",
        "check_evidence_versions",
        "evaluate",
        "refresh_evidence",
        "summarize_pilot_feedback",
        "verify_distribution",
    ):
        assert _tool(name).atomic_write_text is atomic_write_text, name
