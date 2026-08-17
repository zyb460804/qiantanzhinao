"""app/services/unit_conversion.py — 跨模块单位换算契约测试。

契约（别路代理依赖，签名与返回语义不得漂移）：
    convert_to_base_unit(session, sku_id, quantity, from_unit)
    返回 (换算后数量, 基准单位)；SKU 未配置该换算时返回 None；Decimal 运算。
"""

import uuid
from decimal import Decimal

from tests.conftest import TEST_MERCHANT_ID

from app.models.catalog import ProductSKU, UnitConversion
from app.services.unit_conversion import convert_to_base_unit


async def _make_sku(session_factory, canonical_unit: str = "斤") -> uuid.UUID:
    async with session_factory() as session:
        sku = ProductSKU(
            merchant_id=uuid.UUID(TEST_MERCHANT_ID),
            name=f"测试SKU-{uuid.uuid4().hex[:6]}",
            canonical_unit=canonical_unit,
        )
        session.add(sku)
        await session.commit()
        await session.refresh(sku)
        return sku.id


async def _add_conversion(session_factory, sku_id, from_unit, to_unit, factor, sku_specific=True):
    async with session_factory() as session:
        session.add(
            UnitConversion(
                merchant_id=uuid.UUID(TEST_MERCHANT_ID),
                from_unit=from_unit,
                to_unit=to_unit,
                factor=Decimal(str(factor)),
                sku_id=sku_id if sku_specific else None,
            )
        )
        await session.commit()


class TestConvertToBaseUnit:
    async def test_sku_specific_factor(self, db_session):
        """SKU 专属换算：一箱番茄 20 斤 → 2 箱 = 40 斤。"""
        sku_id = await _make_sku(db_session)
        await _add_conversion(db_session, sku_id, "箱", "斤", 20)
        async with db_session() as session:
            result = await convert_to_base_unit(session, sku_id, Decimal("2"), "箱")
        assert result is not None
        amount, base = result
        assert amount == Decimal("40")
        assert base == "斤"

    async def test_generic_fallback(self, db_session):
        """无专属时回退商户通用换算（公斤→斤 = 2）：1 公斤 = 2 斤。"""
        sku_id = await _make_sku(db_session)
        await _add_conversion(db_session, sku_id, "公斤", "斤", 2, sku_specific=False)
        async with db_session() as session:
            result = await convert_to_base_unit(session, sku_id, 1, "公斤")
        assert result == (Decimal("2"), "斤")

    async def test_sku_specific_overrides_generic(self, db_session):
        """专属因子（筐→斤 20）必须压过通用（筐→斤 45）。"""
        sku_id = await _make_sku(db_session)
        await _add_conversion(db_session, sku_id, "筐", "斤", 45, sku_specific=False)
        await _add_conversion(db_session, sku_id, "筐", "斤", 20, sku_specific=True)
        async with db_session() as session:
            result = await convert_to_base_unit(session, sku_id, 1, "筐")
        assert result == (Decimal("20"), "斤")

    async def test_identity_unit_without_config(self, db_session):
        """from_unit 即基准单位：恒等换算，无需任何配置。"""
        sku_id = await _make_sku(db_session)
        async with db_session() as session:
            result = await convert_to_base_unit(session, sku_id, Decimal("3.5"), "斤")
        assert result == (Decimal("3.5"), "斤")

    async def test_unconfigured_returns_none(self, db_session):
        """SKU 未配置该换算 → None（调用方须显式处理，不得静默按 1:1 入账）。"""
        sku_id = await _make_sku(db_session)
        async with db_session() as session:
            assert await convert_to_base_unit(session, sku_id, 1, "筐") is None

    async def test_wrong_direction_conversion_ignored(self, db_session):
        """SKU 以公斤记账时，「筐→斤」是错方向配置，不得拿来用 → None。"""
        sku_id = await _make_sku(db_session, canonical_unit="公斤")
        await _add_conversion(db_session, sku_id, "筐", "斤", 45, sku_specific=False)
        async with db_session() as session:
            assert await convert_to_base_unit(session, sku_id, 1, "筐") is None

    async def test_unknown_or_inactive_sku_returns_none(self, db_session):
        """SKU 不存在 / 已停用 → None。"""
        async with db_session() as session:
            assert await convert_to_base_unit(session, uuid.uuid4(), 1, "斤") is None

        sku_id = await _make_sku(db_session)
        async with db_session() as session:
            sku = await session.get(ProductSKU, sku_id)
            sku.is_active = False
            await session.commit()
        async with db_session() as session:
            assert await convert_to_base_unit(session, sku_id, 1, "筐") is None

    async def test_decimal_precision_from_float(self, db_session):
        """float 0.1 经 str 中转 → Decimal('0.1')，乘法无二进制误差。"""
        sku_id = await _make_sku(db_session)
        await _add_conversion(db_session, sku_id, "公斤", "斤", 2, sku_specific=False)
        async with db_session() as session:
            result = await convert_to_base_unit(session, sku_id, 0.1, "公斤")
        assert result is not None
        assert isinstance(result[0], Decimal)
        assert result[0] == Decimal("0.2")

    async def test_sku_id_as_str_accepted(self, db_session):
        """宽容 str 形式 sku_id（语音/导入链路常传字符串）：3 公斤 = 6 斤。"""
        sku_id = await _make_sku(db_session)
        await _add_conversion(db_session, sku_id, "公斤", "斤", 2, sku_specific=False)
        async with db_session() as session:
            result = await convert_to_base_unit(session, str(sku_id), 3, "公斤")
        assert result == (Decimal("6"), "斤")
