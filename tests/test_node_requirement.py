from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

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


def test_every_ci_pytest_step_requires_node() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    pytest_jobs = {
        name: job
        for name, job in workflow["jobs"].items()
        if any("python -m pytest" in step.get("run", "") for step in job["steps"])
    }

    assert set(pytest_jobs) == {"test", "test-python-latest", "test-windows"}
    for job in pytest_jobs.values():
        uses = [step.get("uses", "") for step in job["steps"]]
        node_index = next(
            index for index, used in enumerate(uses) if used.startswith("actions/setup-node@")
        )
        for index, step in enumerate(job["steps"]):
            if "python -m pytest" in step.get("run", ""):
                assert index > node_index
                env = {**job.get("env", {}), **step.get("env", {})}
                assert env.get("FAIRPOST_REQUIRE_NODE") == "1"
