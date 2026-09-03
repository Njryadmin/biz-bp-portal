"""
apps/api/app/core/registry.py

Loads `business_lines/registry.yaml` and parses each referenced manifest
and indicators file. Caches the result for the process lifetime.

The dynamic loading of business line ROUTERS is handled by
`app.routers.registry` — this module only deals with metadata.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from .config import get_settings


# ---------- Pydantic schemas (mirror packages/types/src/index.ts) ----------


class BusinessLineNavItem(BaseModel):
    path: str
    title: str


class BusinessLineWarehouse(BaseModel):
    # Pydantic reserves `model_*` attributes; we use the safe field name
    # `schema_name` internally and serialize it as `schema` so the YAML
    # contract with business_lines/<line>/manifest.yaml is unchanged.
    model_config = {"populate_by_name": True}

    schema_name: str = Field(alias="schema")
    dbt_schema: str
    mart_schema: str


class BusinessLineRefresh(BaseModel):
    schedule: str
    enabled: bool = True


class BusinessLineFeatures(BaseModel):
    universal_kpi: bool = True
    universal_chart: bool = True
    ag_grid: bool = True


class BusinessLine(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    owner: str = ""
    icon: str = "AppstoreOutlined"
    nav: list[BusinessLineNavItem] = Field(default_factory=list)
    api_prefix: str
    warehouse: BusinessLineWarehouse
    refresh: BusinessLineRefresh = Field(default_factory=lambda: BusinessLineRefresh(schedule="0 2 * * *", enabled=True))
    features: BusinessLineFeatures = Field(default_factory=BusinessLineFeatures)

    @field_validator("api_prefix")
    @classmethod
    def _api_prefix_must_start_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"api_prefix must start with '/', got: {v}")
        return v


class Indicator(BaseModel):
    id: str
    title: str
    unit: str = ""
    format: str = "number"
    aggregation: str = "sum"
    source: str = ""
    description: str = ""


class ChartSpec(BaseModel):
    id: str
    title: str
    type: str = "line"
    x: str = "date"
    y: list[str] = Field(default_factory=list)
    source: str = ""
    description: str = ""


class IndicatorsFile(BaseModel):
    indicators: list[Indicator] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)


# ---------- Registry DTOs ----------


@dataclass
class RegistryEntry:
    """One business line as loaded from registry.yaml + manifest + indicators."""

    line: BusinessLine
    manifest_path: Path
    indicators: list[Indicator] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    indicators_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.line.id,
            "manifest": str(self.manifest_path),
            "indicators_path": str(self.indicators_path) if self.indicators_path else None,
        }


# ---------- Loader ----------


def _resolve_root() -> Path:
    """Find the monorepo root.

    The `business_lines` directory is the canonical marker. We walk up from
    CWD and from this file's location until we find it.
    """
    env_root = os.environ.get("BIZ_BP_PROJECT_ROOT")
    candidates: list[Path] = []
    if env_root:
        candidates.append(Path(env_root))
    candidates.append(Path.cwd())
    # Walk up from this file
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        candidates.append(p)

    for c in candidates:
        try:
            bl = c / "business_lines"
            if (bl / "registry.yaml").exists():
                return c
        except OSError:
            continue
    # Fallback: cwd
    return Path.cwd()


@lru_cache(maxsize=1)
def get_project_root() -> Path:
    return _resolve_root()


def load_registry_file(registry_path: str | Path | None = None) -> dict[str, Any]:
    """Read & parse registry.yaml. Returns the raw dict."""
    settings = get_settings()
    root = get_project_root()
    path = Path(registry_path) if registry_path else root / settings.registry_path
    if not path.exists():
        return {"lines": []}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"registry.yaml root must be a mapping, got {type(data)}")
    data.setdefault("lines", [])
    return data


def load_manifest(manifest_path: str | Path) -> BusinessLine:
    """Load and validate a single manifest file."""
    p = Path(manifest_path)
    if not p.exists():
        raise FileNotFoundError(f"manifest not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return BusinessLine.model_validate(raw)


def load_indicators(indicators_path: str | Path) -> IndicatorsFile:
    """Load and validate an indicators.yaml. Returns empty file if missing."""
    p = Path(indicators_path)
    if not p.exists():
        return IndicatorsFile()
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return IndicatorsFile.model_validate(raw)


def load_registry(registry_path: str | Path | None = None) -> list[RegistryEntry]:
    """Load the full registry: registry.yaml + each manifest + each indicators."""
    raw = load_registry_file(registry_path)
    root = get_project_root()
    entries: list[RegistryEntry] = []
    for item in raw.get("lines", []):
        if not isinstance(item, dict):
            continue
        line_id = item.get("id")
        manifest_rel = item.get("manifest") or f"business_lines/{line_id}/manifest.yaml"
        manifest_path = (root / manifest_rel).resolve()
        line = load_manifest(manifest_path)
        # Cross-check id vs manifest id
        if line.id != line_id:
            raise ValueError(
                f"registry id '{line_id}' does not match manifest id '{line.id}' in {manifest_path}"
            )
        # Optional indicators.yaml next to manifest
        ind_path = manifest_path.parent / "indicators.yaml"
        ind_file = load_indicators(ind_path)
        entries.append(
            RegistryEntry(
                line=line,
                manifest_path=manifest_path,
                indicators=ind_file.indicators,
                charts=ind_file.charts,
                indicators_path=ind_path if ind_path.exists() else None,
            )
        )
    return entries


def registry_version() -> str:
    """Return a hash-like version derived from the registry file content."""
    import hashlib

    settings = get_settings()
    root = get_project_root()
    p = root / settings.registry_path
    if not p.exists():
        return "0.0.0"
    h = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
    return f"0.1.{h}"
