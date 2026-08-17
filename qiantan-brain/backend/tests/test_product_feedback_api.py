"""产品意见反馈（/api/v1/feedback）迁移回归：路径与行为不变。

原 app/routers/feedback.py 已合并进 app/routers/behavior.py 的
feedback_router（URL 仍为 POST /api/v1/feedback）。本文件锁定迁移后：
路径可达、鉴权走认证上下文、merchant_feedback 表落一行。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.feedback import MerchantFeedback


@pytest.mark.asyncio
async def test_feedback_submitted_and_persisted_after_merge(client, db_session):
    """POST /api/v1/feedback 迁移后路径不变：200 + merchant_feedback 落一行。"""
    resp = await client.post(
        "/api/v1/feedback",
        json={
            "content": "建议增加进货提醒功能",
            "page": "pages/index/index",
            "app_version": "1.2.0",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert "感谢" in body["message"]
    assert body["data"]["feedback_id"]

    async with db_session() as session:
        rows = (await session.execute(select(MerchantFeedback))).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "建议增加进货提醒功能"
    assert rows[0].page == "pages/index/index"
    assert rows[0].app_version == "1.2.0"
    assert str(rows[0].merchant_id) == TEST_MERCHANT_ID


@pytest.mark.asyncio
async def test_feedback_content_too_short_rejected(client):
    """内容过短（min_length=2）→ 422（中文摘要由全局 handler 保证）。"""
    resp = await client.post("/api/v1/feedback", json={"content": "短"})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
