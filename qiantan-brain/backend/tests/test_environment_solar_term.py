"""Regression tests for the environment solar-term calendar."""

from app.routers.environment import resolve_solar_term


def test_before_first_january_term_wraps_to_winter_solstice():
    result = resolve_solar_term("0101")
    assert result["solar_term"] == "冬至"
    assert result["next_term"] == "小寒"
    assert result["term_range"] == "12/21 – 01/05"


def test_first_january_term_has_next_term():
    result = resolve_solar_term("0105")
    assert result["solar_term"] == "小寒"
    assert result["next_term"] == "大寒"
    assert result["term_range"] == "01/05 – 01/20"


def test_normal_midyear_term_has_next_term():
    result = resolve_solar_term("0712")
    assert result["solar_term"] == "小暑"
    assert result["next_term"] == "大暑"


def test_year_end_wraps_next_term_to_january():
    result = resolve_solar_term("1231")
    assert result["solar_term"] == "冬至"
    assert result["next_term"] == "小寒"
    assert result["term_range"] == "12/21 – 01/05"


async def test_solar_term_returns_structured_product_list(client):
    res = await client.get("/api/v1/env/solar-term")
    assert res.status_code == 200
    data = res.json()["data"]
    assert isinstance(data["in_season_product_list"], list)
    assert data["in_season_product_list"]
    assert all(isinstance(name, str) and name.strip() for name in data["in_season_product_list"])
    assert "·".join(data["in_season_product_list"]) == data["in_season_products"]
