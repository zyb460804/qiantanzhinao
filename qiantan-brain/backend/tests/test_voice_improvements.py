"""语音链路改进回归测试（2026-09 第六轮实测话术后落地）。

覆盖：
  1. expense（经营支出）事件：「摊位费花了30块」不再误判成进货；确认后
     归口 expenses 表、不产生库存/销售/成本流水；撤销幂等删除费用行
  2. 混合总结句：「西瓜进了20斤卖了15斤赚了50」解析出 2 笔，利润不当收入
  3. 裸金额多意图：「上午卖了100下午卖了200」按时间词拆成 2 笔
  4. time_hint 时间词透传（命中什么放什么，不做日期自动偏移）
  5. 词表外品名候选（啤酒）与「进货葱」动词前缀剥离
  6. 纠错白名单 event_type=expense + 金额上限 422
"""

import uuid

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.expense import Expense
from app.models.inventory import InventoryRecord
from app.services.voice_parser import parse_voice_events, parse_voice_text


pytestmark = pytest.mark.asyncio

PRODUCT_NAMES = [
    "白菜",
    "土豆",
    "苹果",
    "猪肉",
    "西瓜",
    "豆腐",
]

MERCHANT_ID = uuid.UUID(TEST_MERCHANT_ID)


# ---------------------------------------------------------------------------
# 1. expense 事件解析（parser 单元）
# ---------------------------------------------------------------------------


class TestExpenseParsing:
    """费用类话术 → expense 事件：只带 total_amount，不碰商品/数量/单价。"""

    def test_stall_fee_is_expense(self):
        """「摊位费花了30块」实测：此前误判成 purchase 记成进货成本。"""
        r = parse_voice_text("摊位费花了30块", PRODUCT_NAMES)
        assert r["event_type"] == "expense"
        assert r["total_amount"] == 30.0
        assert r["quantity"] is None
        assert r["unit"] is None
        assert r["unit_cost"] is None
        assert r["unit_price"] is None
        assert r["product"] is None
        assert r["expense_category"] == "rent"
        # 费用不需要商品/数量，金额齐了就没有缺失项
        assert r["missing_fields"] == []

    @pytest.mark.parametrize(
        "text,category",
        [
            ("房租花了800块", "rent"),
            ("水电交了100块", "utility"),
            ("过路费花了20块", "fee"),
            ("今天花了50", "other"),
        ],
    )
    def test_expense_category_mapping(self, text, category):
        """费用名词 → Expense.category 归口（rent/utility/fee/other）。"""
        r = parse_voice_text(text, PRODUCT_NAMES)
        assert r["event_type"] == "expense", text
        assert r["expense_category"] == category, text

    def test_bare_spent_without_product_is_expense(self):
        """无商品词 + 付了/花了 → expense 兜底。"""
        r = parse_voice_text("付了30块", PRODUCT_NAMES)
        assert r["event_type"] == "expense"
        assert r["total_amount"] == 30.0

    def test_product_with_spent_stays_purchase(self):
        """「白菜花了10块」是进货口径，不因「花了」改判 expense。"""
        r = parse_voice_text("白菜花了10块", PRODUCT_NAMES)
        assert r["event_type"] == "purchase"

    def test_purchase_verb_wins_over_expense_noun(self):
        """有采购动词时仍是 purchase（「进了2箱苹果花了80」旧行为回归）。"""
        r = parse_voice_text("进了2箱苹果花了80", PRODUCT_NAMES)
        assert r["event_type"] == "purchase"
        assert r["total_amount"] == 80.0

    def test_expense_never_inherits_product(self):
        """前笔是白菜，费用笔不得继承商品（费用与商品无关）。"""
        events = parse_voice_events("进了白菜50斤，又交了摊位费30块", PRODUCT_NAMES)
        assert [(e["event_type"], e["product"]) for e in events] == [
            ("purchase", "白菜"),
            ("expense", None),
        ]
        assert events[1]["total_amount"] == 30.0
        assert events[1]["expense_category"] == "rent"


# ---------------------------------------------------------------------------
# 2. 混合总结句拆分（防丢笔）+ 利润不当收入
# ---------------------------------------------------------------------------


class TestMixedSummarySplit:
    """「西瓜进了20斤卖了15斤赚了50」实测：此前只解析出进货 1 笔。"""

    def test_two_events_with_product_carried(self):
        events = parse_voice_events("西瓜进了20斤卖了15斤赚了50", PRODUCT_NAMES)
        assert [(e["event_type"], e["product"], e["quantity"]) for e in events] == [
            ("purchase", "西瓜", 20.0),
            ("sale", "西瓜", 15.0),
        ]

    def test_profit_is_not_revenue(self):
        """「赚了50」是毛利不是收入：销售笔金额不得等于 50。"""
        events = parse_voice_events("西瓜进了20斤卖了15斤赚了50", PRODUCT_NAMES)
        sale = events[1]
        assert sale["event_type"] == "sale"
        assert sale["total_amount"] is None
        assert "amount" in sale["missing_fields"]

    def test_profit_clause_merges_into_previous_sale(self):
        """「卖了15斤，赚了50」是一笔，毛利不入金额、不另起空销售。"""
        events = parse_voice_events("卖了15斤，赚了50", PRODUCT_NAMES)
        assert len(events) == 1
        assert events[0]["event_type"] == "sale"
        assert events[0]["quantity"] == 15.0
        assert events[0]["total_amount"] is None

    def test_revenue_plus_profit(self):
        """「卖了100块赚了20块」：销售额 100，毛利 20 不入金额。"""
        r = parse_voice_text("卖了100块赚了20块", PRODUCT_NAMES)
        assert r["event_type"] == "sale"
        assert r["total_amount"] == 100.0

    def test_purchase_then_sale_mid_sentence(self):
        """「进了20斤白菜卖了15斤」句中切分 + 商品延续。"""
        events = parse_voice_events("进了20斤白菜卖了15斤", PRODUCT_NAMES)
        assert [(e["event_type"], e["quantity"]) for e in events] == [
            ("purchase", 20.0),
            ("sale", 15.0),
        ]
        assert events[1]["product"] == "白菜"

    def test_aggregate_still_single_event(self):
        """「一共卖了40块」不因动词切分被拆散（旧行为回归）。"""
        events = parse_voice_events("卖了西瓜20斤，两块钱一斤，一共卖了40块", PRODUCT_NAMES)
        assert len(events) == 1
        assert events[0]["total_amount"] == 40.0


# ---------------------------------------------------------------------------
# 3. 时间词边界 + 裸金额多意图
# ---------------------------------------------------------------------------


class TestBareAmountTimeSplit:
    """「上午卖了100下午卖了200」实测：此前输出 1 笔空事件。"""

    def test_two_sales_split_by_time_word(self):
        events = parse_voice_events("上午卖了100下午卖了200", PRODUCT_NAMES)
        assert [(e["event_type"], e["total_amount"]) for e in events] == [
            ("sale", 100.0),
            ("sale", 200.0),
        ]
        assert all("product" in e["missing_fields"] for e in events)

    def test_leading_time_word_not_split(self):
        """「今天上午卖了100」句首时间词不切分，仍是一笔。"""
        events = parse_voice_events("今天上午卖了100", PRODUCT_NAMES)
        assert len(events) == 1
        assert events[0]["total_amount"] == 100.0

    def test_bare_amount_after_sale(self):
        """「卖了100」裸金额即销售额（数量语境的「卖了3斤」不受影响）。"""
        r = parse_voice_text("卖了100", PRODUCT_NAMES)
        assert r["event_type"] == "sale"
        assert r["total_amount"] == 100.0

        r = parse_voice_text("卖了3斤猪肉", PRODUCT_NAMES)
        assert r["quantity"] == 3.0
        assert r["total_amount"] is None


# ---------------------------------------------------------------------------
# 4. time_hint 时间词语义透传
# ---------------------------------------------------------------------------


class TestTimeHint:
    """命中什么放什么（如「昨天」），不做日期自动偏移。"""

    @pytest.mark.parametrize(
        "text,hint",
        [
            ("昨天卖了200块", "昨天"),
            ("大前天进了白菜", "大前天"),
            ("昨儿卖了3斤苹果", "昨儿"),
            ("上午卖了100", "上午"),
            ("前天进了土豆", "前天"),
        ],
    )
    def test_hint_detected(self, text, hint):
        assert parse_voice_text(text, PRODUCT_NAMES)["time_hint"] == hint

    def test_no_hint_is_none(self):
        assert parse_voice_text("卖了3斤苹果", PRODUCT_NAMES)["time_hint"] is None

    def test_each_event_has_own_hint(self):
        events = parse_voice_events("上午卖了100下午卖了200", PRODUCT_NAMES)
        assert [e["time_hint"] for e in events] == ["上午", "下午"]


# ---------------------------------------------------------------------------
# 5. 词表外品名候选 + 动词前缀剥离
# ---------------------------------------------------------------------------


class TestOovProductWord:
    """词表未命中时 product_word 给出剥离数量/单位/动词后的干净候选。"""

    def test_beer_candidate(self):
        """「进了三箱啤酒每箱80元」实测：数量单价金额全对，候选=啤酒。"""
        r = parse_voice_text("进了三箱啤酒每箱80元", PRODUCT_NAMES)
        assert r["product"] is None
        assert r["product_word"] == "啤酒"
        assert r["quantity"] == 3.0
        assert r["unit_cost"] == 80.0
        assert r["total_amount"] == 240.0

    def test_verb_prefix_stripped(self):
        """「进货葱20斤」：动词「进货」不再并进品名（此前 product_word=进货葱）。"""
        assert parse_voice_text("进货葱20斤", PRODUCT_NAMES)["product_word"] == "葱"

    def test_two_char_oov_unchanged(self):
        """≥2 字词表外品名照旧（旧行为回归）。"""
        assert parse_voice_text("卖了3斤火龙果10块", PRODUCT_NAMES)["product_word"] == "火龙果"

    def test_non_product_word_still_rejected(self):
        """「剩余」等修饰词仍不得成为品名（旧行为回归）。"""
        assert parse_voice_text("剩余5斤卖完了", PRODUCT_NAMES)["product_word"] is None


# ---------------------------------------------------------------------------
# 6. API：parse-text 契约 + expense 确认/撤销落库 + 纠错白名单 + 金额上限
# ---------------------------------------------------------------------------


async def _parse_text(client, text: str) -> dict:
    resp = await client.post(
        "/api/v1/voice/parse-text", json={"merchant_id": TEST_MERCHANT_ID, "text": text}
    )
    assert resp.status_code == 200
    return resp.json()["data"]


class TestParseTextResponseContract:
    async def test_expense_event_in_response(self, client):
        data = await _parse_text(client, "摊位费花了30块")
        event = data["parsed"]
        assert event["event_type"] == "expense"
        assert event["total_amount"] == 30.0
        assert event["expense_category"] == "rent"
        assert event["time_hint"] is None
        assert len(data["events"]) == 1

    async def test_time_hint_in_response(self, client):
        data = await _parse_text(client, "昨天卖了200块")
        assert data["parsed"]["time_hint"] == "昨天"
        assert data["events"][0]["time_hint"] == "昨天"

    async def test_time_split_in_response(self, client):
        data = await _parse_text(client, "上午卖了100下午卖了200")
        assert data["warning"] == "检测到2笔，已全部拆分，请逐笔确认"
        assert [e["total_amount"] for e in data["events"]] == [100.0, 200.0]
        assert [e["time_hint"] for e in data["events"]] == ["上午", "下午"]
        assert all("product" in e["missing_fields"] for e in data["events"])


class TestExpenseConfirmFlow:
    async def test_confirm_books_expense_only(self, client, db_session):
        """expense 确认：落 expenses 表，不产生任何库存流水。"""
        data = await _parse_text(client, "摊位费花了30块")
        log_id = data["parsed"]["voice_log_id"]
        resp = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["event_type"] == "expense"
        assert float(body["total_amount"]) == 30.0

        async with db_session() as session:
            expenses = (await session.execute(select(Expense))).scalars().all()
            assert len(expenses) == 1
            assert expenses[0].merchant_id == MERCHANT_ID
            assert expenses[0].category == "rent"
            assert float(expenses[0].amount) == 30.0
            records = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.merchant_id == MERCHANT_ID
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert records == []

    async def test_monthly_report_counts_expense_not_revenue(self, client, db_session):
        """费用确认后月报：收入/进货成本不受影响，费用进 expenses 口径。"""
        data = await _parse_text(client, "摊位费花了30块")
        log_id = data["parsed"]["voice_log_id"]
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        ).status_code == 200

        async with db_session() as session:
            expense = (await session.execute(select(Expense))).scalar_one()
            month = expense.expense_date.strftime("%Y-%m")

        resp = await client.get(
            "/api/v1/expenses/monthly-report",
            params={"merchant_id": TEST_MERCHANT_ID, "month": month},
        )
        assert resp.status_code == 200
        report = resp.json()["data"]
        assert report["revenue"] == 0.0
        assert report["purchase_cost"] == 0.0
        assert report["expenses"] == 30.0
        assert report["net_profit"] == -30.0

    async def test_void_expense_deletes_row(self, client, db_session):
        """撤销 expense：费用行删除、状态翻转，重复撤销 409（幂等）。"""
        data = await _parse_text(client, "摊位费花了30块")
        log_id = data["parsed"]["voice_log_id"]
        assert (
            await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        ).status_code == 200

        void = await client.post(
            f"/api/v1/voice/{log_id}/void", json={"reason": "记错了"}
        )
        assert void.status_code == 200

        async with db_session() as session:
            assert len((await session.execute(select(Expense))).scalars().all()) == 0
            from app.models.voice import VoiceLog

            log = await session.get(VoiceLog, uuid.UUID(log_id))
            assert log.status == "voided"

        again = await client.post(f"/api/v1/voice/{log_id}/void", json={"reason": "再撤"})
        assert again.status_code == 409

    async def test_expense_without_amount_rejected(self, client):
        """费用缺金额：confirm 拦截 400，不落任何账。"""
        data = await _parse_text(client, "摊位费")
        log_id = data["parsed"]["voice_log_id"]
        resp = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert resp.status_code == 400
        assert "金额" in resp.json()["detail"]

    async def test_correction_switches_to_expense(self, client, db_session):
        """纠错白名单接受 event_type=expense：误判进货可改成费用后入账。"""
        data = await _parse_text(client, "进了白菜50斤")
        log_id = data["parsed"]["voice_log_id"]

        resp = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": log_id,
                "corrections": {"event_type": "expense", "total_amount": 30},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["parsed"]["event_type"] == "expense"

        confirm = await client.post("/api/v1/voice/confirm", json={"voice_log_id": log_id})
        assert confirm.status_code == 200
        assert confirm.json()["data"]["event_type"] == "expense"

        async with db_session() as session:
            expenses = (await session.execute(select(Expense))).scalars().all()
            assert len(expenses) == 1
            assert float(expenses[0].amount) == 30.0
            records = (
                (
                    await session.execute(
                        select(InventoryRecord).where(
                            InventoryRecord.merchant_id == MERCHANT_ID
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert records == []

    async def test_correction_whitelist_rejects_unknown_field(self, client):
        """白名单外字段（merchant_id 注入）仍被拒（旧行为回归）。"""
        data = await _parse_text(client, "进了白菜50斤")
        log_id = data["parsed"]["voice_log_id"]
        resp = await client.post(
            "/api/v1/voice/correct",
            json={
                "voice_log_id": log_id,
                "corrections": {"merchant_id": "hack"},
            },
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "field,value",
        [
            ("total_amount", 1000001),
            ("unit_price", 1000001),
            ("unit_cost", 1000001),
        ],
    )
    async def test_amount_upper_cap_422(self, client, field, value):
        """金额上限 le=1000000（与 SKU 售价口径一致）：超限 422。"""
        data = await _parse_text(client, "进了白菜50斤")
        log_id = data["parsed"]["voice_log_id"]
        resp = await client.post(
            "/api/v1/voice/correct",
            json={"voice_log_id": log_id, "corrections": {field: value}},
        )
        assert resp.status_code == 422
