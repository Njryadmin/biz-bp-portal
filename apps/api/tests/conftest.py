"""
apps/api/tests/conftest.py

Pytest config. Ensures CWD is the monorepo root so that the registry loader
finds `business_lines/registry.yaml`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Repo root = two levels up from this file (apps/api/tests -> repo root)
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_TESTS_DIR = _HERE
_API_DIR = _HERE.parents[1]
_APP_DIR = _API_DIR / "app"


def _ensure_on_path() -> None:
    for p in (str(_REPO_ROOT), str(_API_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)


@pytest.fixture(scope="session", autouse=True)
def _setup_paths():
    _ensure_on_path()
    # Tell the registry loader where the project root is.
    os.environ["FIN_BP_PROJECT_ROOT"] = str(_REPO_ROOT)
    yield


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT
