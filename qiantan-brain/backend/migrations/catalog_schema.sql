-- =============================================================================
-- 千摊智脑 · 商品/单位/供应商 目录 + 库存幂等键  ·  SQL 参考（手写迁移）
-- =============================================================================
-- 用途：
--   1) 代码评审时对照 catalog.py 模型，确认字段/约束一致；
--   2) 不使用 Alembic 自动生成时，可手动在目标库执行本文件；
--   3) 与 migrations/versions/002_catalog_and_idempotency.py 内容等价。
--
-- 方言说明：
--   - 默认按 PostgreSQL 书写（生产环境）。
--   - SQLite 开发库由 app/database.py 的 create_all 自动建表，无需手跑本文件；
--     SQLite 下 UUID 实际存为 CHAR(32)/TEXT，NUMERIC 存为 REAL/INTEGER，不影响语义。
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 商品 SKU：库存/批次/账本的真正主键（取代扁平的 product_categories）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_skus (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    name            VARCHAR(50) NOT NULL,            -- 标准名：番茄
    category_group  VARCHAR(30),
    canonical_unit  VARCHAR(10) NOT NULL DEFAULT '斤', -- 该 SKU 账本标准单位
    shelf_life_hours INTEGER,
    default_sale_price NUMERIC(10, 2),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 商品别名 → SKU 映射（番茄/西红柿/洋柿子 都指向同一个 SKU）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_aliases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    sku_id      UUID NOT NULL REFERENCES product_skus(id),
    alias       VARCHAR(50) NOT NULL,
    is_system   BOOLEAN DEFAULT FALSE,              -- True=系统内置（如 西红柿→番茄）
    CONSTRAINT uq_alias_per_merchant UNIQUE (merchant_id, alias)
);

-- ---------------------------------------------------------------------------
-- 商品规格（大果/精品/次品），影响售价
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_specifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    sku_id      UUID NOT NULL REFERENCES product_skus(id),
    name        VARCHAR(30) NOT NULL,
    price_delta NUMERIC(10, 2) DEFAULT 0,           -- 相对标准售价加价
    is_active   BOOLEAN DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- 单位字典（斤/筐/件），kind 区分 weight/package/count
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS units (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    code      VARCHAR(10) NOT NULL,                 -- 斤/筐/件
    name      VARCHAR(20) NOT NULL,
    kind      VARCHAR(10) DEFAULT 'weight',
    is_base   BOOLEAN DEFAULT FALSE,
    CONSTRAINT uq_unit_code_per_merchant UNIQUE (merchant_id, code)
);

-- ---------------------------------------------------------------------------
-- 单位换算因子：to_base = quantity * factor
--   sku_id 为 NULL 表示通用换算（如 公斤→斤 = 2）；
--   否则随商品不同（一筐西红柿≈45斤，一筐土豆≈60斤）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unit_conversions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    from_unit   VARCHAR(10) NOT NULL,
    to_unit     VARCHAR(10) NOT NULL,
    factor      NUMERIC(12, 4) NOT NULL,
    sku_id      UUID,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_unit_conv UNIQUE (merchant_id, from_unit, to_unit, sku_id)
);

-- ---------------------------------------------------------------------------
-- 供应商档案（支撑采购闭环、欠款、比价）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id    UUID NOT NULL REFERENCES merchants(id),
    name           VARCHAR(50) NOT NULL,            -- 老王
    contact        VARCHAR(50),
    min_order_qty  NUMERIC(10, 2),
    lead_time_hours INTEGER,
    is_active      BOOLEAN DEFAULT TRUE,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 供应商对某 SKU 的近期报价（支撑比价与采购预测）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supplier_products (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id    UUID NOT NULL REFERENCES merchants(id),
    supplier_id    UUID NOT NULL REFERENCES suppliers(id),
    sku_id         UUID NOT NULL REFERENCES product_skus(id),
    last_price     NUMERIC(10, 2),
    min_order_qty  NUMERIC(10, 2),
    updated_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 库存记录加幂等键：防止网络重试造成重复记账
--   允许 NULL（旧数据/手动补录）；NULL 不参与唯一冲突（PG/SQLite 均如此）。
-- ---------------------------------------------------------------------------
ALTER TABLE inventory_records ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS ix_inventory_records_idempotency_key
    ON inventory_records (idempotency_key);
