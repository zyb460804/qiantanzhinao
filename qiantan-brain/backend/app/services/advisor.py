"""
Business advice generation engine.
Produces the three-line explainable recommendation format.
"""

import logging
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.timezone import local_days_ago, utc_now
from app.models.ai_action import AIAction
from app.models.environment import EnvironmentRecord
from app.models.inventory import InventoryRecord
from app.models.product import ProductCategory
from app.models.recommendation import Recommendation
from app.services.behavior import get_merchant_profile, personalize_recommendation
from app.services.env_engine import EnvFactors, estimate_demand
from app.services.forecast import predict_demand
from app.services.sku_service import resolve_sku_id


logger = logging.getLogger(__name__)


def generate_daily_advice(
    product_name: str,
    current_inventory_qty: float,
    moving_avg_7d: float,
    moving_avg_30d: float,
    max_historical_daily: float,
    env_factors: EnvFactors,
    forecast_result: dict | None = None,
) -> dict:
    """
    Generate a three-line explainable business recommendation.

    Returns dict with suggestion, basis list, and risk warning.
    """
    # Estimate demand with environment factors
    demand = estimate_demand(
        product_name=product_name,
        moving_avg_7d=moving_avg_7d,
        moving_avg_30d=moving_avg_30d,
        max_historical_daily=max_historical_daily,
        env_factors=env_factors,
    )

    predicted_qty = demand["predicted_qty"]
    coefs = demand["coefficients"]

    # --- Online forecast override (Prophet / moving-average from forecast service) ---
    forecast_model = None
    forecast_bounds = None
    if forecast_result is not None:
        fc_qty = forecast_result.get("predicted_qty")
        if fc_qty is not None:
            predicted_qty = fc_qty
            forecast_model = forecast_result.get("model")
            forecast_bounds = {
                "lower": forecast_result.get("lower_bound"),
                "upper": forecast_result.get("upper_bound"),
            }

    # Build basis list
    basis = []

    # ① 7-day average
    basis.append(
        {
            "factor": "近7日平均销量",
            "value": f"{coefs['moving_avg_7d']}斤",
            "impact": "+",
        }
    )

    # ② Weekend
    if coefs["weekend"] > 1.0:
        basis.append(
            {
                "factor": "周末客流",
                "value": f"预计增加{(coefs['weekend'] - 1) * 100:.0f}%",
                "impact": "+",
            }
        )
    elif coefs["weekend"] < 1.0:
        basis.append(
            {
                "factor": "工作日",
                "value": f"较周末减少{(1 - coefs['weekend']) * 100:.0f}%",
                "impact": "-",
            }
        )

    # ③ Temperature
    if coefs["temperature"] != 1.0:
        direction = "+" if coefs["temperature"] > 1.0 else "-"
        basis.append(
            {
                "factor": f"气温影响({env_factors.temp_high}°C)",
                "value": (
                    f"{'增加' if direction == '+' else '减少'}"
                    f"{abs(coefs['temperature'] - 1) * 100:.0f}%"
                ),
                "impact": direction,
            }
        )

    # ④ Rain
    if coefs["rainfall"] < 1.0:
        basis.append(
            {
                "factor": f"降雨概率({env_factors.rainfall_prob}%)",
                "value": f"预计减少{(1 - coefs['rainfall']) * 100:.0f}%",
                "impact": "-",
            }
        )

    # ⑤ Inventory
    days_of_inventory = current_inventory_qty / moving_avg_7d if moving_avg_7d > 0 else 0
    if days_of_inventory < 1:
        basis.append(
            {
                "factor": "当前库存",
                "value": "不足1天销量",
                "impact": "+",
            }
        )
    elif days_of_inventory > 3:
        basis.append(
            {
                "factor": "当前库存",
                "value": f"充足(可销{days_of_inventory:.0f}天)",
                "impact": "-",
            }
        )

    # ⑥ Holiday
    if coefs["holiday"] != 1.0:
        direction = "+" if coefs["holiday"] > 1.0 else "-"
        basis.append(
            {
                "factor": "节假日影响",
                "value": (
                    f"{'增加' if direction == '+' else '减少'}"
                    f"{abs(coefs['holiday'] - 1) * 100:.0f}%"
                ),
                "impact": direction,
            }
        )

    # Calculate recommended purchase quantity
    recommended_qty = max(0, predicted_qty - current_inventory_qty)
    recommended_qty = round(recommended_qty, 1)

    # Generate suggestion text
    if recommended_qty > 0:
        suggestion = f"建议明日采购{product_name}{recommended_qty}斤"
    else:
        suggestion = f"{product_name}库存充足，明日无需进货"

    # Generate risk warning
    warnings = []
    if env_factors.rainfall_prob and env_factors.rainfall_prob > 50:
        adjusted = round(recommended_qty * 0.7, 1)
        warnings.append(f"若明日降雨概率超过50%，建议减少至{adjusted}斤")
    if env_factors.temp_high and env_factors.temp_high > 35:
        warnings.append(f"高温天气({env_factors.temp_high}°C)，请注意{product_name}保鲜")
    if predicted_qty > max_historical_daily * 1.2:
        warnings.append("预测值偏高，请注意控制风险")

    risk_warning = "；".join(warnings) if warnings else None

    # Confidence — rule-based estimate
    confidence = 0.78  # Base confidence for rule engine
    if forecast_model is not None and forecast_result is not None:
        confidence = forecast_result.get("confidence", confidence)

    return {
        "product_name": product_name,
        "suggestion": suggestion,
        "basis": basis,
        "risk_warning": risk_warning,
        "recommended_qty": recommended_qty,
        "confidence": confidence,
        "predicted_qty": predicted_qty,
        "forecast": {
            "model": forecast_model,
            "lower_bound": forecast_bounds["lower"] if forecast_bounds else None,
            "upper_bound": forecast_bounds["upper"] if forecast_bounds else None,
        }
        if forecast_model
        else None,
    }


async def build_daily_advice(db: AsyncSession, merchant_id: uuid.UUID) -> dict:
    """编排「每日建议」的数据抓取与生成，供路由层薄封装调用。

    把原先写在 router 里的 ~80 行查询与循环搬到这里，
    路由只负责「取依赖 → 调本函数 → 包信封返回」。
    这样单测可直接打本函数、不依赖 HTTP 层，也顺带灭掉了 N+1 的
    可读性问题（查询仍逐商品发起，后续可在服务层统一聚合）。
    """
    today = date.today()

    # 1. 当前环境
    env_row = (
        await db.execute(select(EnvironmentRecord).where(EnvironmentRecord.date == today))
    ).scalar_one_or_none()

    env_factors = EnvFactors(
        date=today,
        temp_high=float(env_row.temp_high) if env_row and env_row.temp_high else 25,
        temp_low=float(env_row.temp_low) if env_row and env_row.temp_low else 18,
        rainfall_prob=float(env_row.rainfall_prob) if env_row and env_row.rainfall_prob else 20,
        is_holiday=env_row.is_holiday if env_row else False,
        holiday_name=env_row.holiday_name if env_row else None,
        is_weekend=env_row.is_weekend if env_row else (today.weekday() >= 5),
        day_of_week=today.weekday(),
    )

    # 2. 在售商品
    products = (
        (await db.execute(select(ProductCategory).where(ProductCategory.is_active))).scalars().all()
    )

    if not products:
        return {"recommendations": [], "message": "暂无商品品类数据"}

    # 3. 商户行为画像（个性化）
    profile = await get_merchant_profile(db, merchant_id)

    # 4. 逐商品计算库存 / 销量 / 建议
    recommendations: list[dict] = []
    saved_rec_ids: list[Recommendation] = []
    for product in products:
        pid = product.id
        # 解析本商户主 SKU（category:sku 当前一对一）
        sku_id = await resolve_sku_id(db, merchant_id, product_id=pid)

        # 查询过滤：以 product_id 为基，若已绑定 SKU 则同时纳入该 SKU 的无 SKU 历史数据。
        base_filters = [
            InventoryRecord.merchant_id == merchant_id,
            InventoryRecord.product_id == pid,
        ]
        sku_filter: ColumnElement[bool]
        if sku_id is not None:
            sku_filter = (InventoryRecord.sku_id == sku_id) | (InventoryRecord.sku_id.is_(None))
        else:
            sku_filter = InventoryRecord.sku_id.is_(None)

        # 当前库存：全部数量变动求和
        current_qty = float(
            (
                await db.execute(
                    select(func.sum(InventoryRecord.quantity)).where(*base_filters, sku_filter)
                )
            ).scalar()
            or 0
        )

        # 7 日 / 30 日销量均值
        seven_days_ago = local_days_ago(7)
        total_sales_7d = float(
            (
                await db.execute(
                    select(func.sum(func.abs(InventoryRecord.quantity))).where(
                        *base_filters,
                        sku_filter,
                        InventoryRecord.event_type == "sale",
                        InventoryRecord.event_time >= seven_days_ago,
                    )
                )
            ).scalar()
            or 0
        )
        moving_avg_7d = round(total_sales_7d / 7, 1)

        thirty_days_ago = local_days_ago(30)
        total_sales_30d = float(
            (
                await db.execute(
                    select(func.sum(func.abs(InventoryRecord.quantity))).where(
                        *base_filters,
                        sku_filter,
                        InventoryRecord.event_type == "sale",
                        InventoryRecord.event_time >= thirty_days_ago,
                    )
                )
            ).scalar()
            or 0
        )
        moving_avg_30d = round(total_sales_30d / 30, 1)

        max_daily = max(moving_avg_7d * 1.5, moving_avg_30d * 1.5, 10)

        # 在线预测（Prophet，失败自动回退规则引擎）
        try:
            forecast_result = await predict_demand(db, merchant_id, pid, sku_id=sku_id)
        except Exception as fe:
            logger.warning("forecast.predict_demand failed, using rule engine: %s", fe)
            forecast_result = None

        advice = generate_daily_advice(
            product_name=product.name,
            current_inventory_qty=current_qty,
            moving_avg_7d=moving_avg_7d,
            moving_avg_30d=moving_avg_30d,
            max_historical_daily=max_daily,
            env_factors=env_factors,
            forecast_result=forecast_result,
        )
        advice["product_id"] = pid
        advice["sku_id"] = str(sku_id) if sku_id else None

        # 基于行为画像个性化
        raw_qty = advice.get("recommended_qty", 0)
        personalized_qty = personalize_recommendation(raw_qty, profile)
        advice["recommended_qty"] = personalized_qty
        advice["personalized"] = profile["purchase_style"] != "balanced"

        # 落库 Recommendation 以追踪行为反馈；同时生成可执行 AIAction
        rec = Recommendation(
            merchant_id=merchant_id,
            product_id=pid,
            sku_id=sku_id,
            suggestion=advice["suggestion"],
            basis=advice.get("basis", []),
            risk_warning=advice.get("risk_warning"),
            recommended_qty=personalized_qty,
            confidence=advice.get("confidence", 0.78),
        )
        db.add(rec)
        saved_rec_ids.append(rec)

        # 仅当有采购建议时生成可执行 AIAction（hold 无需执行）
        if personalized_qty > 0:
            action = AIAction(
                merchant_id=merchant_id,
                recommendation_id=rec.id,
                action_type="purchase",
                title=f"采购{product.name}{personalized_qty}斤",
                payload={
                    "items": [
                        {
                            "product_id": pid,
                            "sku_id": str(sku_id) if sku_id else None,
                            "qty": float(personalized_qty),
                            "unit": "斤",
                            "cost": 0,
                        }
                    ],
                    "total_cost": 0,
                },
            )
            db.add(action)

        recommendations.append(advice)

    await db.flush()  # Flush 以拿到 recommendation IDs
    await db.commit()  # 保持 ID 有效，供后续行为反馈

    # 按置信度（最紧急优先）排序
    recommendations.sort(key=lambda x: x.get("recommended_qty", 0), reverse=True)

    return {
        "recommendations": recommendations,
        "recommendation_ids": [str(r.id) for r in saved_rec_ids],
        "generated_at": utc_now().isoformat(),
        "behavior_profile": profile,
        "env_summary": {
            "temp_high": env_factors.temp_high,
            "rainfall_prob": env_factors.rainfall_prob,
            "is_weekend": env_factors.is_weekend,
            "is_holiday": env_factors.is_holiday,
        },
    }
