from __future__ import annotations

from pathlib import Path
import json

from mcp_server.build_identity import (
    DEPLOYMENT_CONFIG_POLICY,
    DEPLOYMENT_EXCLUSION_POLICY,
    RUNTIME_SOURCE_FILES,
    _canonical_python_source,
    runtime_source_fingerprint,
    runtime_source_manifest,
)


def test_runtime_source_manifest_covers_engine_dependencies() -> None:
    assert {
        "core/engine.py",
        "core/extractor.py",
        "core/loader.py",
        "core/morph.py",
        "core/schema.py",
    } <= set(RUNTIME_SOURCE_FILES)


def test_runtime_source_manifest_covers_deployed_web() -> None:
    assert {
        "index.html",
        "pyproject.toml",
        "web/app.js",
        "web/data.js",
        "web/engine.js",
        "web/index.html",
        "web/styles.css",
    } <= set(RUNTIME_SOURCE_FILES)


def test_deployment_exclusion_policy_matches_vercelignore() -> None:
    exclusions = (Path(__file__).resolve().parents[1] / ".vercelignore").read_text(
        encoding="utf-8"
    )
    configured = {
        line.strip()
        for line in exclusions.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert set(DEPLOYMENT_EXCLUSION_POLICY) <= configured


def test_deployment_config_policy_matches_vercel_json() -> None:
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "vercel.json").read_text(
            encoding="utf-8"
        )
    )
    function = config["functions"]["api/index.py"]
    rewrites = tuple(
        (item["source"], item["destination"])
        for item in config["rewrites"]
    )
    headers = tuple(
        (item["key"], item["value"])
        for item in config["headers"][0]["headers"]
    )

    assert function["maxDuration"] == DEPLOYMENT_CONFIG_POLICY[
        "function_max_duration"
    ]
    assert function["excludeFiles"] == DEPLOYMENT_CONFIG_POLICY[
        "function_exclude_files"
    ]
    assert rewrites == DEPLOYMENT_CONFIG_POLICY["rewrites"]
    assert headers == DEPLOYMENT_CONFIG_POLICY["headers"]


def test_runtime_source_fingerprint_binds_code_and_rule_versions(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def value():\n    return 1\n", encoding="utf-8")
    canonical = _canonical_python_source(module_path)
    assert b"decorator_list=[]" in canonical
    assert b"type_params=[]" in canonical
    first = runtime_source_fingerprint(
        ruleset_version="rules-a",
        matching_version="match-a",
        root=tmp_path,
        source_files=("module.py",),
    )
    repeated = runtime_source_fingerprint(
        ruleset_version="rules-a",
        matching_version="match-a",
        root=tmp_path,
        source_files=("module.py",),
    )

    assert first == repeated
    assert first.startswith("runtime-")

    module_path.write_text("def value( ):\r\n    return 1\r\n", encoding="utf-8")
    formatting_changed = runtime_source_fingerprint(
        ruleset_version="rules-a",
        matching_version="match-a",
        root=tmp_path,
        source_files=("module.py",),
    )
    assert formatting_changed == first

    module_path.write_text("def value():\n    return 2\n", encoding="utf-8")
    code_changed = runtime_source_fingerprint(
        ruleset_version="rules-a",
        matching_version="match-a",
        root=tmp_path,
        source_files=("module.py",),
    )
    rules_changed = runtime_source_fingerprint(
        ruleset_version="rules-b",
        matching_version="match-a",
        root=tmp_path,
        source_files=("module.py",),
    )

    assert code_changed != first
    assert rules_changed != code_changed


def test_runtime_source_fingerprint_binds_vercel_entrypoint(
    tmp_path: Path,
) -> None:
    entrypoint = tmp_path / "api" / "index.py"
    entrypoint.parent.mkdir()
    entrypoint.write_text("app = object()\n", encoding="utf-8")
    first = runtime_source_fingerprint(
        ruleset_version="rules",
        matching_version="matching",
        root=tmp_path,
        source_files=("api/index.py",),
    )

    entrypoint.write_text("app = None\n", encoding="utf-8")
    changed = runtime_source_fingerprint(
        ruleset_version="rules",
        matching_version="matching",
        root=tmp_path,
        source_files=("api/index.py",),
    )

    assert changed != first


def test_runtime_source_fingerprint_normalizes_web_line_endings(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "web" / "app.js"
    asset.parent.mkdir()
    asset.write_bytes(b"const value = 1;\nexport { value };\n")
    first = runtime_source_fingerprint(
        ruleset_version="rules",
        matching_version="matching",
        root=tmp_path,
        source_files=("web/app.js",),
    )

    asset.write_bytes(b"const value = 1;\r\nexport { value };\r\n")
    crlf = runtime_source_fingerprint(
        ruleset_version="rules",
        matching_version="matching",
        root=tmp_path,
        source_files=("web/app.js",),
    )
    assert crlf == first

    asset.write_bytes(b"const value = 2;\r\nexport { value };\r\n")
    changed = runtime_source_fingerprint(
        ruleset_version="rules",
        matching_version="matching",
        root=tmp_path,
        source_files=("web/app.js",),
    )
    assert changed != first


def test_runtime_source_manifest_uses_canonical_hashes_and_missing_markers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "web" / "app.js"
    source.parent.mkdir()
    source.write_bytes(b"const value = 1;\r\n")
    first = runtime_source_manifest(
        root=tmp_path,
        source_files=("web/app.js", "web/missing.js"),
    )

    source.write_bytes(b"const value = 1;\n")
    repeated = runtime_source_manifest(
        root=tmp_path,
        source_files=("web/app.js", "web/missing.js"),
    )

    assert repeated == first
    assert first["web/app.js"].startswith("sha256:")
    assert first["web/missing.js"] == "missing"
