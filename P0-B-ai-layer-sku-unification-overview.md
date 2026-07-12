# P0-B AI 层与 CurrentInventory SKU 化收尾 — 落地总览

## 一句话结论
`Recommendation`、`CurrentInventory`、`AI 参谋/预测/数字孪生` 已接到 SKU 维度；采购建议 from-advice 直接带 `sku_id` 落库。Alembic 迁移已生成并在全新/存量库验证通过。全量测试 **109 passed, 1 skipped**。

---

## 改动清单

### 模型层
| 文件 | 改动 |
|------|------|
| `app/models/recommendation.py` | `Recommendation` 新增 `sku_id`（可空，FK→`product_skus.id`，index） |
| `app/models/inventory.py` | `CurrentInventory` 新增 `sku_id`（可空，FK→`product_skus.id`，index）；保留现有 `(merchant_id, product_id)` 主键 |

### 迁移
| 文件 | 说明 |
|------|------|
| `migrations/versions/80bd7e0fc1ac_recommendation_and_current_inventory_sku_id.py` | 接在 `008aca35a3e6` 之后；仅新增 `recommendations.sku_id` 与 `current_inventory.sku_id` 的列/索引/FK；使用 `batch_alter_table` 兼容 SQLite |

### AI 查询层
| 文件 | 改动 |
|------|------|
| `app/services/advisor.py` | `build_daily_advice` 为每个品类解析主 SKU；库存/销量查询按 `product_id` + (`sku_id` 或 NULL) 兼容过滤；调用 `predict_demand(db, merchant_id, pid, sku_id=sku_id)`；写入 `Recommendation`/`AIAction` 时落 `sku_id`；返回建议带 `sku_id` |
| `app/services/forecast.py` | `predict_demand` 与 `_get_daily_sales_history` 增加可选 `sku_id`；有 SKU 时按 SKU 聚合历史，否则回退无 SKU 数据；返回结果带 `sku_id` |
| `app/routers/twin.py` | `/inventory-mirror` 的 products 列表增加 `sku_id`/`sku_name`；lifecycle heatmap 增加 `sku_id`/`sku_name` |

### 采购闭环
| 文件 | 改动 |
|------|------|
| `app/routers/purchase.py` | from-advice 生成 `PurchaseItem` 时直接带 `rec.sku_id` |

---

## 设计决策

1. **可空兼容**：`sku_id` 全部可空，旧数据无 SKU 时不影响查询正确性。
2. **过滤策略**： advisor/forecast 的查询以 `product_id` 为基，若已绑定 SKU 则同时纳入 `sku_id == 该 SKU OR sku_id IS NULL` 的记录。这样过渡期不会因为部分记录有 SKU、部分没有而漏算。
3. **聚合键保留 product_id**：`CurrentInventory` 主键不变；twin 的 dashboard/business-mirror/risk-mirror 仍按 product_id 聚合。当前商户内 category:sku 一对一，不影响精度；未来一品类多 SKU 时再拆主键/聚合键。
4. **迁移保守清理**：autogenerate 检测出大量 VARCHAR→Uuid 类型差异、merchants 索引重命名等噪声。本次迁移只保留真正新增的 `recommendations.sku_id` 和 `current_inventory.sku_id`，其余留到后续专项迁移处理。
5. **SQLite 兼容**：所有外键/索引/列变更放在 `batch_alter_table` 内，避免 SQLite 不支持原生 `ALTER TABLE ADD FOREIGN KEY` 报错。

---

## 验证结果

| 验证项 | 结果 |
|--------|------|
| 全新库 `alembic upgrade head` | ✅ 通过，`recommendations` 与 `current_inventory` 均含 `sku_id` |
| 存量 dev 库 `alembic upgrade head` | ✅ 通过，`008aca35a3e6` → `80bd7e0fc1ac` |
| `import app.main` | ✅ |
| `uvicorn app.main:app` + `/api/v1/health` | ✅ 200 |
| 全量 pytest | **109 passed, 1 skipped** |

---

## 生产/合入检查清单

- [ ] 生产库（PG/SQLite）先 `alembic upgrade head` 再部署代码
- [ ] 若生产库已有 `recommendations`/`current_inventory` 数据，`sku_id` 可空，无需回填
- [ ] 前端消费 `/advice/daily`、`/twin/inventory-mirror` 时，可读取新增 `sku_id`/`sku_name` 字段做 SKU 级展示

---

## 残留与后续路线

1. **twin 剩余接口按 SKU 拆分**：`/dashboard`、`/business-mirror`、`/risk-mirror` 仍按 `product_id` 聚合。当前 category:sku 一对一，不影响；未来多 SKU 时需要拆分。
2. **CurrentInventory 刷新逻辑**：当前表/视图如何刷新未在本次改动；若后续按 SKU 刷新需同步调整刷新 SQL。
3. **历史 event_time 时区迁移**：仍是全局时区彻底统一的残留项。
4. **Alembic 历史噪声迁移**：基线与模型之间还存在一些 VARCHAR/Uuid 类型差异、缺失 FK 等，建议后续生成一个专项迁移统一补齐。

---

## 可复用经验

- **autogenerate 迁移必须人工审计**：基线/模型/实际库三者可能不一致，autogenerate 会带上大量噪声，直接应用可能破坏现有 schema。
- **SQLite FK 变更用 `batch_alter_table`**：原生 `op.create_foreign_key` 在 SQLite 上报 `NotImplementedError`。
- **过渡期查询用 `sku_id = X OR sku_id IS NULL`**：避免旧数据（NULL sku_id）被漏算。
