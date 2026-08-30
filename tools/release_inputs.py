from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_INPUT_DIRS = (
    "api",
    "cli",
    "core",
    "data",
    "mcp_server",
    "tests",
    "tools",
    "web",
)
VALIDATION_ROOT_FILES = (
    "pyproject.toml",
    ".mcp.json",
    "MANIFEST.in",
    "vercel.json",
)


def validation_inputs(root: Path = ROOT) -> list[Path]:
    inputs = [root / name for name in VALIDATION_ROOT_FILES if (root / name).is_file()]
    for directory in VALIDATION_INPUT_DIRS:
        source_dir = root / directory
        if not source_dir.is_dir():
            continue
        inputs.extend(
            path
            for path in source_dir.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return sorted(inputs)


def validation_source_fingerprint(root: Path = ROOT) -> str:
    digest = hashlib.sha256(b"fairpost-validation-inputs-v1\0")
    for path in validation_inputs(root):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, byteorder="big", signed=False))
        digest.update(payload)
        digest.update(b"\0")
    return f"validation-{digest.hexdigest()}"
