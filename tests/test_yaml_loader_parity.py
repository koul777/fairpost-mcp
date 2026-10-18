from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from core import loader


ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = sorted((ROOT / "data").rglob("*.yaml"))
requires_libyaml = pytest.mark.skipif(
    not hasattr(yaml, "CSafeLoader"), reason="PyYAML is built without libyaml"
)


def _typed(value: Any) -> Any:
    """Compare order and exact types too, so 1 and True never pass as equal."""

    if isinstance(value, dict):
        return ("dict", [(_typed(key), _typed(item)) for key, item in value.items()])
    if isinstance(value, list):
        return ("list", [_typed(item) for item in value])
    return (type(value).__name__, value)


def test_loader_prefers_libyaml_when_available() -> None:
    expected = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    assert loader._SAFE_LOADER is expected


@requires_libyaml
@pytest.mark.parametrize(
    "path", DATA_FILES, ids=[path.relative_to(ROOT).as_posix() for path in DATA_FILES]
)
def test_libyaml_and_python_parsers_load_identical_values(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    python_value = yaml.load(text, Loader=yaml.SafeLoader)
    libyaml_value = yaml.load(text, Loader=yaml.CSafeLoader)

    assert _typed(libyaml_value) == _typed(python_value)


@requires_libyaml
def test_ruleset_and_versions_match_the_pure_python_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fast = loader.load_ruleset()
    monkeypatch.setattr(loader, "_SAFE_LOADER", yaml.SafeLoader)
    reference = loader.load_ruleset()

    assert fast.version == reference.version
    assert fast.matching_version == reference.matching_version
    assert _typed(list(fast.rules)) == _typed(list(reference.rules))
    assert _typed(fast.slots) == _typed(reference.slots)
    assert _typed(fast.statutes) == _typed(reference.statutes)
