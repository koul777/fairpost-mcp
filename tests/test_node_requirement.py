from __future__ import annotations

from pathlib import Path
import re

import pytest

import conftest


ROOT = Path(__file__).resolve().parents[1]


class MarkedItem:
    def __init__(self, marked: bool) -> None:
        self.marked = marked

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self.marked and name == "requires_node" else None


def test_missing_node_skips_by_default(monkeypatch) -> None:
    monkeypatch.setattr(conftest.shutil, "which", lambda name: None)
    monkeypatch.delenv(conftest.REQUIRE_NODE_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        conftest.pytest_runtest_setup(MarkedItem(True))


def test_missing_node_fails_when_ci_requires_it(monkeypatch) -> None:
    monkeypatch.setattr(conftest.shutil, "which", lambda name: None)
    monkeypatch.setenv(conftest.REQUIRE_NODE_ENV, "1")

    with pytest.raises(pytest.fail.Exception, match="FAIRPOST_REQUIRE_NODE=1"):
        conftest.pytest_runtest_setup(MarkedItem(True))


def test_node_requirement_ignores_unmarked_tests_and_available_node(
    monkeypatch,
) -> None:
    monkeypatch.setenv(conftest.REQUIRE_NODE_ENV, "1")
    monkeypatch.setattr(conftest.shutil, "which", lambda name: None)
    conftest.pytest_runtest_setup(MarkedItem(False))

    monkeypatch.setattr(conftest.shutil, "which", lambda name: "/usr/bin/node")
    conftest.pytest_runtest_setup(MarkedItem(True))


def test_node_tests_use_the_required_marker_instead_of_silent_skips() -> None:
    silent_skip = re.compile(r"skipif\(\s*shutil\.which\(\s*[\"']node[\"']")
    offenders = [
        path.name
        for path in sorted((ROOT / "tests").glob("test_*.py"))
        if silent_skip.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_every_ci_pytest_job_requires_node() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    jobs = re.split(r"\n  (?=[A-Za-z0-9_-]+:\n)", workflow.split("\njobs:\n", 1)[1])
    pytest_jobs = [job for job in jobs if "python -m pytest" in job]

    assert len(pytest_jobs) == 3
    for job in pytest_jobs:
        assert "actions/setup-node@" in job
        assert 'FAIRPOST_REQUIRE_NODE: "1"' in job
