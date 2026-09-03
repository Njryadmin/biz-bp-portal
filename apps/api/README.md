# apps/api

Fin BP Portal API。

## 快速开始

```bash
cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

然后访问 `GET http://localhost:8000/api/registry/lines`，
未注册任何业务线时应返回 `{"lines": [], "version": "..."}`。

## 测试

```bash
pytest -q
```

## 目录结构

```
app/
  main.py            FastAPI 入口
  core/              配置、注册表加载器
  routers/           通用路由 + 动态发现路由
  schemas/           Pydantic v2 模型
  db/                SQLAlchemy 2.0 async 引擎
tests/               pytest
```
