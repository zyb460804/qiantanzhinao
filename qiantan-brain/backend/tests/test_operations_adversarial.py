"""Operations router 对抗性测试 — 攻击面与边界输入覆盖。

设计原则（第一性原理 + 对抗性）：
  - 绿色测试：锁定当前【已正确防护】的行为，防止回归（负数/越权/隔离）。
  - xfail(strict=True) 测试：标记【已知漏洞】，当前实现会让测试失败（XFAIL）；
    一旦漏洞被修复，测试转为 XPASS → strict 模式下报失败，提醒去掉 xfail 标记。
    这样漏洞既是文档又是待办哨兵。

覆盖维度：
  1. 输入验证：负数/非数字/缺字段（body: dict 绕过 Pydantic 的代价）
  2. 权限：角色越权（require_permission 一致性）
  3. 租户隔离：跨商户数据不可见
  4. 业务逻辑：停赊/超额还款/重复幂等
  5. 导出安全：CSV 公式注入
"""

import csv as csv_mod
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.conftest import TEST_MERCHANT_ID

from app.models.accounts import CustomerReceivable
from app.models.batch import BatchLifecycle
from app.models.inventory import InventoryRecord
from app.models.staff import StaffMember
from app.services.batch import create_batch


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════


async def _seed_cashier(db_session, merchant_id: uuid.UUID) -> uuid.UUID:
    """Seed 一个 cashier 员工并返回其 id（用于 X-Staff-Id 头模拟角色）。"""
    async with db_session() as session:
        cashier = StaffMember(
            merchant_id=merchant_id,
            name="测试收银员",
            role="cashier",
            pin_code="0000",
        )
        session.add(cashier)
        await session.commit()
        return cashier.id


async def _seed_batch_with_stock(
    db_session, merchant_id: uuid.UUID, label: str, qty: Decimal = Decimal("20")
) -> str:
    """Seed 一个有库存的批次，返回 batch_id（字符串）。"""
    async with db_session() as session:
        batch = await create_batch(session, merchant_id, 1, "白菜", label, qty)
        await session.commit()
        return str(batch.id)


# ═══════════════════════════════════════════════════════════
# 报损 (waste) 对抗性
# ═══════════════════════════════════════════════════════════


class TestWasteAdversarial:
    async def test_negative_quantity_rejected(self, client):
        """负数报损数量 → 400（已有防护：quantity <= 0 校验）。"""
        res = await client.post(
            "/api/v1/ops/waste",
            json={"product_id": 1, "quantity": -5, "reason": "腐烂"},
        )
        assert res.status_code == 400

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: record_waste 用 body:dict 绕过 Pydantic，quantity='abc' → "
        "Decimal('abc') 抛 InvalidOperation → 500。应由 Pydantic schema 返回 422。",
    )
    async def test_non_numeric_quantity_rejected_as_422(self, client):
        """非数字 quantity 字符串 → 应 422（当前 500，信息泄露）。"""
        res = await client.post(
            "/api/v1/ops/waste",
            json={"product_id": 1, "quantity": "abc", "reason": "腐烂"},
        )
        assert res.status_code == 422

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: record_waste 用 body['product_id'] 直接取值，缺字段 → "
        "KeyError → 500。应由 Pydantic schema 返回 422（字段必填）。",
    )
    async def test_missing_product_id_rejected_as_422(self, client):
        """缺 product_id 字段 → 应 422（当前 KeyError → 500）。"""
        res = await client.post(
            "/api/v1/ops/waste",
            json={"quantity": 5, "reason": "腐烂"},
        )
        assert res.status_code == 422

    async def test_cashier_cannot_record_waste(self, client, db_session):
        """收银员（cashier）无 record_waste 权限 → 403。

        验证 require_permission('record_waste') 对低权限角色生效。
        cashier 仅持有 credit_sale / order_refund 两项权限。
        """
        mid = uuid.UUID(TEST_MERCHANT_ID)
        cashier_id = await _seed_cashier(db_session, mid)

        res = await client.post(
            "/api/v1/ops/waste",
            json={"product_id": 1, "quantity": 1, "reason": "腐烂"},
            headers={"X-Staff-Id": str(cashier_id)},
        )
        assert res.status_code == 403

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: record_waste 的 idempotency_key 唯一约束存在，但重复提交时 "
        "IntegrityError 未被捕获 → 500。应捕获并返回 409/原记录（幂等语义）。",
    )
    async def test_duplicate_idempotency_key_rejected_gracefully(self, client, db_session):
        """相同 idempotency_key 重复报损 → 第二次应 409 或返回原结果（当前 500）。"""
        mid = uuid.UUID(TEST_MERCHANT_ID)
        await _seed_batch_with_stock(db_session, mid, "idem-batch", Decimal("20"))

        payload = {
            "product_id": 1,
            "quantity": 3,
            "reason": "腐烂",
            "idempotency_key": "adversarial-dup-key-001",
        }
        first = await client.post("/api/v1/ops/waste", json=payload)
        assert first.status_code == 200

        second = await client.post("/api/v1/ops/waste", json=payload)
        # 幂等语义：重复请求不应二次扣库存，应 409 或 200（返回原 record_id）
        assert second.status_code in (200, 409)


# ═══════════════════════════════════════════════════════════
# 临期清货 / 促销 对抗性
# ═══════════════════════════════════════════════════════════


class TestClearanceAdversarial:
    async def test_zero_hours_rejected_as_422(self, client):
        """within_hours=0 → 必须是 422（Query ge=1 强制），不是 200。

        对抗点：早期测试写 `assert status_code in (422, 200)` —— 200 分支永不
        发生却没收紧，若有人删掉 ge=1 约束让 0 静默通过，测试照绿。
        """
        res = await client.get("/api/v1/ops/expiry/clearance?within_hours=0")
        assert res.status_code == 422

    async def test_negative_hours_rejected_as_422(self, client):
        """within_hours=-1 → 422。"""
        res = await client.get("/api/v1/ops/expiry/clearance?within_hours=-1")
        assert res.status_code == 422

    async def test_cashier_cannot_set_promotion(self, client, db_session):
        """收银员设置临期促销 → 403（已修复：挂 require_permission('change_price')）。

        回归哨兵：cashier 角色仅持有 credit_sale / order_refund 两项权限，
        无 change_price。若后续误删权限依赖，本测试会立刻失败。
        """
        mid = uuid.UUID(TEST_MERCHANT_ID)
        cashier_id = await _seed_cashier(db_session, mid)

        async with db_session() as session:
            batch = await create_batch(session, mid, 1, "白菜", "promo-batch", Decimal("5"))
            batch.expiry_date = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=12)
            await session.commit()
            batch_id = str(batch.id)

        res = await client.post(
            f"/api/v1/ops/expiry/clearance/{batch_id}/promotion",
            json={
                "promotion_price": 0.01,
                "end_at": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
            },
            headers={"X-Staff-Id": str(cashier_id)},
        )
        assert res.status_code == 403


# ═══════════════════════════════════════════════════════════
# 客户赊账 / 信用档案 对抗性
# ═══════════════════════════════════════════════════════════


class TestCustomerCreditAdversarial:
    async def test_repay_negative_amount_rejected(self, client):
        """负数回款金额 → 400（已有防护）。"""
        res = await client.post(
            "/api/v1/ops/customers/repay",
            json={"customer_name": "张记", "amount": -50},
        )
        assert res.status_code == 400

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: customer_repay 用 Decimal(str(body.get('amount',0)))，"
        "amount='abc' → InvalidOperation → 500。应由 Pydantic schema 返回 422。",
    )
    async def test_repay_non_numeric_amount_rejected_as_422(self, client):
        """非数字回款金额 → 应 422（当前 500）。"""
        res = await client.post(
            "/api/v1/ops/customers/repay",
            json={"customer_name": "张记", "amount": "abc"},
        )
        assert res.status_code == 422

    async def test_check_credit_blocked_customer_denied(self, client):
        """停赊客户赊账 → allowed=False（已有防护）。"""
        await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={"customer_name": "黑名单客户", "is_blocked": True, "block_reason": "逾期"},
        )
        res = await client.post(
            "/api/v1/ops/customers/check-credit",
            json={"customer_name": "黑名单客户", "amount": 10},
        )
        assert res.status_code == 200
        assert res.json()["data"]["allowed"] is False

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: upsert_customer_credit_profile 无 credit_limit 下限校验，"
        "负数额度直接落库。应拒绝或约束 ge=0。",
    )
    async def test_credit_profile_rejects_negative_limit(self, client):
        """负数信用额度 → 应 400（当前落库，业务语义错误）。"""
        res = await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={"customer_name": "负额度客户", "credit_limit": -9999},
        )
        assert res.status_code == 400

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: upsert_customer_credit_profile 无 default_credit_days 范围校验，"
        "负数账期直接落库（会导致所有客户立刻'未逾期'）。应约束 ge=0。",
    )
    async def test_credit_profile_rejects_negative_days(self, client):
        """负数默认账期天数 → 应 400（当前落库，绕过逾期检测）。"""
        res = await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={"customer_name": "负账期客户", "default_credit_days": -1},
        )
        assert res.status_code == 400

    @pytest.mark.xfail(
        strict=True,
        reason="漏洞: check_customer_credit 无 amount<=0 校验，负金额会让 "
        "remaining_credit = limit - balance - (-|amount|) 反向增加。应拒绝负金额。",
    )
    async def test_check_credit_rejects_negative_amount(self, client):
        """负金额信用检查 → 应拒绝（当前 allowed=True，remaining 反向增加）。"""
        await client.post(
            "/api/v1/ops/customers/credit-profile",
            json={"customer_name": "额度客户", "credit_limit": 500},
        )
        res = await client.post(
            "/api/v1/ops/customers/check-credit",
            json={"customer_name": "额度客户", "amount": -100},
        )
        # 负金额无业务意义，应被拒绝或 allowed=False
        data = res.json()["data"]
        assert data["allowed"] is False

    async def test_cross_merchant_customer_isolation(self, client, db_session):
        """商户A 的客户赊账记录不会被商户B 查到（租户隔离）。

        对抗点：若 customer_ledger 查询漏了 merchant_id 过滤，B 会看到 A 的欠款。
        """
        mid_a = uuid.UUID(TEST_MERCHANT_ID)
        mid_b = uuid.UUID("00000000-0000-0000-0000-000000000099")
        async with db_session() as session:
            session.add(
                CustomerReceivable(
                    merchant_id=mid_a,
                    customer_name="A的独家客户",
                    direction="charge",
                    amount=Decimal("999"),
                )
            )
            await session.commit()

        # 以 B 身份查 A 的客户 → 必须看不到任何记录
        res = await client.get(
            "/api/v1/ops/customers/A的独家客户/ledger",
            headers={"X-Test-Merchant-Id": str(mid_b)},
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["balance"] == 0
        assert data["items"] == []
        assert data["total_charge"] == 0


# ═══════════════════════════════════════════════════════════
# 导出 对抗性
# ═══════════════════════════════════════════════════════════


class TestExportAdversarial:
    async def test_export_accounts_sanitizes_formula_injection(self, client, db_session):
        """导出 CSV 必须净化公式注入字符（= / + / - / @ 前缀）。

        已修复：_rows_to_csv 通过 _sanitize_csv_cell 对每个单元格的危险前缀
        加单引号转义。回归哨兵：若有人移除净化，本测试会立刻失败。

        攻击路径：恶意 customer_name='=CMD(...)' → 导出 CSV → Excel 打开执行公式。
        """
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as db:
            db.add(
                CustomerReceivable(
                    merchant_id=mid,
                    customer_name='=CMD("calc.exe")',
                    direction="charge",
                    amount=Decimal("1"),
                )
            )
            await db.commit()

        res = await client.get("/api/v1/ops/export/accounts")
        assert res.status_code == 200
        csv_text = res.json()["data"]["csv"]

        # 用 csv.reader 解析回来，逐单元格检查：任何单元格不得以公式字符开头
        reader = csv_mod.reader(io.StringIO(csv_text.lstrip("﻿")))
        dangerous = {"=", "+", "-", "@", "\t", "\r"}
        for row in reader:
            for cell in row:
                stripped = cell.lstrip()
                assert not stripped or stripped[0] not in dangerous, (
                    f"CSV 公式注入未净化: 单元格以危险字符开头 → {cell!r}"
                )

    async def test_export_accounts_envelope_contract(self, client, db_session):
        """导出信封结构一致性：{code:0, data:{rows, csv, filename}} + BOM + filename。

        对抗点：早期 inventory/waste 导出测试只断言 'csv' in data，漏写 BOM 会
        导致 Excel 打开乱码却照绿。本测试锁死完整契约。
        """
        mid = uuid.UUID(TEST_MERCHANT_ID)
        async with db_session() as db:
            db.add(
                CustomerReceivable(
                    merchant_id=mid,
                    customer_name="契约测试客户",
                    direction="charge",
                    amount=Decimal("100"),
                )
            )
            await db.commit()

        res = await client.get("/api/v1/ops/export/accounts")
        assert res.status_code == 200
        assert "text/csv" not in res.headers.get("content-type", "")  # JSON 不是 CSV 流
        data = res.json()["data"]
        assert data["filename"] == "accounts.csv"
        assert data["csv"].startswith("﻿")  # UTF-8 BOM（Excel 中文不乱码）
        assert isinstance(data["rows"], list)
        assert len(data["rows"]) >= 1
