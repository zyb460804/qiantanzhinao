# 千摊智脑 数据库设计文档

> 更新日期: 2026-07-11
> 后端: SQLAlchemy 2.0 async | 双数据库支持: PostgreSQL 16(生产) / SQLite(开发)

本文档与 `backend/app/models/` 下的 ORM 模型同步,共 **10 张表**。

---

## 一、架构概览

### 1.1 ER 关系图

```
                          ┌─────────────────┐
                          │   merchants     │
                          │  (商户表)        │
                          └────────┬────────┘
                                   │ 1
                 ┌─────────────────┼─────────────────┐
                 │                 │                  │ 1
                 ▼ N               ▼ N                ▼ 1
        ┌─────────────┐   ┌──────────────┐   ┌──────────────────┐
        │ voice_logs  │   │ inventory_   │   │ merchant_        │
        │ (语音记录)   │   │ records      │   │ preferences      │
        └──────┬──────┘   │ (库存流水)    │   │ (商户偏好)        │
               │          └──────┬───────┘   └──────────────────┘
               │ 1               │ N
               │                 │
               └─── voice_log_id │
                                   │
                     ┌─────────────▼──────────┐
                     │ product_categories     │
                     │ (商品品类表)            │
                     └─────────────┬──────────┘
                                   │ 1
                    ┌──────────────┼──────────────┐
                    │ N            │ N            │ N
                    ▼              ▼              ▼
           ┌──────────────┐ ┌────────────┐ ┌──────────────┐
           │ recommenda-  │ │ simulation │ │ batch_life-  │
           │ tions        │ │ _records   │ │ cycles       │
           │ (建议记录)    │ │ (模拟记录)  │ │ (批次追踪)    │
           └──────────────┘ └────────────┘ └──────────────┘

     ┌───────────────────┐         ┌──────────────────┐
     │ environment_      │         │ current_inventory │
     │ records (环境数据) │         │ (当前库存汇总)     │
     └───────────────────┘         └──────────────────┘
```

### 1.2 双数据库策略

| 场景 | 数据库 | 连接串 |
|------|--------|--------|
| 开发(零依赖) | SQLite | `sqlite+aiosqlite:///./qiantan_dev.db` |
| 生产(Docker) | PostgreSQL 16 | `postgresql+asyncpg://user:pass@db:5432/qiantan` |

所有模型使用 DB 无关类型(`sa.Uuid`、`sa.JSON`、`sa.Boolean`),避免 PostgreSQL 专有类型,实现无缝切换。

---

## 二、表结构详解

### 2.1 `merchants` — 商户表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, 默认 uuid4 | 商户唯一标识 |
| `name` | VARCHAR(100) | NOT NULL | 商户/摊位名称 |
| `business_type` | VARCHAR(50) | | 经营类型(生鲜/水果/蔬菜/豆制品) |
| `location` | VARCHAR(200) | | 经营地点 |
| `preferences` | JSON | 默认 {} | 偏好配置(方言、保守/激进等) |
| `created_at` | DateTime | server_default now() | 创建时间 |
| `updated_at` | DateTime | onupdate now() | 更新时间 |

### 2.2 `product_categories` — 商品品类表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK, autoincrement | 品类ID |
| `name` | VARCHAR(50) | UNIQUE, NOT NULL | 品类名(白菜/土豆/西瓜) |
| `unit` | VARCHAR(20) | NOT NULL, 默认"斤" | 默认计量单位 |
| `default_price` | DECIMAL(10,2) | | 参考单价 |
| `shelf_life_hours` | Integer | NOT NULL | 保质期小时(白菜72h,豆腐24h) |
| `category_group` | VARCHAR(30) | | 大类(叶菜类/根茎类/水果类/肉类等) |
| `is_active` | Boolean | 默认 true | 是否在营 |

**与规则配置的关系**:[product_categories.json](../backend/app/rules/product_categories.json) 定义品类分组与保质期,DB 表存储商户实际经营的品类实例。

### 2.3 `inventory_records` — 库存流水表(核心)

每次库存变动一条记录(采购=正、销售=负、损耗=负)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | 流水ID |
| `merchant_id` | UUID | FK→merchants | 商户ID |
| `product_id` | Integer | FK→product_categories | 品类ID |
| `quantity` | DECIMAL(10,2) | NOT NULL | 变动量(正=入库,负=出库) |
| `unit` | VARCHAR(20) | NOT NULL | 单位 |
| `unit_cost` | DECIMAL(10,2) | | 进货单价(入库时) |
| `unit_price` | DECIMAL(10,2) | | 售价(出库时) |
| `total_amount` | DECIMAL(12,2) | | 总金额 |
| `event_type` | VARCHAR(30) | NOT NULL | `purchase`/`sale`/`waste`/`adjustment` |
| `event_time` | DateTime | NOT NULL | 事件发生时间 |
| `source` | VARCHAR(30) | 默认 "voice" | 数据来源 `voice`/`vision`/`manual`/`simulate` |
| `voice_log_id` | UUID | | 关联语音记录 |
| `batch_label` | VARCHAR(50) | | 批次标签(生命周期追踪用) |
| `notes` | Text | | 备注 |
| `created_at` | DateTime | server_default now() | |

**当前库存计算**:`SUM(quantity) GROUP BY merchant_id, product_id`(无物化视图,实时聚合)。

### 2.4 `current_inventory` — 当前库存汇总表

物化汇总视图,由业务逻辑刷新。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `merchant_id` | UUID | PK(联合) | 商户ID |
| `product_id` | Integer | PK(联合) | 品类ID |
| `current_qty` | DECIMAL(12,2) | | 当前库存量 |
| `avg_cost` | DECIMAL(10,2) | | 加权平均成本 |
| `last_updated` | DateTime | | 最后更新时间 |

### 2.5 `voice_logs` — 语音记录表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | 语音记录ID |
| `merchant_id` | UUID | FK→merchants | 商户ID |
| `audio_url` | VARCHAR(500) | | 录音文件路径 |
| `asr_text` | Text | 默认 "" | ASR 识别原文 |
| `parsed_event` | JSON | | 解析后的结构化事件 |
| `status` | VARCHAR(20) | 默认 "pending" | `pending`/`parsed`/`confirmed`/`corrected`/`error` |
| `correction_count` | Integer | 默认 0 | 修正次数 |
| `created_at` | DateTime | server_default now() | |

**`parsed_event` JSON 结构**:
```json
{
  "event_type": "purchase",
  "product": "白菜",
  "quantity": 50,
  "unit": "斤",
  "unit_cost": 0.3,
  "total_cost": 15.0,
  "confidence": 0.85,
  "missing_fields": []
}
```

### 2.6 `environment_records` — 环境数据表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `date` | Date | NOT NULL | 日期 |
| `city` | VARCHAR(50) | 默认 "上海" | 城市 |
| `temp_high` | DECIMAL(5,1) | | 最高温℃ |
| `temp_low` | DECIMAL(5,1) | | 最低温℃ |
| `weather_type` | VARCHAR(30) | | 晴/多云/雨/雪 |
| `rainfall_prob` | DECIMAL(5,1) | | 降雨概率% |
| `is_holiday` | Boolean | 默认 false | 是否节假日 |
| `holiday_name` | VARCHAR(50) | | 节假日名称 |
| `day_of_week` | Integer | | 0=周日...6=周六 |
| `is_weekend` | Boolean | 默认 false | 是否周末 |
| `special_event` | VARCHAR(100) | | 特殊事件(春节前3天) |
| `fetched_at` | DateTime | server_default now() | 抓取时间 |

**唯一约束**:`UNIQUE(date, city)` — 同一城市同一天仅一条。

### 2.7 `recommendations` — 经营建议表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | 建议ID |
| `merchant_id` | UUID | FK→merchants | |
| `product_id` | Integer | FK→product_categories | |
| `suggestion` | Text | NOT NULL | 一句话建议 |
| `basis` | JSON | 默认 [] | 依据列表(三行式) |
| `risk_warning` | Text | | 风险提示 |
| `recommended_qty` | DECIMAL(10,2) | | 建议数量 |
| `confidence` | DECIMAL(4,2) | | 置信度 |
| `was_adopted` | Boolean | | 是否采纳(行为学习用) |
| `actual_deviation` | DECIMAL(10,2) | | 实际偏差 |
| `created_at` | DateTime | | |

**`basis` JSON 结构**:
```json
[
  { "factor": "近7日平均销量", "value": "18斤", "impact": "+" },
  { "factor": "周末客流", "value": "预计增加12%", "impact": "+" }
]
```

### 2.8 `simulation_records` — 决策模拟记录表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `merchant_id` | UUID | FK→merchants | |
| `product_id` | Integer | FK→product_categories | |
| `input_params` | JSON | 默认 {} | `{purchase_qty, unit_cost, unit_price}` |
| `output_result` | JSON | 默认 {} | `{estimated_sales, net_profit, ...}` |
| `created_at` | DateTime | | |

### 2.9 `batch_lifecycles` — 批次生命周期表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `merchant_id` | UUID | FK→merchants | |
| `product_id` | Integer | FK→product_categories | |
| `batch_label` | VARCHAR(50) | NOT NULL | 批次标签 |
| `purchase_date` | DateTime | NOT NULL | 进货时间 |
| `purchase_qty` | DECIMAL(10,2) | NOT NULL | 进货量 |
| `remaining_qty` | DECIMAL(10,2) | NOT NULL | 剩余量 |
| `expiry_date` | DateTime | | 到期时间 |
| `status` | VARCHAR(20) | 默认 "fresh" | `fresh`/`attention`/`expiring`/`spoiled` |
| `last_check` | DateTime | | 最后检查时间 |

### 2.10 `merchant_preferences` — 商户偏好表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `merchant_id` | UUID | FK, UNIQUE | 一商户一记录 |
| `risk_profile` | VARCHAR(20) | 默认 "neutral" | `conservative`/`neutral`/`aggressive` |
| `voice_dialect` | VARCHAR(30) | 默认 "mandarin" | 方言 |
| `favorite_products` | JSON | 默认 [] | 常营品类ID列表 |
| `avg_order_size` | DECIMAL(10,2) | | 平均订单量 |
| `preference_data` | JSON | 默认 {} | `{overbuy_tendency, discount_threshold}` |

---

## 三、关键查询模式

### 3.1 当前库存聚合

```python
# inventory/current 端点
SELECT product_id, SUM(quantity) AS current_qty, AVG(unit_cost) AS avg_cost
FROM inventory_records
WHERE merchant_id = :mid
GROUP BY product_id
```

### 3.2 移动平均销量

```python
# advice/daily 端点计算 MA7/MA30
# 排除断货日(sale 事件 quantity=0 且当日无库存)
SELECT AVG(daily_sales) FROM (
  SELECT DATE(event_time), SUM(-quantity) AS daily_sales
  FROM inventory_records
  WHERE merchant_id = :mid AND product_id = :pid AND event_type = 'sale'
    AND event_time >= :start_date
  GROUP BY DATE(event_time)
)
```

### 3.3 环境数据缓存查

```python
SELECT * FROM environment_records WHERE date = :today AND city = :city
# 命中 → 直接返回(source=cached)
# 未命中 → 调和风API → INSERT → 返回(source=qweather)
# API 失败 → 返回 mock(source=mock)
```

---

## 四、推荐索引

```sql
-- 库存查询(高频)
CREATE INDEX idx_inv_merchant_time ON inventory_records(merchant_id, event_time DESC);
CREATE INDEX idx_inv_product ON inventory_records(product_id);

-- 语音记录查询
CREATE INDEX idx_voice_merchant_time ON voice_logs(merchant_id, created_at DESC);

-- 环境数据查询
CREATE INDEX idx_env_date ON environment_records(date);

-- 建议效果追踪(行为学习)
CREATE INDEX idx_rec_merchant_time ON recommendations(merchant_id, created_at DESC);
CREATE INDEX idx_rec_adopted ON recommendations(was_adopted) WHERE was_adopted IS NOT NULL;
```

> **SQLite 说明**:开发模式下索引通过 `init_db()` 自动建表创建;生产 PostgreSQL 通过 Alembic 迁移管理(见 [backend/migrations/](../backend/migrations/))。

---

## 五、数据初始化

种子脚本 [backend/scripts/seed_db.py](../backend/scripts/seed_db.py) 创建:

| 数据 | 数量 | 说明 |
|------|------|------|
| merchants | 1 | 默认测试商户 |
| product_categories | 10 | 白菜/土豆/黄瓜/番茄/苹果/西瓜/豆腐/猪肉/葱/姜 |
| environment_records | 30 | 过去 30 天天气 |
| inventory_records | 284 | 30 天的采购(+) + 销售(-) 流水 |

运行:`cd backend && python scripts/seed_db.py`
