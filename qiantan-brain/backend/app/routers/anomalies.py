"""异常检测 API — 把 anomaly_detector 接到产品里。

修复（审计工程改进）：anomaly_detector.py 此前在 app/ 里零引用（只被测试导入），
且集成投票阈值 bug 把最有价值的信号静默丢弃。现已修投票 bug（默认阈值 2→1），
并提供两个端点：
  - POST /api/v1/anomalies/check  算法演示（接受历史序列，返回检测报告）
  - GET  /api/v1/anomalies/scan   扫描当前商户全部 SKU 最近 N 天的销量异常

演示价值：「连续零销提醒忘记记账」「数量级录入错误」「突变模式」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_merchant
from app.database import get_db
from app.models.inventory import InventoryRecord
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.schemas.common import AnyResponse
from app.services.anomaly_detector import AnomalyDetector, quick_check


router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])


class AnomalyCheckRequest(BaseModel):
    """算法演示请求 —— 传一组历史销量 + 当前值。"""

    history: list[float] = Field(..., description="历史每日销量序列（建议≥7 个点）", min_length=1)
    current_value: float = Field(..., description="最新一天的实际值")
    product_name: str = Field("未命名商品", description="商品名（用于报告）")


@router.post("/check", response_model=AnyResponse)
async def check_anomaly(
    body: AnomalyCheckRequest,
    merchant: Merchant = Depends(get_current_merchant),
):
    """算法演示端点 —— 输入历史序列与当前值，返回异常检测报告。

    示例（连续零销 → 应检出 ZERO_SALES）：
        history=[12,10,11,13,9], current_value=0
    示例（数量级错误 → 应检出 DATA_ERROR）：
        history=[10,11,9,12,10], current_value=500
    """
    report = quick_check(body.history, body.current_value, body.product_name)
    return {
        "code": 0,
        "data": {
            "product_name": body.product_name,
            "total_signals": report.total_signals,
            "by_type": report.by_type,
            "by_severity": report.by_severity,
            "summary": report.summary,
            "signals": [
                {
                    "type": s.anomaly_type.value,
                    "severity": s.severity.value,
                    "date": s.date,
                    "product_name": s.product_name,
                    "actual_value": s.actual_value,
                    "expected_value": s.expected_value,
                    "deviation": s.deviation,
                    "detector": s.detector,
                    "details": s.details,
                    "suggestion": s.suggestion,
                }
                for s in report.signals
            ],
        },
    }


@router.get("/scan", response_model=AnyResponse)
async def scan_merchant_anomalies(
    days: int = Query(30, ge=7, le=120, description="回溯天数"),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """扫描当前商户全部商品最近 N 天的销量异常。

    从 InventoryRecord 按天聚合每个商品的销量（event_type='sale' 的 quantity），
    取最后一天作为 current_value、之前 N-1 天作为 history，逐一调用异常检测。
    返回有异常的商品列表（按信号数降序）。
    """
    cutoff = datetime.now(UTC) - timedelta(days=days + 1)

    # 按天聚合每个商品的销量（event_type='sale' 的 quantity）
    rows = (
        await db.execute(
            select(
                InventoryRecord.product_id,
                func.date(InventoryRecord.event_time).label("d"),
                func.sum(InventoryRecord.quantity).label("qty"),
            )
            .where(
                InventoryRecord.merchant_id == merchant.id,
                InventoryRecord.event_type == "sale",
                InventoryRecord.is_voided.is_(False),
                InventoryRecord.event_time >= cutoff,
            )
            .group_by(InventoryRecord.product_id, "d")
            .order_by(InventoryRecord.product_id, "d")
        )
    ).all()  # noqa: E712

    if not rows:
        return {
            "code": 0,
            "data": {"scanned": 0, "anomalies": [], "summary": "近期无销量数据"},
        }

    # 按 product_id 聚合成序列
    by_product: dict[int, list[tuple[str, float]]] = {}
    for row in rows:
        by_product.setdefault(row.product_id, []).append((str(row.d), float(row.qty or 0)))

    # 补齐缺失日期（没有销量的天记为 0）—— 连续零销才能被检出
    all_dates = sorted({d for _, seq in by_product.items() for d, _ in seq})
    for pid, seq in by_product.items():
        seq_map = dict(seq)
        by_product[pid] = [(d, seq_map.get(d, 0.0)) for d in all_dates]

    # 查商品名（product_id 关联 product_categories 表，int 主键）
    product_ids = list(by_product.keys())
    products = (
        await db.execute(
            select(ProductCategory.id, ProductCategory.name).where(
                ProductCategory.id.in_(product_ids)
            )
        )
    ).all()
    name_map: dict[int, str] = {p.id: p.name for p in products}

    detector = AnomalyDetector()
    anomalies: list[dict] = []
    scanned = 0

    for pid, seq in by_product.items():
        if len(seq) < 3:  # 数据太少跳过
            continue
        values = [v for _, v in seq]
        history = values[:-1]
        current = values[-1]
        if len(history) < 2:
            continue
        scanned += 1
        report = detector.full_report(history, current, name_map.get(pid, str(pid)))
        if report.total_signals > 0:
            anomalies.append(
                {
                    "product_id": pid,
                    "product_name": name_map.get(pid, str(pid)),
                    "current_value": current,
                    "history": history,
                    "total_signals": report.total_signals,
                    "by_type": report.by_type,
                    "by_severity": report.by_severity,
                    "summary": report.summary,
                }
            )

    # 按信号数降序
    anomalies.sort(key=lambda x: x["total_signals"], reverse=True)

    return {
        "code": 0,
        "data": {
            "scanned": scanned,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[:20],  # 最多返回 20 条
            "summary": f"扫描 {scanned} 个商品，发现 {len(anomalies)} 个异常",
        },
    }
