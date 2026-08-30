from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE_FILES = (
    "index.html",
    "pyproject.toml",
    "vercel.json",
    "api/index.py",
    "core/__init__.py",
    "core/engine.py",
    "core/extractor.py",
    "core/loader.py",
    "core/morph.py",
    "core/schema.py",
    "mcp_server/__init__.py",
    "mcp_server/build_identity.py",
    "mcp_server/remote.py",
    "mcp_server/server.py",
    "mcp_server/storage.py",
    "web/app.js",
    "web/data.js",
    "web/engine.js",
    "web/index.html",
    "web/styles.css",
)


def _canonical_python_source(path: Path) -> bytes:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    # Keep one canonical AST shape across every supported Python boundary.
    # Python 3.12 added empty ``type_params`` fields, while Python 3.14 began
    # hiding empty fields from ast.dump() unless show_empty is requested.
    if sys.version_info < (3, 12):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                node._fields = (*node._fields, "type_params")
                node.type_params = []
    options = {"annotate_fields": True, "include_attributes": False}
    if sys.version_info >= (3, 14):
        options["show_empty"] = True
    return ast.dump(tree, **options).encode("utf-8")


def _canonical_text_source(path: Path) -> bytes:
    """Keep deploy fingerprints stable across Git LF/CRLF checkouts."""

    return (
        path.read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .encode("utf-8")
    )


def _resolve_source_path(
    root: Path,
    relative: str,
    *,
    discover_ancestors: bool,
) -> Path:
    candidates = [root / relative]
    if discover_ancestors:
        candidates.extend(parent / relative for parent in root.parents)
    return next((path for path in candidates if path.is_file()), candidates[0])


def runtime_source_fingerprint(
    *,
    ruleset_version: str,
    matching_version: str,
    root: Path | None = None,
    source_files: Iterable[str] = RUNTIME_SOURCE_FILES,
) -> str:
    """Hash deployable code plus the already content-derived rule versions."""

    active_root = ROOT if root is None else root
    discover_ancestors = root is None
    digest = hashlib.sha256(b"fairpost-runtime-source-v1\0")
    for relative in sorted(source_files):
        path = _resolve_source_path(
            active_root,
            relative,
            discover_ancestors=discover_ancestors,
        )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            payload = (
                _canonical_python_source(path)
                if path.suffix == ".py"
                else _canonical_text_source(path)
            )
            digest.update(len(payload).to_bytes(8, byteorder="big", signed=False))
            digest.update(payload)
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    digest.update(ruleset_version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(matching_version.encode("utf-8"))
    return f"runtime-{digest.hexdigest()}"
