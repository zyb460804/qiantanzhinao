"""Environment data API router — QWeather integration + DB persistence."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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


def _has_api_key() -> bool:
    from app.config import settings

    return bool(settings.weather_api_key)
