"""Channel bill parsing, import idempotency, and payment matching."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import (
    ChannelBillEntry,
    ChannelBillImport,
    ReconciliationDifference,
    ReconciliationTask,
)
from app.models.pos import Payment, SaleOrder


class BillParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedBillEntry:
    entry_key: str
    record_type: str
    channel_ref: str | None
    merchant_ref: str | None
    amount: Decimal
    fee_amount: Decimal
    occurred_at: datetime | None
    channel_status: str | None
    raw_data: dict[str, Any]


class ReconciliationResult(TypedDict):
    system_total: Decimal
    channel_total: Decimal
    diff_amount: Decimal
    fee_amount: Decimal
    matched_count: int
    unmatched_system: int
    unmatched_channel: int
    difference_count: int
    status: str


TRANSACTION_REF_FIELDS = (
    "channel_transaction_id",
    "transaction_id",
    "渠道订单号",
    "微信订单号",
    "支付宝交易号",
)
REFUND_REF_FIELDS = (
    "channel_refund_id",
    "refund_id",
    "微信退款单号",
    "支付宝退款号",
    "退款批次号/请求号",
)
MERCHANT_REF_FIELDS = (
    "merchant_order_no",
    "order_no",
    "商户订单号",
)
AMOUNT_FIELDS = (
    "amount",
    "paid_amount",
    "应结订单金额",
    "商家实收（元）",
    "商家实收(元)",
    "订单金额（元）",
    "订单金额(元)",
    "订单金额",
)
REFUND_AMOUNT_FIELDS = (
    "refund_amount",
    "退款金额",
    "退款金额（元）",
    "退款金额(元)",
    "申请退款金额",
)
FEE_FIELDS = ("fee", "手续费", "服务费（元）", "服务费(元)")
STATUS_FIELDS = ("status", "交易状态", "退款状态", "状态")
TYPE_FIELDS = ("record_type", "业务类型", "收支类型", "交易类型")
TIME_FIELDS = ("occurred_at", "transaction_time", "交易时间", "完成时间", "创建时间")
SKIP_STATUS_MARKERS = ("失败", "关闭", "撤销", "取消", "REVOKED", "CLOSED", "FAIL")
MAX_BILL_ENTRIES = 50000


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip().strip("`").strip()


def _value(row: dict[str, str], aliases: tuple[str, ...]) -> str:
    normalized = {_clean(key): _clean(value) for key, value in row.items() if key is not None}
    for alias in aliases:
        value = normalized.get(alias)
        if value:
            return value
    return ""


def _amount(value: str, *, default: Decimal = Decimal("0")) -> Decimal:
    cleaned = _clean(value).replace(",", "").replace("¥", "").replace("￥", "")
    if not cleaned:
        return default
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise BillParseError(f"无法解析金额: {value}") from exc


def _datetime(value: str) -> datetime | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BillParseError("账单编码不支持，请使用 UTF-8 或 GB18030 CSV")


def _reader(text: str) -> csv.DictReader[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if any(alias in line for alias in TRANSACTION_REF_FIELDS + MERCHANT_REF_FIELDS)
        ),
        None,
    )
    if header_index is None:
        raise BillParseError("未找到渠道交易号或商户订单号表头")
    return csv.DictReader(io.StringIO("\n".join(lines[header_index:])))


def _entry(
    *,
    record_type: str,
    channel_ref: str | None,
    merchant_ref: str | None,
    amount: Decimal,
    fee_amount: Decimal,
    occurred_at: datetime | None,
    channel_status: str | None,
    raw_data: dict[str, Any],
) -> ParsedBillEntry:
    key_source = "|".join(
        (
            record_type,
            channel_ref or "",
            merchant_ref or "",
            str(amount),
            occurred_at.isoformat() if occurred_at else "",
        )
    )
    return ParsedBillEntry(
        entry_key=hashlib.sha256(key_source.encode()).hexdigest(),
        record_type=record_type,
        channel_ref=channel_ref,
        merchant_ref=merchant_ref,
        amount=amount,
        fee_amount=fee_amount,
        occurred_at=occurred_at,
        channel_status=channel_status,
        raw_data=raw_data,
    )


def parse_channel_bill(content: bytes) -> list[ParsedBillEntry]:
    """Parse normalized CSV plus common WeChat/Alipay statement columns."""
    entries: list[ParsedBillEntry] = []
    seen_keys: set[str] = set()
    for raw_row in _reader(_decode(content)):
        row = {_clean(key): _clean(value) for key, value in raw_row.items() if key is not None}
        status = _value(row, STATUS_FIELDS)
        if any(marker.lower() in status.lower() for marker in SKIP_STATUS_MARKERS):
            continue

        channel_ref = _value(row, TRANSACTION_REF_FIELDS) or None
        refund_ref = _value(row, REFUND_REF_FIELDS) or None
        merchant_ref = _value(row, MERCHANT_REF_FIELDS) or None
        if channel_ref is None and refund_ref is None and merchant_ref is None:
            continue

        record_type = _value(row, TYPE_FIELDS).lower()
        amount = _amount(_value(row, AMOUNT_FIELDS))
        refund_amount = abs(_amount(_value(row, REFUND_AMOUNT_FIELDS)))
        fee = abs(_amount(_value(row, FEE_FIELDS)))
        occurred_at = _datetime(_value(row, TIME_FIELDS))
        is_refund_row = "refund" in record_type or "退款" in record_type

        parsed_rows: list[ParsedBillEntry] = []
        if is_refund_row:
            signed_refund = -abs(refund_amount or amount)
            if signed_refund != 0:
                parsed_rows.append(
                    _entry(
                        record_type="refund",
                        channel_ref=refund_ref or channel_ref,
                        merchant_ref=merchant_ref,
                        amount=signed_refund,
                        fee_amount=fee,
                        occurred_at=occurred_at,
                        channel_status=status or None,
                        raw_data=row,
                    )
                )
        else:
            if amount != 0:
                parsed_rows.append(
                    _entry(
                        record_type="payment" if amount > 0 else "refund",
                        channel_ref=channel_ref,
                        merchant_ref=merchant_ref,
                        amount=amount,
                        fee_amount=fee,
                        occurred_at=occurred_at,
                        channel_status=status or None,
                        raw_data=row,
                    )
                )
            if refund_amount > 0:
                parsed_rows.append(
                    _entry(
                        record_type="refund",
                        channel_ref=refund_ref or channel_ref,
                        merchant_ref=merchant_ref,
                        amount=-refund_amount,
                        fee_amount=Decimal("0"),
                        occurred_at=occurred_at,
                        channel_status=status or None,
                        raw_data=row,
                    )
                )

        for parsed in parsed_rows:
            if parsed.entry_key in seen_keys:
                duplicate_ref = parsed.channel_ref or parsed.merchant_ref
                raise BillParseError(f"账单内存在重复交易: {duplicate_ref}")
            seen_keys.add(parsed.entry_key)
            entries.append(parsed)
            if len(entries) > MAX_BILL_ENTRIES:
                raise BillParseError(f"单个账单最多支持 {MAX_BILL_ENTRIES} 条明细")

    if not entries:
        raise BillParseError("账单中没有可对账的成功支付或退款记录")
    return entries


async def get_or_create_task(
    db: AsyncSession,
    merchant_id: uuid.UUID,
    channel: str,
    bill_date: date,
) -> ReconciliationTask:
    task = await db.scalar(
        select(ReconciliationTask).where(
            ReconciliationTask.merchant_id == merchant_id,
            ReconciliationTask.channel == channel,
            ReconciliationTask.date == bill_date,
        )
    )
    if task is None:
        task = ReconciliationTask(merchant_id=merchant_id, channel=channel, date=bill_date)
        db.add(task)
        await db.flush()
    return task


async def import_channel_bill_file(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    channel: str,
    bill_date: date,
    file_name: str,
    content: bytes,
    fee_rate: Decimal,
) -> tuple[ChannelBillImport, bool, ReconciliationResult]:
    file_hash = hashlib.sha256(content).hexdigest()
    existing = await db.scalar(
        select(ChannelBillImport).where(
            ChannelBillImport.merchant_id == merchant_id,
            ChannelBillImport.channel == channel,
            ChannelBillImport.bill_date == bill_date,
            ChannelBillImport.file_hash == file_hash,
        )
    )
    task = await get_or_create_task(db, merchant_id, channel, bill_date)
    if existing is not None:
        return existing, True, await current_task_result(db, task)

    parsed = parse_channel_bill(content)
    existing_keys = set(
        (
            await db.execute(
                select(ChannelBillEntry.entry_key).where(
                    ChannelBillEntry.merchant_id == merchant_id,
                    ChannelBillEntry.channel == channel,
                    ChannelBillEntry.bill_date == bill_date,
                    ChannelBillEntry.entry_key.in_([entry.entry_key for entry in parsed]),
                )
            )
        ).scalars()
    )
    bill_import = ChannelBillImport(
        task_id=task.id,
        merchant_id=merchant_id,
        channel=channel,
        bill_date=bill_date,
        file_name=file_name[:255] or "channel-bill.csv",
        file_hash=file_hash,
        row_count=len(parsed),
        inserted_count=len(parsed) - len(existing_keys),
        duplicate_count=len(existing_keys),
    )
    db.add(bill_import)
    await db.flush()
    db.add_all(
        [
            ChannelBillEntry(
                import_id=bill_import.id,
                task_id=task.id,
                merchant_id=merchant_id,
                channel=channel,
                bill_date=bill_date,
                entry_key=entry.entry_key,
                record_type=entry.record_type,
                channel_ref=entry.channel_ref,
                merchant_ref=entry.merchant_ref,
                amount=entry.amount,
                fee_amount=entry.fee_amount,
                occurred_at=entry.occurred_at,
                channel_status=entry.channel_status,
                raw_data=entry.raw_data,
            )
            for entry in parsed
            if entry.entry_key not in existing_keys
        ]
    )
    await db.flush()
    result = await reconcile_task(db, task, fee_rate=fee_rate)
    return bill_import, False, result


async def reconcile_task(
    db: AsyncSession,
    task: ReconciliationTask,
    *,
    fee_rate: Decimal,
) -> ReconciliationResult:
    day_start = datetime.combine(task.date, time.min)
    day_end = datetime.combine(task.date, time.max)
    payment_rows = (
        await db.execute(
            select(Payment, SaleOrder.order_no)
            .outerjoin(SaleOrder, SaleOrder.id == Payment.order_id)
            .where(
                Payment.merchant_id == task.merchant_id,
                Payment.method == task.channel,
                Payment.status.in_(("success", "refunded")),
                Payment.created_at >= day_start,
                Payment.created_at <= day_end,
            )
            .order_by(Payment.created_at, Payment.id)
        )
    ).all()
    entries = (
        (
            await db.execute(
                select(ChannelBillEntry)
                .where(ChannelBillEntry.task_id == task.id)
                .order_by(ChannelBillEntry.occurred_at, ChannelBillEntry.id)
            )
        )
        .scalars()
        .all()
    )

    await db.execute(
        delete(ReconciliationDifference).where(
            ReconciliationDifference.task_id == task.id,
            ReconciliationDifference.status == "open",
        )
    )
    for entry in entries:
        entry.matched_payment_id = None
        entry.match_status = "unmatched"

    system_total = sum((payment.amount for payment, _ in payment_rows), Decimal("0"))
    channel_total = sum((entry.amount for entry in entries), Decimal("0"))
    channel_fee = sum((entry.fee_amount for entry in entries), Decimal("0"))
    task.system_total = system_total.quantize(Decimal("0.01"))
    task.channel_total = channel_total.quantize(Decimal("0.01"))
    task.diff_amount = (task.system_total - task.channel_total).quantize(Decimal("0.01"))
    task.fee_amount = (
        channel_fee if channel_fee > 0 else abs(task.channel_total * fee_rate)
    ).quantize(Decimal("0.01"))

    if not entries:
        task.matched_count = 0
        task.unmatched_system = len(payment_rows)
        task.unmatched_channel = 0
        task.status = "pending"
        task.note = "渠道账单尚未导入，无法执行逐笔对账"
        return _result(task, difference_count=0)

    payments_by_id = {payment.id: (payment, order_no) for payment, order_no in payment_rows}
    unused_ids = set(payments_by_id)
    by_transaction: dict[str, list[uuid.UUID]] = {}
    by_order: dict[str, list[uuid.UUID]] = {}
    for payment, order_no in payment_rows:
        if payment.transaction_id:
            by_transaction.setdefault(payment.transaction_id, []).append(payment.id)
        if order_no:
            by_order.setdefault(order_no, []).append(payment.id)

    matched_count = 0
    mismatch_count = 0
    channel_only_count = 0
    differences: list[ReconciliationDifference] = []
    for entry in entries:
        candidate_ids = [
            payment_id
            for payment_id in by_transaction.get(entry.channel_ref or "", [])
            if payment_id in unused_ids
        ]
        if not candidate_ids:
            candidate_ids = [
                payment_id
                for payment_id in by_order.get(entry.merchant_ref or "", [])
                if payment_id in unused_ids
            ]
        if not candidate_ids:
            entry.match_status = "channel_only"
            channel_only_count += 1
            differences.append(
                ReconciliationDifference(
                    task_id=task.id,
                    merchant_id=task.merchant_id,
                    diff_type="channel_only",
                    channel_ref=entry.channel_ref or entry.merchant_ref,
                    channel_amount=entry.amount,
                )
            )
            continue

        matched_id = next(
            (
                payment_id
                for payment_id in candidate_ids
                if payments_by_id[payment_id][0].amount == entry.amount
            ),
            candidate_ids[0],
        )
        payment, order_no = payments_by_id[matched_id]
        unused_ids.remove(matched_id)
        entry.matched_payment_id = matched_id
        if payment.amount == entry.amount:
            entry.match_status = "matched"
            matched_count += 1
        else:
            entry.match_status = "amount_mismatch"
            mismatch_count += 1
            differences.append(
                ReconciliationDifference(
                    task_id=task.id,
                    merchant_id=task.merchant_id,
                    diff_type="amount_mismatch",
                    system_ref=payment.transaction_id or order_no or str(payment.id),
                    channel_ref=entry.channel_ref or entry.merchant_ref,
                    system_amount=payment.amount,
                    channel_amount=entry.amount,
                )
            )

    for payment_id in unused_ids:
        payment, order_no = payments_by_id[payment_id]
        differences.append(
            ReconciliationDifference(
                task_id=task.id,
                merchant_id=task.merchant_id,
                diff_type="system_only",
                system_ref=payment.transaction_id or order_no or str(payment.id),
                system_amount=payment.amount,
            )
        )

    # Query existing resolved/ignored differences to avoid duplicates
    existing_diffs = (
        (
            await db.execute(
                select(ReconciliationDifference).where(
                    ReconciliationDifference.task_id == task.id,
                    ReconciliationDifference.status.in_(["resolved", "ignored"]),
                )
            )
        )
        .scalars()
        .all()
    )

    existing_keys = {(d.diff_type, d.system_ref or "", d.channel_ref or "") for d in existing_diffs}

    # Filter out differences that already exist as resolved/ignored
    new_differences = [
        d
        for d in differences
        if (d.diff_type, d.system_ref or "", d.channel_ref or "") not in existing_keys
    ]
    db.add_all(new_differences)
    task.matched_count = matched_count
    task.unmatched_system = len(unused_ids) + mismatch_count
    task.unmatched_channel = channel_only_count + mismatch_count
    task.status = (
        "balanced"
        if not new_differences and abs(task.diff_amount) <= Decimal("0.01")
        else "exception"
    )
    task.note = (
        f"逐笔匹配 {matched_count} 笔，系统侧未匹配 {task.unmatched_system} 笔，"
        f"渠道侧未匹配 {task.unmatched_channel} 笔"
    )
    return _result(task, difference_count=len(new_differences))


def _result(task: ReconciliationTask, *, difference_count: int) -> ReconciliationResult:
    return {
        "system_total": task.system_total,
        "channel_total": task.channel_total,
        "diff_amount": task.diff_amount,
        "fee_amount": task.fee_amount,
        "matched_count": task.matched_count,
        "unmatched_system": task.unmatched_system,
        "unmatched_channel": task.unmatched_channel,
        "difference_count": difference_count,
        "status": task.status,
    }


async def current_task_result(
    db: AsyncSession,
    task: ReconciliationTask,
) -> ReconciliationResult:
    difference_count = await db.scalar(
        select(func.count(ReconciliationDifference.id)).where(
            ReconciliationDifference.task_id == task.id
        )
    )
    return _result(task, difference_count=int(difference_count or 0))
