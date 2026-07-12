"""一次性脚本：把开发库 qiantan_dev.db 同步到当前模型（dev 模式用 create_all）。

为什么需要它：
- create_all 只新建【不存在的表】，不会给【已存在的表】加列。
- 新代码在 inventory_records 上加了 idempotency_key，但库里这张表已存在，
  因此 create_all 不会自动补列 → 写入幂等键时会报「无此列」。
- 本脚本：① 跑 create_all（补齐 7 张 catalog 新表）；
          ② 手动给 inventory_records 加 idempotency_key 列 + 唯一索引
            （等价于 migrations/versions/002 的作用，只是绕过未安装的 alembic）。

生产环境请用 `alembic upgrade head`，不要用手动脚本。
"""

import asyncio
import sqlite3

from app.database import Base, init_db


async def main():
    # 1) 补齐新表（catalog 等）
    await init_db()
    print("[ok] create_all 完成（已补齐新表）")

    # 2) 给已存在的 inventory_records 补列（create_all 不会做这事）
    db_path = "qiantan_dev.db"
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(inventory_records)").fetchall()]
    if "idempotency_key" not in cols:
        cur.execute("ALTER TABLE inventory_records ADD COLUMN idempotency_key VARCHAR(64)")
        print("[ok] 已添加 inventory_records.idempotency_key")
    else:
        print("[skip] idempotency_key 已存在")

    cur.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='index' AND name='ix_inventory_records_idempotency_key'"
    )
    if not cur.fetchone():
        cur.execute(
            "CREATE UNIQUE INDEX ix_inventory_records_idempotency_key "
            "ON inventory_records (idempotency_key)"
        )
        print("[ok] 已创建唯一索引 ix_inventory_records_idempotency_key")
    else:
        print("[skip] 唯一索引已存在")

    # 3) 补齐 P0-1 鉴权字段：merchants.wechat_openid / role + auth_revoked_tokens 表
    #    create_all 不会给已存在的 merchants 表加列，也不会自动建 auth_revoked_tokens。
    mcols = [r[1] for r in cur.execute("PRAGMA table_info(merchants)").fetchall()]
    if "wechat_openid" not in mcols:
        cur.execute("ALTER TABLE merchants ADD COLUMN wechat_openid VARCHAR(64)")
        print("[ok] 已添加 merchants.wechat_openid")
    else:
        print("[skip] merchants.wechat_openid 已存在")
    if "role" not in mcols:
        cur.execute("ALTER TABLE merchants ADD COLUMN role VARCHAR(20) DEFAULT 'owner'")
        print("[ok] 已添加 merchants.role")
    else:
        print("[skip] merchants.role 已存在")

    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='ix_merchants_wechat_openid'"
    )
    if not cur.fetchone():
        cur.execute(
            "CREATE UNIQUE INDEX ix_merchants_wechat_openid "
            "ON merchants (wechat_openid)"
        )
        print("[ok] 已创建唯一索引 ix_merchants_wechat_openid")
    else:
        print("[skip] 唯一索引 ix_merchants_wechat_openid 已存在")

    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_revoked_tokens'"
    )
    if not cur.fetchone():
        cur.execute(
            "CREATE TABLE auth_revoked_tokens ("
            "jti VARCHAR(64) PRIMARY KEY, "
            "revoked_at DATETIME, "
            "expires_at DATETIME)"
        )
        print("[ok] 已创建表 auth_revoked_tokens")
    else:
        print("[skip] 表 auth_revoked_tokens 已存在")

    con.commit()
    con.close()

    # 3) 验证：重新拉取元数据，确认列与表齐全
    tabs = sorted(Base.metadata.tables.keys())
    assert "idempotency_key" in [c.name for c in Base.metadata.tables["inventory_records"].columns]
    for t in ("product_skus", "product_aliases", "product_specifications",
              "units", "unit_conversions", "suppliers", "supplier_products",
              "auth_revoked_tokens"):
        assert t in tabs, f"缺少表 {t}"
    mcols = [c.name for c in Base.metadata.tables["merchants"].columns]
    for c in ("wechat_openid", "role"):
        assert c in mcols, f"merchants 缺少列 {c}"
    assert "idempotency_key" in [
        c.name for c in Base.metadata.tables["inventory_records"].columns
    ]
    print(
        f"[ok] 校验通过：共 {len(tabs)} 张表，"
        f"idempotency_key / wechat_openid / role / auth_revoked_tokens 均就位"
    )


if __name__ == "__main__":
    asyncio.run(main())
