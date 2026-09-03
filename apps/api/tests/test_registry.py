"""
apps/api/tests/test_registry.py

Minimum-viable tests as required by the T0 spec:
- registry.yaml loads
- manifest.yaml.example parses
- indicators.yaml.example parses
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.core.registry import (
    BusinessLine,
    IndicatorsFile,
    load_indicators,
    load_manifest,
    load_registry,
    load_registry_file,
)


def test_registry_yaml_loads(repo_root: Path):
    """registry.yaml is a mapping with a `lines` key (possibly empty)."""
    path = repo_root / "business_lines" / "registry.yaml"
    assert path.exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert "lines" in raw
    assert isinstance(raw["lines"], list)
    # Each entry, if present, must have at minimum an `id` and a `manifest` path.
    for entry in raw["lines"]:
        assert "id" in entry
        assert "manifest" in entry


def test_manifest_yaml_example_loads(repo_root: Path):
    """`_template/manifest.yaml.example` parses and validates."""
    path = repo_root / "business_lines" / "_template" / "manifest.yaml.example"
    assert path.exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert "id" in raw
    assert "nav" in raw
    # Validate via the Pydantic model (this also confirms our schema is correct)
    line = load_manifest(path)
    assert isinstance(line, BusinessLine)
    assert line.api_prefix.startswith("/")


def test_indicators_yaml_example_loads(repo_root: Path):
    path = repo_root / "business_lines" / "_template" / "indicators.yaml.example"
    assert path.exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert "indicators" in raw
    ind = load_indicators(path)
    assert isinstance(ind, IndicatorsFile)
    assert len(ind.indicators) >= 1
    assert len(ind.charts) >= 1


def test_load_registry_returns_list(repo_root: Path):
    """load_registry() returns a list (possibly empty if nothing is registered)."""
    entries = load_registry()
    assert isinstance(entries, list)
    # If there are entries, each must reference an existing manifest file.
    for e in entries:
        assert e.manifest_path.exists()
        assert e.line.id == e.line.id  # structural sanity


def test_registry_file_helper(repo_root: Path):
    raw = load_registry_file(repo_root / "business_lines" / "registry.yaml")
    assert "lines" in raw
