"""Run ``web/engine.js`` under Node from tests, one process per batch.

Starting Node costs about 0.3 s, so a test that checks N postings must never
start N processes. ``web_check_batch`` sends every text to a single
``tests/js_runner.cjs --batch`` run and returns the results in input order;
``web_check`` is the one-text convenience over the same path.

The batch travels through a temporary file, not argv or stdin: Windows limits
the command line to about 32k characters and a long posting is several times
that once base64-encoded, and a file avoids the pipe-closing races that stdin
shows on the Windows CI runners (the same reason as ``tools/js_batch_runner.cjs``).

Import it from a sibling test module with ``import web_engine`` (pytest puts
``tests/`` on ``sys.path`` for these rootless test modules, as for ``conftest``).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
JS_RUNNER = ROOT / "tests" / "js_runner.cjs"


def node_executable() -> str:
    """Node as found on PATH; the bare name lets the OS report a clear failure."""
    return shutil.which("node") or "node"


def run_node(args: list[str]) -> str:
    """Run Node on ``args`` from the repository root and return its stdout.

    A failure raises with Node's stderr in the message: ``check=True`` alone
    would show only the exit status and hide the JavaScript error.
    """
    completed = subprocess.run(
        [node_executable(), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"node {' '.join(args[:2])} exited with {completed.returncode}: "
            f"{completed.stderr.strip()[:2000]}"
        )
    return completed.stdout


def web_check_batch(
    texts: Iterable[str], tmp_path: Path | None = None
) -> list[dict]:
    """Check every text with ``web/engine.js`` in one Node process.

    ``tmp_path`` only chooses where the temporary batch file goes; it is
    optional and the file is always removed.
    """
    items = list(texts)
    if not items:
        return []
    encoded = [base64.b64encode(text.encode("utf-8")).decode("ascii") for text in items]
    with tempfile.TemporaryDirectory(
        prefix="fairpost-web-batch-", dir=tmp_path
    ) as directory:
        batch = Path(directory) / "web-batch.json"
        batch.write_text(json.dumps(encoded), encoding="utf-8")
        stdout = run_node([str(JS_RUNNER), "--batch", str(batch)])
    results = json.loads(stdout)
    if len(results) != len(items):
        raise AssertionError(
            f"web engine returned {len(results)} results for {len(items)} texts"
        )
    return results


def web_check(text: str, tmp_path: Path | None = None) -> dict:
    """Check one text with ``web/engine.js`` (a batch of one)."""
    return web_check_batch([text], tmp_path)[0]
