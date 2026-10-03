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


@pytest.fixture(scope="session", autouse=True)
def bind_junit_to_validation_inputs(record_testsuite_property):
    from tools.release_inputs import validation_source_fingerprint

    initial = validation_source_fingerprint(ROOT)
    record_testsuite_property("fairpost_validation_source_fingerprint", initial)
    yield
    if validation_source_fingerprint(ROOT) != initial:
        pytest.fail("validation inputs changed while the test suite was running")
