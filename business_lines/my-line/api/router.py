"""my-line API Router — 插件扩展 demo

修改自 _template，3 步：
  1. 改 router 装饰器路径
  2. 加 /ping /info /demo 三个端点
  3. 启动后 API 自动挂载
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping():
    return {
        "status": "ok",
        "line": "my-line",
        "message": "插件扩展测试通过！5 分钟新增业务线",
    }


@router.get("/info")
async def info():
    return {
        "line_id": "my-line",
        "display_name": "我的测试业务线",
        "owner": "you@example.com",
        "indicator_count": 3,
    }


@router.get("/demo")
async def demo():
    return {
        "line_id": "my-line",
        "data": [
            {"month": "2026-01", "hello_kpi": 95.2, "my_count": 1200, "my_revenue": 320.5},
            {"month": "2026-02", "hello_kpi": 96.8, "my_count": 1450, "my_revenue": 410.2},
            {"month": "2026-03", "hello_kpi": 98.1, "my_count": 1620, "my_revenue": 455.0},
        ],
    }
