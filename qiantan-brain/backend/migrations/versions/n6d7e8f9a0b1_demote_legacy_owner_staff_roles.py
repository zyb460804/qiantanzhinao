"""demote legacy owner-role staff to least-privileged role

Revision ID: n6d7e8f9a0b1
Revises: n5c6d7e8f9a0
Create Date: 2026-08-14

数据清理（fix-staff 残余风险移交）：staff CRUD 已禁止创建/修改 role='owner'，
但存量 staff_members 行里可能仍有 role='owner'，staff_login 对这些行仍会
签发 owner 员工 token（ROLE_PERMISSIONS['owner'] = 15 项全权限）。

处理：把 role='owner' 的行统一降级为 ROLE_PERMISSIONS 中权限集最小的角色
'purchaser'（仅 purchase_confirm 一项；app/models/staff.py）。不 DELETE，
行保留，sensitive_operations.staff_id 等 FK 不断裂。UPDATE ... WHERE 为
SQLite / PG 双兼容的纯 SQL，无需类型转换。

降级行数的核对方式（迁移内不打印业务日志）：
- 升级前：SELECT count(*) FROM staff_members WHERE role='owner';
- 升级后：SELECT count(*) FROM staff_members WHERE role='purchaser'; 对比增量。

downgrade 不做反向恢复：原角色信息未保留（无法区分「本来就是 purchaser」与
「被降级的 owner」），且按新设计 owner 身份应由商户本人（merchants）承载，
不应回到 staff_members 表。
"""

from collections.abc import Sequence

from alembic import op


revision: str = "n6d7e8f9a0b1"
down_revision: str | None = "n5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE staff_members SET role = 'purchaser' WHERE role = 'owner'")


def downgrade() -> None:
    # 刻意不实现：见模块 docstring —— 无法安全区分历史 purchaser 与被降级
    # 的 owner，恢复会把全权限 owner 角色重新放回员工体系。
    pass
