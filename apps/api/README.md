# apps/api

Fin BP Portal API.

## Quick start

```bash
cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

Then `GET http://localhost:8000/api/registry/lines` should return
`{"lines": [], "version": "..."}` when no business lines are registered.

## Tests

```bash
pytest -q
```

## Layout

```
app/
  main.py            FastAPI entrypoint
  core/              Settings, registry loader
  routers/           Generic + dynamic-discovery routers
  schemas/           Pydantic v2 models
  db/                SQLAlchemy 2.0 async engine
tests/               pytest
```
