"""核心实体状态机 — 定义 Tenant/Subscription/Invoice/AIAction 的合法状态流转。

非法流转在服务层返回 409，禁止路由直接赋值 status 字段。
"""

from __future__ import annotations

from fastapi import HTTPException, status


# ── 租户状态 ────────────────────────────────────────────

TENANT_TRANSITIONS: dict[str, set[str]] = {
    "trial": {"active", "suspended", "expired"},
    "active": {"suspended", "expired"},
    "suspended": {"active", "expired"},
    "expired": {"trial", "active"},  # 允许重新激活
}


def validate_tenant_transition(from_status: str, to_status: str, entity_name: str = "租户") -> None:
    allowed = TENANT_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{entity_name} 状态从 {from_status} 不能直接变更为 {to_status}",
        )


# ── 订阅状态 ────────────────────────────────────────────

SUBSCRIPTION_TRANSITIONS: dict[str, set[str]] = {
    "trialing": {"active", "canceled", "expired"},
    "active": {"past_due", "canceled"},
    "past_due": {"active", "suspended", "canceled", "expired"},
    "suspended": {"active", "canceled", "expired"},
    "canceled": set(),  # 终态
    "expired": set(),   # 终态
}


def validate_subscription_transition(from_status: str, to_status: str) -> None:
    allowed = SUBSCRIPTION_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"订阅状态从 {from_status} 不能直接变更为 {to_status}",
        )


# ── 发票状态 ────────────────────────────────────────────

INVOICE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"sent", "void"},
    "sent": {"paid", "overdue", "void"},
    "overdue": {"paid", "void"},
    "paid": set(),   # 终态，只能冲正（新记录）
    "void": set(),   # 终态
}


def validate_invoice_transition(from_status: str, to_status: str) -> None:
    allowed = INVOICE_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"发票状态从 {from_status} 不能直接变更为 {to_status}",
        )


# ── AI 动作状态 ─────────────────────────────────────────

AI_ACTION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"executed", "rejected", "cancelled", "failed"},
    "executed": set(),     # 终态
    "rejected": set(),     # 终态
    "failed": {"pending"}, # 可重试
    "cancelled": set(),    # 终态
}


def validate_ai_action_transition(from_status: str, to_status: str) -> None:
    allowed = AI_ACTION_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AI 动作状态从 {from_status} 不能直接变更为 {to_status}",
        )
