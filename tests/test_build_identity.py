from __future__ import annotations

import ast
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
import json

from mcp_server.build_identity import (
    DEPLOYMENT_CONFIG_POLICY,
    DEPLOYMENT_EXCLUSION_POLICY,
    RUNTIME_SOURCE_FILES,
    _canonical_python_source,
    runtime_source_fingerprint,
    runtime_source_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = ("api", "cli", "core", "mcp_server")
# Vercel function, console scripts in pyproject.toml, and the local launcher.
RUNTIME_ENTRYPOINTS = (
    "api.index",
    "cli.main",
    "mcp_server.server",
    "mcp_server.local_runtime",
)


def test_runtime_source_manifest_covers_engine_dependencies() -> None:
    assert {
        "core/engine.py",
        "core/extractor.py",
        "core/loader.py",
        "core/morph.py",
        "core/organization_guidance.py",
        "core/review_packet.py",
        "core/schema.py",
    } <= set(RUNTIME_SOURCE_FILES)
    assert "mcp_server/assisted_review.py" in RUNTIME_SOURCE_FILES
    assert "data/guidance/organization-applicability.yaml" in RUNTIME_SOURCE_FILES


def _module_source(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] not in RUNTIME_PACKAGES:
        return None
    base = ROOT.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(ROOT).as_posix()
    return None


def _imported_modules(relative: str) -> set[str]:
    path = PurePosixPath(relative)
    package = list(path.parent.parts)
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            modules.add(module)
            # ``from package import name`` may import a submodule.
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def _module_chain(module: str) -> list[str]:
    # Importing a.b.c runs every package __init__ on the way down.
    parts = module.split(".")
    sources = (
        _module_source(".".join(parts[:depth])) for depth in range(1, len(parts) + 1)
    )
    return [source for source in sources if source is not None]


def _runtime_import_closure() -> set[str]:
    pending = [
        source for module in RUNTIME_ENTRYPOINTS for source in _module_chain(module)
    ]
    seen: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in seen:
            continue
        seen.add(relative)
        for module in _imported_modules(relative):
            pending.extend(
                source for source in _module_chain(module) if source not in seen
            )
    return seen


def test_runtime_source_files_cover_runtime_import_closure() -> None:
    closure = _runtime_import_closure()

    assert {
        "api/__init__.py",
        "cli/__init__.py",
        "core/review_packet.py",
        "mcp_server/storage.py",
    } <= closure
    assert sorted(closure - set(RUNTIME_SOURCE_FILES)) == []


def test_runtime_source_files_exist_in_checkout() -> None:
    manifest = runtime_source_manifest(root=ROOT)

    assert sorted(path for path, digest in manifest.items() if digest == "missing") == []


def _excluded_by_vercelignore(relative: str, pattern: str) -> bool:
    parts = PurePosixPath(relative).parts
    if pattern.endswith("/"):
        directory = pattern.rstrip("/")
        if "/" in directory:
            return relative.startswith(f"{directory}/")
        return any(fnmatch(part, directory) for part in parts[:-1])
    if "/" in pattern:
        return fnmatch(relative, pattern)
    return any(fnmatch(part, pattern) for part in parts)


def _excluded_by_function_glob(relative: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return fnmatch(relative.split("/", 1)[0], pattern[: -len("/**")]) and (
            "/" in relative
        )
    return fnmatch(relative, pattern) or fnmatch(
        PurePosixPath(relative).name, pattern
    )


def test_runtime_source_files_are_shipped_to_the_vercel_function() -> None:
    ignore_patterns = [
        line.strip()
        for line in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and not line.lstrip().startswith("!")
    ]
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    exclude_files = config["functions"]["api/index.py"]["excludeFiles"]
    function_globs = exclude_files.strip("{}").split(",")

    # Guard the helpers against vacuous matching.
    assert _excluded_by_vercelignore("tools/x.py", "tools/")
    assert _excluded_by_vercelignore("core/__pycache__/x.pyc", "__pycache__/")
    assert _excluded_by_vercelignore("run.bat", "*.bat")
    assert _excluded_by_function_glob("tests/x.py", "tests/**")

    shipped_elsewhere = {
        relative: [
            pattern
            for pattern in ignore_patterns
            if _excluded_by_vercelignore(relative, pattern)
        ]
        + [
            pattern
            for pattern in function_globs
            if _excluded_by_function_glob(relative, pattern)
        ]
        for relative in RUNTIME_SOURCE_FILES
    }

    assert {path: hits for path, hits in shipped_elsewhere.items() if hits} == {}


def test_runtime_source_manifest_covers_deployed_web() -> None:
    assert {
        "index.html",
        "pyproject.toml",
        "web/app.js",
        "web/data.js",
        "web/engine.js",
        "web/index.html",
        "web/posting-templates.js",
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
    # The printed book QR must keep working and stay a temporary redirect: a
    # permanent (301/308) redirect would be cached by browsers and make the
    # destination impossible to change later. ``statusCode`` could smuggle in a
    # permanent status, so only these exact keys are allowed.
    for item in config["redirects"]:
        assert set(item) == {"source", "destination", "permanent"}
        assert item["permanent"] is False
    redirects = tuple(
        (item["source"], item["destination"], item["permanent"])
        for item in config["redirects"]
    )
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
    assert redirects == DEPLOYMENT_CONFIG_POLICY["redirects"]
    assert {source for source, _, _ in redirects} >= {"/book", "/book/"}
    assert all(
        destination == "/web/?entry=book" and permanent is False
        for _, destination, permanent in DEPLOYMENT_CONFIG_POLICY["redirects"]
    )
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
