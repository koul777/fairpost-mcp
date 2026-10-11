"""The per-test environment scrub in conftest.py keeps ambient config out of tests."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

import conftest

# Names an ambient shell or CI runner might export. None may reach a test.
POLLUTANTS = {
    "FAIRPOST_MCP_TOKEN": "ambient-not-a-secret",
    "FAIRPOST_DATA_DIR": "ambient-data-dir",
    "VERCEL": "1",
    "VERCEL_ENV": "production",
    "UPSTASH_REDIS_REST_URL": "ambient-url",
    "KV_REST_API_TOKEN": "ambient-not-a-secret",
    "LAW_OPEN_API_OC": "ambient-not-a-secret",
    "ANTHROPIC_API_KEY": "ambient-not-a-secret",
    "OPENAI_API_KEY": "ambient-not-a-secret",
    "GEMINI_API_KEY": "ambient-not-a-secret",
    "GOOGLE_API_KEY": "ambient-not-a-secret",
}
KEPT = {
    conftest.REQUIRE_NODE_ENV: "1",
    "PYTHONIOENCODING": "utf-8",
}


@pytest.fixture(scope="module", autouse=True)
def ambient_environment():
    """Pollute the process before each test's scrub runs, as a dirty shell would.

    A module-scoped fixture is set up before the function-scoped autouse scrub,
    so the test body sees what the scrub left. The previous values are put back
    afterwards so no other module observes the pollution.
    """
    previous = {name: os.environ.get(name) for name in {**POLLUTANTS, **KEPT}}
    os.environ.update(POLLUTANTS)
    os.environ.update(KEPT)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_ambient_names_are_removed_before_the_test_body() -> None:
    assert not set(POLLUTANTS) & set(os.environ)
    assert conftest.ambient_env_names(os.environ) == []


def test_harness_settings_survive_the_scrub() -> None:
    for name, value in KEPT.items():
        assert os.environ[name] == value


def test_subprocess_inherits_the_scrubbed_environment() -> None:
    probe = (
        "import json, os; "
        "print(json.dumps(sorted(n for n in os.environ if n in "
        f"{sorted(POLLUTANTS)!r})))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.stdout.strip() == "[]"
    # The copy-at-call-time idiom used by the MCP protocol tests is covered too.
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    assert not set(POLLUTANTS) & set(environment)


def test_a_test_can_set_a_name_back_for_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAIRPOST_MCP_TOKEN", "set-by-the-test")
    assert os.environ["FAIRPOST_MCP_TOKEN"] == "set-by-the-test"


def test_an_override_in_one_test_does_not_leak_into_the_next() -> None:
    # Runs after the test above: its setenv was undone, and the scrub runs again.
    assert "FAIRPOST_MCP_TOKEN" not in os.environ


@pytest.mark.parametrize(
    ("name", "scrubbed"),
    [
        ("FAIRPOST_ANYTHING", True),
        ("fairpost_lowercase", True),
        ("VERCEL_GIT_COMMIT_SHA", True),
        ("UPSTASH_REDIS_REST_TOKEN", True),
        ("KV_URL", True),
        ("LAW_OPEN_API_OC", True),
        ("ANTHROPIC_API_KEY", True),
        ("GOOGLE_API_KEY", True),
        (conftest.REQUIRE_NODE_ENV, False),
        ("PYTHONIOENCODING", False),
        ("PATH", False),
        ("KVM_DEVICE", False),
        ("MY_FAIRPOST_NOTE", False),
    ],
)
def test_scrub_rules(name: str, scrubbed: bool) -> None:
    selected = conftest.ambient_env_names({name: "x"})
    assert (selected == [name]) is scrubbed
