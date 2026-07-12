"""Environment data API router — QWeather integration + DB persistence."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import get_merchant_id
from app.models.environment import EnvironmentRecord
from app.schemas.common import AnyResponse
from app.services.weather import get_forecast_env, get_today_env


router = APIRouter(prefix="/api/v1/env", tags=["environment"])


@router.get("/today", response_model=AnyResponse)
async def get_today(city: str = "上海", db: AsyncSession = Depends(get_db)):
    """Get today's environment data.

    Checks DB first. If not cached, fetches from QWeather API
    (falls back to mock when API key is not configured).
    """
    today = date.today()

    # Check DB cache
    query = select(EnvironmentRecord).where(
        EnvironmentRecord.date == today,
        EnvironmentRecord.city == city,
    )
    result = await db.execute(query)
    cached = result.scalar_one_or_none()

    if cached:
        return {
            "code": 0,
            "data": _record_to_dict(cached),
            "source": "cache",
        }

    # Fetch from API (or mock)
    env_data = await get_today_env(city)
    data_source = env_data.pop("source", "unknown")

    # Persist to DB (source field is not a DB column, already removed)
    record = EnvironmentRecord(**env_data)
    db.add(record)
    await db.commit()

    return {
        "code": 0,
        "data": env_data,
        "source": data_source,
    }


@router.get("/forecast", response_model=AnyResponse)
async def get_forecast(
    city: str = "上海",
    days: int = 3,
    db: AsyncSession = Depends(get_db),
):
    """Get N-day weather forecast. Persists to DB for offline use."""
    forecasts = await get_forecast_env(city, days)

    # Determine data source from first forecast item
    data_source = "unknown"
    if forecasts:
        data_source = forecasts[0].get("source", "unknown")

    # Persist forecasts to DB (upsert — skip if already exists)
    # Strip 'source' field before passing to EnvironmentRecord
    saved = 0
    for fc in forecasts:
        fc_clean = {k: v for k, v in fc.items() if k != "source"}
        existing = await db.execute(
            select(EnvironmentRecord).where(
                EnvironmentRecord.date == fc_clean["date"],
                EnvironmentRecord.city == city,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(EnvironmentRecord(**fc_clean))
            saved += 1

    if saved > 0:
        await db.commit()

    return {
        "code": 0,
        "data": forecasts,
        "source": data_source,
    }


@router.get("/history", response_model=AnyResponse)
async def get_history(
    city: str = "上海",
    days: int = 30,
    db: AsyncSession = Depends(get_db),
):
    """Get recent environment records from DB."""
    from datetime import timedelta

    start_date = date.today() - timedelta(days=days)

    query = (
        select(EnvironmentRecord)
        .where(
            EnvironmentRecord.date >= start_date,
            EnvironmentRecord.city == city,
        )
        .order_by(EnvironmentRecord.date.desc())
    )
    result = await db.execute(query)
    records = result.scalars().all()

    return {
        "code": 0,
        "data": [_record_to_dict(r) for r in records],
        "count": len(records),
    }


def _record_to_dict(r: EnvironmentRecord) -> dict:
    return {
        "date": r.date.isoformat(),
        "city": r.city,
        "temp_high": float(r.temp_high) if r.temp_high else None,
        "temp_low": float(r.temp_low) if r.temp_low else None,
        "weather_type": r.weather_type,
        "rainfall_prob": float(r.rainfall_prob) if r.rainfall_prob else None,
        "is_holiday": r.is_holiday,
        "holiday_name": r.holiday_name,
        "day_of_week": r.day_of_week,
        "is_weekend": r.is_weekend,
    }


# ── 二十四节气查找表 (近似日期, 以月日为Key) ─────

SOLAR_TERMS = [
    ("0105", "小寒"), ("0120", "大寒"), ("0203", "立春"), ("0218", "雨水"),
    ("0305", "惊蛰"), ("0320", "春分"), ("0404", "清明"), ("0419", "谷雨"),
    ("0505", "立夏"), ("0520", "小满"), ("0605", "芒种"), ("0621", "夏至"),
    ("0706", "小暑"), ("0722", "大暑"), ("0807", "立秋"), ("0822", "处暑"),
    ("0907", "白露"), ("0922", "秋分"), ("1008", "寒露"), ("1023", "霜降"),
    ("1107", "立冬"), ("1122", "小雪"), ("1206", "大雪"), ("1221", "冬至"),
]

SEASONAL_PRODUCTS = {
    "小寒": "羊肉·白萝卜·冬笋", "大寒": "羊肉·白菜·冬笋",
    "立春": "春笋·韭菜·荠菜", "雨水": "春笋·韭菜·菠菜",
    "惊蛰": "春笋·韭菜·豆芽", "春分": "春笋·香椿·韭菜",
    "清明": "荠菜·莴笋·豆芽", "谷雨": "荠菜·黄瓜·莴笋",
    "立夏": "黄瓜·番茄·苦瓜", "小满": "黄瓜·番茄·苦瓜",
    "芒种": "西瓜·番茄·苦瓜", "夏至": "西瓜·番茄·黄瓜",
    "小暑": "西瓜·番茄·黄瓜·毛豆", "大暑": "西瓜·番茄·毛豆·苦瓜",
    "立秋": "西瓜·黄瓜·毛豆", "处暑": "西瓜·黄瓜·莲藕",
    "白露": "莲藕·梨·冬瓜", "秋分": "莲藕·梨·芋头",
    "寒露": "红薯·芋头·萝卜", "霜降": "红薯·萝卜·白菜",
    "立冬": "白菜·萝卜·萝卜", "小雪": "白菜·萝卜·红薯",
    "大雪": "白菜·萝卜·芋头", "冬至": "羊肉·白萝卜·白菜",
}

SEASONAL_ADVICE = {
    "小暑": [
        {"label": "备货", "icon": "cart", "tone": "info", "text": "西瓜、番茄进入当令旺销，西瓜备货量上调 30%。"},
        {"label": "营销", "icon": "spark", "tone": "hot", "text": '"消暑套餐"组合卖：西瓜+黄瓜+毛豆，客单更高。'},
        {"label": "时段", "icon": "leaf", "tone": "warn", "text": "午后高温，叶菜易蔫，早市多摆、晚市转清货。"},
    ],
    "大暑": [
        {"label": "备货", "icon": "cart", "tone": "info", "text": "西瓜、番茄持续旺销，冰镇水果日备货翻倍。"},
        {"label": "营销", "icon": "spark", "tone": "hot", "text": '"大暑消暑日"，推凉拌菜组合，吸引街坊。'},
        {"label": "时段", "icon": "leaf", "tone": "warn", "text": "午间高温，叶菜类易枯，早7点前上架，11点后减量。"},
    ],
}

DEFAULT_ADVICE = [
    {"label": "备货", "icon": "cart", "tone": "info", "text": "根据近期销量和天气，合理安排每日进货量。"},
    {"label": "营销", "icon": "spark", "tone": "hot", "text": "周末客流大时多备水果和叶菜，工作日以根茎类为主。"},
    {"label": "时段", "icon": "leaf", "tone": "warn", "text": "早市多摆叶菜，午市以根茎类为主，晚市打折清货。"},
]


@router.get("/solar-term", response_model=AnyResponse)
async def get_solar_term():
    """Get the current solar term and seasonal products.

    基于 date 查表返回最近节气、当令商品和经营建议。
    """
    today = date.today()
    mmdd = today.strftime("%m%d")

    # 找最近的节气 (不晚于今天)
    current_term = "小暑"  # fallback
    term_start = "0707"    # fallback start date
    term_end = "0722"      # fallback end date
    next_term_idx = None

    sorted_terms = sorted(SOLAR_TERMS, key=lambda x: x[0])

    for i, (td, name) in enumerate(sorted_terms):
        if td <= mmdd:
            current_term = name
            term_start = td
            next_idx = (i + 1) % len(sorted_terms)
            term_end = sorted_terms[next_idx][0] if next_idx > i else "1221"

    # Format date range
    start_str = term_start[:2] + "/" + term_start[2:]
    end_str = term_end[:2] + "/" + term_end[2:]

    products = SEASONAL_PRODUCTS.get(current_term, "西瓜·番茄·黄瓜")
    advice = SEASONAL_ADVICE.get(current_term, DEFAULT_ADVICE)

    return {
        "code": 0,
        "data": {
            "solar_term": current_term,
            "term_range": start_str + " – " + end_str,
            "in_season_products": products,
            "advice": advice,
            "next_term": next_term_idx and sorted_terms[next_term_idx][1] if next_term_idx is not None else None,
        },
    }


@router.get("/env/seasonal", response_model=AnyResponse)
async def get_seasonal_advice(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
):
    """Get seasonal business advice for the calendar page.

    Combines solar-term lookup with merchant-specific weather forecast summary.
    """
    today = date.today()
    mmdd = today.strftime("%m%d")

    # Find current solar term
    current_term = "小暑"
    for td, name in sorted(SOLAR_TERMS, key=lambda x: x[0]):
        if td <= mmdd:
            current_term = name

    products = SEASONAL_PRODUCTS.get(current_term, "西瓜·番茄·黄瓜")
    advice = SEASONAL_ADVICE.get(current_term, DEFAULT_ADVICE)

    return {
        "code": 0,
        "data": {
            "solar_term": current_term,
            "in_season_products": products,
            "advice": advice,
        },
    }


def _has_api_key() -> bool:
    from app.config import settings

    return bool(settings.weather_api_key)
