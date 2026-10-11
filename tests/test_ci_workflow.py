"""Structure of the GitHub Actions workflows that run the test suite."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    return load(ROOT / ".github" / "workflows" / "ci.yml")


def test_a_pull_request_runs_ci_once_and_pushes_are_for_main(ci) -> None:
    on = triggers(ci)
    assert on["push"] == {"branches": ["main"]}
    assert "pull_request" in on
    assert "workflow_dispatch" in on


def test_a_newer_pull_request_commit_cancels_the_older_run_but_main_never_is(ci) -> None:
    assert ci["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_pytest_step_has_node_and_requires_it(path: Path) -> None:
    """A skipped parity test must not look like a pass: Node is installed and required."""
    workflow = load(path)
    seen = 0
    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        for index, step in enumerate(steps):
            if "-m pytest" not in str(step.get("run", "")):  # not a mention of pytest.xml
                continue
            seen += 1
            where = f"{path.name}:{job_name}:{step.get('name', index)}"
            assert str(step.get("env", {}).get("FAIRPOST_REQUIRE_NODE")) == "1", where
            node_setups = [
                earlier
                for earlier in steps[:index]
                if str(earlier.get("uses", "")).startswith("actions/setup-node@")
            ]
            assert node_setups, f"{where}: no setup-node step before the tests"
            assert str(node_setups[-1]["with"]["node-version"]) == "22", where
    assert seen, f"{path.name} has no pytest step"
