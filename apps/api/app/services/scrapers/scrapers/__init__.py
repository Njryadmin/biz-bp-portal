"""
apps/api/app/services/scrapers/scrapers/__init__.py

Marker package; concrete scrapers live in this directory. Each module
is expected to call :func:`register` at import time. The
``registry.discover_scrapers`` function walks this package and imports
every non-``_``-prefixed module.
"""
