"""main.py 全局异常处理器回归：500 可观测性 + 422 中文摘要。

此前未注册 Exception handler，500 时 uvicorn 日志抓不到任何 traceback，
线上问题无法定位；本文件锁定：
1. 未处理异常 → 500 统一文案 + logger.exception 完整堆栈（带 request_id）。
2. pydantic 422 → 中文摘要（missing → 缺少字段X），状态码保持 422。
"""

from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_unhandled_exception_returns_500_with_unified_detail_and_logs_traceback(caplog):
    """受控异常 → 500 统一文案，且 app.main logger 记录完整堆栈与 request_id。"""

    boom = APIRouter()

    @boom.get("/api/v1/__boom__")
    async def _boom():
        raise RuntimeError("受控异常：验证 500 日志可观测性")

    routes_before = len(app.router.routes)
    app.include_router(boom)
    try:
        with caplog.at_level(logging.ERROR, logger="app.main"):
            # raise_app_exceptions=False：让 ASGI 应用内再抛出的异常被吞掉，
            # 只取 ServerErrorMiddleware 已发送的 500 响应（对齐 uvicorn 行为）。
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/v1/__boom__", headers={"X-Request-ID": "req-boom-001"}
                )
    finally:
        del app.router.routes[routes_before:]

    assert resp.status_code == 500
    assert resp.json()["detail"] == "服务器开小差了，请稍后再试"

    error_records = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "/api/v1/__boom__" in r.getMessage()
    ]
    assert error_records, "500 必须产生 ERROR 日志（此前 uvicorn 日志 0 条 traceback）"
    record = error_records[0]
    assert record.name == "app.main"
    assert record.exc_info is not None, "必须用 logger.exception 记录完整堆栈"
    assert record.exc_info[0] is RuntimeError
    assert "受控异常：验证 500 日志可观测性" in str(record.exc_info[1])
    assert getattr(record, "request_id", "") == "req-boom-001"


@pytest.mark.asyncio
async def test_validation_error_missing_field_returns_chinese_summary(client):
    """缺字段 422 → 中文摘要「缺少字段content」，状态码保持 422。"""
    resp = await client.post("/api/v1/feedback", json={})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "缺少字段content"


@pytest.mark.asyncio
async def test_validation_error_value_error_keeps_original_msg(client):
    """自定义校验错误（value_error 类）→ 摘要保留原 msg，状态码 422。

    组合支付 credit 无客户名：CreateSaleOrderRequest 的 model_validator
    产生 value_error 类型错误。
    """
    resp = await client.post(
        "/api/v1/pos/orders",
        json={
            "items": [{"product_id": 1, "quantity": 1, "unit": "斤", "unit_price": 3.5}],
            "payments": [{"method": "credit", "amount": 3.5}],
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # 摘要是面向用户的字符串，不再是 pydantic 错误数组
    assert isinstance(detail, str)
    assert "客户" in detail or "customer" in detail.lower()


@pytest.mark.asyncio
async def test_validation_error_bad_date_returns_chinese_format_hint(client):
    """非法日期 422 → 中文格式提示，而非英文技术文案透传。

    pydantic 对 ?date=2026-13-45 抛 date_parsing / date_from_datetime 类错误，
    msg 为 'Input should be a valid date...'，此前会原样透传给小程序用户。
    """
    resp = await client.get("/api/v1/reports/daily?date=2026-13-45")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert detail.startswith("date格式不正确")
    assert "Input should be" not in detail
