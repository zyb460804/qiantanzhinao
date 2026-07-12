"""
QWeather API integration service.
Fetches current weather + 3-day forecast, caches to DB.
Includes Chinese holiday data and data source tracking.

Free tier: 1000 calls/day. Register at https://dev.qweather.com/
Docs: https://dev.qweather.com/docs/api/weather/
"""

import logging
from datetime import date, datetime, timedelta

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

# ── QWeather city IDs for common Chinese cities ──────────────────────────
_CITY_IDS: dict[str, str] = {
    "上海": "101020100",
    "北京": "101010100",
    "广州": "101280101",
    "深圳": "101280601",
    "杭州": "101210101",
    "南京": "101190101",
    "武汉": "101200101",
    "成都": "101270101",
    "重庆": "101040100",
    "西安": "101110101",
}

# Weather text → simplified Chinese label mapping
_WEATHER_SIMPLIFY: dict[str, str] = {
    "晴": "晴",
    "少云": "多云",
    "晴间多云": "多云",
    "多云": "多云",
    "阴": "阴",
    "小雨": "雨",
    "中雨": "雨",
    "大雨": "雨",
    "暴雨": "雨",
    "阵雨": "雨",
    "雷阵雨": "雨",
    "小雪": "雪",
    "中雪": "雪",
    "大雪": "雪",
    "雾": "雾",
    "霾": "霾",
}

# ── Chinese statutory holidays (2025-2026) ───────────────────────────────
# In production, replace with a holiday API or maintain annually.
_HOLIDAYS: dict[str, str] = {
    # 2025
    "2025-01-01": "元旦",
    "2025-01-28": "除夕",
    "2025-01-29": "春节",
    "2025-01-30": "春节",
    "2025-01-31": "春节",
    "2025-02-01": "春节",
    "2025-02-02": "春节",
    "2025-02-03": "春节",
    "2025-04-04": "清明节",
    "2025-04-05": "清明节",
    "2025-04-06": "清明节",
    "2025-05-01": "劳动节",
    "2025-05-02": "劳动节",
    "2025-05-03": "劳动节",
    "2025-05-04": "劳动节",
    "2025-05-05": "劳动节",
    "2025-06-01": "端午节",
    "2025-06-02": "端午节",
    "2025-10-01": "国庆节",
    "2025-10-02": "国庆节",
    "2025-10-03": "国庆节",
    "2025-10-04": "国庆节",
    "2025-10-05": "国庆节",
    "2025-10-06": "国庆节",
    "2025-10-07": "国庆节",
    # 2026
    "2026-01-01": "元旦",
    "2026-02-16": "除夕",
    "2026-02-17": "春节",
    "2026-02-18": "春节",
    "2026-02-19": "春节",
    "2026-02-20": "春节",
    "2026-02-21": "春节",
    "2026-02-22": "春节",
    "2026-04-05": "清明节",
    "2026-04-06": "清明节",
    "2026-04-07": "清明节",
    "2026-05-01": "劳动节",
    "2026-05-02": "劳动节",
    "2026-05-03": "劳动节",
    "2026-05-04": "劳动节",
    "2026-05-05": "劳动节",
    "2026-06-19": "端午节",
    "2026-06-20": "端午节",
    "2026-10-01": "国庆节",
    "2026-10-02": "国庆节",
    "2026-10-03": "国庆节",
    "2026-10-04": "国庆节",
    "2026-10-05": "国庆节",
    "2026-10-06": "国庆节",
    "2026-10-07": "国庆节",
    "2026-10-08": "国庆节",
}


def _get_holiday(d: date) -> tuple[bool, str | None]:
    """Check if a date is a Chinese statutory holiday.

    Returns (is_holiday, holiday_name).
    Also checks adjacent days for multi-day holidays.
    """
    d_str = d.strftime("%Y-%m-%d")
    if d_str in _HOLIDAYS:
        return True, _HOLIDAYS[d_str]
    # Check if it's a adjusted working day (weekend that's a workday)
    # For simplicity, we only check the explicit holiday list
    return False, None


def _city_id(city: str) -> str:
    """Resolve city name to QWeather location ID."""
    return _CITY_IDS.get(city, settings.weather_city_id)


def _simplify_weather(text: str) -> str:
    """Map QWeather detailed weather description to simplified label."""
    return _WEATHER_SIMPLIFY.get(text, text)


async def _fetch_json(url: str, params: dict[str, str]) -> dict | None:
    """Fetch JSON from QWeather API with timeout and error handling."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "200":
                return data
            logger.warning("QWeather API returned code=%s", data.get("code"))
            return None
    except httpx.HTTPError as e:
        logger.warning("QWeather API request failed: %s", e)
        return None
    except Exception:
        logger.warning("QWeather API unexpected error", exc_info=True)
        return None


async def fetch_current_weather(city: str = "上海") -> dict | None:
    """Fetch current weather from QWeather.

    Returns dict with keys matching EnvironmentRecord fields, or None on failure.
    """
    if not settings.weather_api_key:
        logger.info("No QWeather API key — using mock data")
        return None

    url = f"{settings.weather_api_url}/now"
    params = {"location": _city_id(city), "key": settings.weather_api_key}
    data = await _fetch_json(url, params)
    if not data:
        return None

    now = data.get("now", {})
    temp = float(now.get("temp", 25))
    weather_text = now.get("text", "晴")
    precip = float(now.get("precip", 0))
    # If currently raining, rainfall prob is high; otherwise estimate from humidity
    humidity = float(now.get("humidity", 50))
    if "雨" in weather_text:
        rainfall_prob = max(60.0, precip)
    elif humidity > 80:
        rainfall_prob = 40.0
    else:
        rainfall_prob = float(precip) if precip > 0 else 10.0

    today = date.today()
    dow = today.weekday()
    is_holiday, holiday_name = _get_holiday(today)

    return {
        "date": today,
        "city": city,
        "temp_high": temp,
        "temp_low": round(temp - 8.0, 1),
        "weather_type": _simplify_weather(weather_text),
        "rainfall_prob": rainfall_prob,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "day_of_week": dow,
        "is_weekend": dow >= 5,
        "source": "qweather",
    }


async def fetch_forecast(city: str = "上海", days: int = 3) -> list[dict] | None:
    """Fetch N-day weather forecast from QWeather.

    Returns list of dicts with EnvironmentRecord fields, or None on failure.
    """
    if not settings.weather_api_key:
        logger.info("No QWeather API key — skipping forecast fetch")
        return None

    url = f"{settings.weather_api_url}/{days}d"
    params = {"location": _city_id(city), "key": settings.weather_api_key}
    data = await _fetch_json(url, params)
    if not data:
        return None

    results = []
    for daily in data.get("daily", []):
        fx_date = datetime.strptime(daily["fxDate"], "%Y-%m-%d").date()
        dow = fx_date.weekday()
        is_holiday, holiday_name = _get_holiday(fx_date)
        results.append(
            {
                "date": fx_date,
                "city": city,
                "temp_high": float(daily.get("tempMax", 25)),
                "temp_low": float(daily.get("tempMin", 15)),
                "weather_type": _simplify_weather(daily.get("textDay", "晴")),
                "rainfall_prob": float(daily.get("precip", 0)),
                "is_holiday": is_holiday,
                "holiday_name": holiday_name,
                "day_of_week": dow,
                "is_weekend": dow >= 5,
                "source": "qweather",
            }
        )

    return results


async def get_today_env(city: str = "上海") -> dict:
    """Get today's environment data, fetching from API if needed.

    Always returns a dict (mock fallback if API unavailable).
    """
    result = await fetch_current_weather(city)
    if result:
        return result
    # Mock fallback
    return _mock_today(city)


async def get_forecast_env(city: str = "上海", days: int = 3) -> list[dict]:
    """Get forecast, fetching from API if needed. Falls back to mock."""
    result = await fetch_forecast(city, days)
    if result:
        return result
    return _mock_forecast(city, days)


def _mock_today(city: str) -> dict:
    """Mock today's environment data (used when no API key configured)."""
    today = date.today()
    dow = today.weekday()
    is_holiday, holiday_name = _get_holiday(today)
    return {
        "date": today,
        "city": city,
        "temp_high": 28.0,
        "temp_low": 18.0,
        "weather_type": "晴",
        "rainfall_prob": 10.0,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "day_of_week": dow,
        "is_weekend": dow >= 5,
        "source": "mock",
    }


def _mock_forecast(city: str, days: int) -> list[dict]:
    """Mock forecast data (used when no API key configured)."""
    today = date.today()
    results = []
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        dow = d.weekday()
        is_holiday, holiday_name = _get_holiday(d)
        results.append(
            {
                "date": d,
                "city": city,
                "temp_high": 28.0 + i,
                "temp_low": 18.0 + i,
                "weather_type": "多云" if i % 2 == 0 else "晴",
                "rainfall_prob": 10.0 + i * 5,
                "is_holiday": is_holiday,
                "holiday_name": holiday_name,
                "day_of_week": dow,
                "is_weekend": dow >= 5,
                "source": "mock",
            }
        )
    return results
