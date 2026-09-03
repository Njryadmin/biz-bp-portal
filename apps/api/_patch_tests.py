"""
One-shot migration script: update existing tests to use the
``app_with_auth`` fixture from conftest.py.

For each target test file we:
  1. Remove the `from app.main import create_app` import (it is no
     longer needed because the fixture provides the app).
  2. Remove the `app = create_app()` and `with TestClient(app) as
     client:` lines.
  3. Add `client_with_auth` as a parameter to every test function in
     the file.

The Python interpreter is happy with the over-indented body left
behind by step 2 (extra 4 spaces are harmless), so we don't try to
re-indent the body. The test functions look like:

    def test_http_rules_endpoint():
        # (was: app = create_app() / with TestClient(app) as client:)
        r = client_with_auth.get("/api/alerts/rules/residential")
        ...

This is safe to re-run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent / "tests"
TARGETS = [
    "test_alerts.py",
    "test_forecast.py",
    "test_scrapers.py",
    "test_sensitivity.py",
    "test_llm_backends.py",
]


def patch_file(path: Path) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    n1 = n2 = n3 = 0

    # 1) Remove the create_app import (the fixture provides the app).
    new_text, n1 = re.subn(
        r"^from app\.main import create_app\s*\n",
        "",
        text,
        flags=re.MULTILINE,
    )
    # 2) Remove ``app = create_app()`` line.
    new_text, n2 = re.subn(
        r"^[ \t]*app\s*=\s*create_app\(\)\s*\n",
        "",
        new_text,
        flags=re.MULTILINE,
    )
    # 3) Remove ``with TestClient(app) as client:`` line.
    new_text, n3 = re.subn(
        r"^[ \t]*with\s+TestClient\(app\)\s+as\s+client:\s*\n",
        "",
        new_text,
        flags=re.MULTILINE,
    )
    # 4) Replace ``client.`` references with ``client_with_auth.``.
    #    We use a word-boundary pattern so we don't accidentally
    #    rewrite the parameter name ``client_with_auth`` itself.
    new_text, n4 = re.subn(
        r"\bclient\.",
        "client_with_auth.",
        new_text,
    )

    # 5) Add the fixture parameter to test_* functions that don't
    #    already have it. We look for ``def test_XYZ(...):`` and
    #    insert ``client_with_auth`` into the parameter list.
    def add_fixture(match: re.Match[str]) -> str:
        header = match.group(0)
        if "client_with_auth" in header:
            return header
        # ``def test_X():`` → ``def test_X(client_with_auth):``
        # ``def test_X(a, b):`` → ``def test_X(a, b, client_with_auth):``
        params = match.group(1)
        if params.strip() == "":
            return f"def test_{match.group(2)}(client_with_auth):"
        return f"def test_{match.group(2)}({params}, client_with_auth):"

    new_text, n5 = re.subn(
        r"^def (test_(\w+))\(([^)]*)\):",
        lambda m: (
            m.group(0) if "client_with_auth" in m.group(0)
            else f"def {m.group(1)}({m.group(3) + (', ' if m.group(3).strip() else '')}client_with_auth):"
        ),
        new_text,
        flags=re.MULTILINE,
    )

    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return n1 + n2 + n3, n4 + n5


def main() -> None:
    for name in TARGETS:
        p = TESTS_DIR / name
        if not p.exists():
            print(f"  skip {name} (not found)")
            continue
        n_boilerplate, n_client = patch_file(p)
        print(f"  {name}: {n_boilerplate} boilerplate line(s), "
              f"{n_client} client reference(s) updated")


if __name__ == "__main__":
    main()
