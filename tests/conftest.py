from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Child processes inherit this and emit UTF-8 on stdout/stderr regardless of the
# host console code page, so the Korean CLI messages the tests assert on survive
# on Windows exactly as they do on the Linux CI runner. Only stream encoding is
# forced; file I/O defaults stay untouched so missing encoding= stays a real bug.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# CI sets this so Python/JS parity tests can never pass by silently skipping.
REQUIRE_NODE_ENV = "FAIRPOST_REQUIRE_NODE"

# Ambient configuration that would change a test's result: a developer shell or
# a CI runner that happens to export a token, a deployment marker or a provider
# key must not leak into the code under test. Every test starts without them.
# Names match case-insensitively, as on Windows. REQUIRE_NODE_ENV is the one
# FAIRPOST_* setting that is test-harness policy rather than product config.
AMBIENT_ENV_PREFIXES = ("FAIRPOST_", "VERCEL", "UPSTASH_", "KV_")
AMBIENT_ENV_NAMES = frozenset(
    {
        "LAW_OPEN_API_OC",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
)
AMBIENT_ENV_KEEP = frozenset({REQUIRE_NODE_ENV})


def ambient_env_names(environ) -> list[str]:
    """Names in ``environ`` that the per-test scrub removes (sorted)."""
    return sorted(
        name
        for name in environ
        if name.upper() not in AMBIENT_ENV_KEEP
        and (
            name.upper() in AMBIENT_ENV_NAMES
            or name.upper().startswith(AMBIENT_ENV_PREFIXES)
        )
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_node: needs Node.js; skipped without node unless "
        f"{REQUIRE_NODE_ENV}=1, which turns the skip into a failure",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("requires_node") is None:
        return
    if shutil.which("node") is not None:
        return
    if os.environ.get(REQUIRE_NODE_ENV) == "1":
        pytest.fail(
            f"Node.js가 필요합니다: {REQUIRE_NODE_ENV}=1인데 PATH에 node가 없습니다",
            pytrace=False,
        )
    pytest.skip("Node.js가 필요합니다")


@pytest.fixture(autouse=True)
def scrub_ambient_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test without ambient FAIRPOST_*, Vercel, KV and provider keys.

    ``monkeypatch.delenv`` edits ``os.environ`` itself, so a subprocess that
    copies ``os.environ`` when it is called (``{**os.environ, ...}``, the
    default of ``subprocess.run``) gets the scrubbed environment too. A test
    that needs one of these names sets it with ``monkeypatch.setenv`` or passes
    an explicit ``env=``; the original values come back after each test.
    """
    for name in ambient_env_names(os.environ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session", autouse=True)
def bind_junit_to_validation_inputs(record_testsuite_property):
    from tools.release_inputs import validation_source_fingerprint

    initial = validation_source_fingerprint(ROOT)
    record_testsuite_property("fairpost_validation_source_fingerprint", initial)
    yield
    if validation_source_fingerprint(ROOT) != initial:
        pytest.fail("validation inputs changed while the test suite was running")
