# 千摊智脑 · 项目长期记忆

## 项目性质
`qiantan-brain/` = 千摊智脑后端（FastAPI + SQLAlchemy async + SQLite/PG），前端微信小程序；面向摊主的经营 AI 助手（语音记账、视觉识货、库存、报告、AI 参谋、数字孪生、经验云聚合+差分隐私、边缘端树莓派）。
前端 WXSS 兼容性红线见《team-code-quality-guidance.md》（无 `@media`/`:root`/`*`/`vh`/`url()` 本地图等）。

## 技术债 · 头号优先级（2026-07-12 资深开发复审更新）
- **P0-A 鉴权覆盖不全（越权黑洞）— 已收口 ✅（2026-07-12）**：`core/security.py` 的 `get_current_merchant`+JWT 已落地，所有业务路由（advice/inventory/behavior/purchase/reports/twin/vision/voice）均已改走 token 鉴权 + 归属校验；`routers/edge.py` 也已接入 `get_merchant_id`，body 中 merchant_id 仅用于校验一致性，不再作为身份来源。生产置 `auth_allow_fallback=False`（config.py 已默认 False）即强制 token。测试见 `tests/test_auth_api.py`（真实 JWT 链路）+ `tests/test_edge_auth.py`（edge 鉴权）+ conftest 的 `get_current_merchant` override。

- **⚠️ 全局时区坑（代码层已规范，历史 event_time 数据待迁移）**：模型时间戳 `created_at` 等用 `server_default=sa.func.now()`（UTC），代码层新写入的 `voided_at`/`purchased_at`/`paid_at`/`closed_at`/`executed_at`/`event_time` 等已统一经 `app/core/timezone.py` 走 UTC；业务查询按字段来源选择时区基准——`created_at` 相关用 UTC 日期，`event_time` 相关用本地日期，避免 UTC+8 零点漏查。历史数据库中 `event_time` 仍为本地时间（旧写入未改时区），若需全栈统一 UTC 需单独数据迁移（+8h 偏移）。
- **P0-B SKU 体系孤儿化 — 已收口 ✅（2026-07-12）**：`InventoryRecord`/`PurchaseItem`/`BatchLifecycle`/`Recommendation`/`CurrentInventory` 已加 `sku_id`(UUID→product_skus.id，可空兼容)；新增 `app/services/sku_service.py`(`resolve_sku_id` 按名/别名/品类解析本商户 SKU)；写入路径（purchase confirm/from-advice、voice confirm/edit、pos 销售、offline_sync upsert）接 sku_id；查询层已切换：`inventory.py /current` 和 `/history` 返回 `sku_id`/`sku_name`，`batch.py` 的 FIFO 扣减新增可选 `sku_id` 参数并优先按 SKU 扣减、无 SKU 回退 product_id，`pos.py` 销售按 SKU 扣减批次；AI 参谋/预测/数字孪生（`advisor.py`/`forecast.py`/`twin.py`）已按 SKU 维度聚合并在返回中携带 `sku_id`/`sku_name`；`scripts/backfill_sku.py` 一次性回填历史账本。dev 库用 `scripts/dev_sync_schema.py` 补齐列 + `alembic stamp 5242218be814` 对齐基线，新增迁移 `80bd7e0fc1ac` 接在 `008aca35a3e6` 之后。**⚠️ 迁移链教训**：仓库已收敛为单条全量基线 `5242218be814`，写新迁移前务必先 `alembic heads` 确认头，勿凭空造 00x 孤儿链（我曾误造 004/005 已被删）；SQLite 上改 FK 需用 `batch_alter_table`。
- **P1-C 金额用 float — 已收口 ✅（2026-07-12）**：`InventoryRecord`/`PurchaseItem`/`BatchLifecycle`/`ProductSKU`/`ProductCategory`/`CurrentInventory`/`UnitConversion`/`Supplier`/`SupplierProduct`/`ProductSpecification`/`SaleOrder`/`SaleOrderItem`/`Payment`/`DailySettlement`/`Reconciliation` 的 Numeric 列 Mapped 已改为 `Decimal`；`app/services/offline_sync.py` 已改用 `Decimal(str(...))` 构建记录；`app/routers/purchase.py`/`voice.py`/`pos.py` 的核心写入路径已去掉中间 `float()` 计算，改为 Decimal 运算（仅在 JSON 返回时转 float 序列化）；`app/services/batch.py` 的 FIFO 数量计算也已改为 Decimal。模型与路由/服务层均无 float 精度传递。
- **P1-D 供应商/往来账 — 已收口 ✅（2026-07-12）**：新增 `app/services/accounts_service.py`（SupplierPayable/CustomerReceivable 写入与余额查询）；`purchase.py` confirm 按 payment_status 写应付流水；`voice_parser.py`/`voice.py` 识别交易对手与赊账/回款并写应收流水；新增 `app/routers/accounts.py` 查询接口。
- **POS + 日结对账 — 已落地 ✅（2026-07-12）**：新增 `app/models/pos.py`（SaleOrder/SaleOrderItem/Payment/DailySettlement/Reconciliation）；新增 `app/routers/pos.py`（创建订单、支付、日结关闭/查询）。
- **离线同步 client_id 幂等 — 已落地 ✅（2026-07-12）**：`inventory_records`/`voice_logs` 加 `client_id`/`client_reference` 列与索引；`OfflineSyncItem` schema 加 `client_id`/`client_reference`；`offline_sync.py` 透传；`voice.py` upload/parse-text 接受 `client_id`。
- **AI 行动追踪 — 已落地 ✅（2026-07-12）**：新增 `app/models/ai_action.py` 与 `app/routers/ai_actions.py`；`advisor.py` 生成每日建议时同步创建 `AIAction`，实现建议→可执行任务的闭环。
- 配套（CORS/Alembic）：CORS `*` 已在 main.py 降级 + 生产白名单 critical 告警；`main.py` **已统一走 Alembic** ✅（2026-07-12 轮收口：`init_db` 优先 `alembic upgrade head`，注入 app engine + `asyncio.to_thread` 规避 running-loop 冲突；**本轮补存量库自动 stamp 基线守卫**，dev 库启动不再回退 create_all；debug=False fail-fast；全量基线 `5242218be814` 已从空库独立建全部 26 表）。

## 团队能力现状（2026-07-11 评审）
- 会写规范（Pydantic schema 好、pytest 13 文件、ruff+mypy 已配、Alembic 已接），但**规范未强制落地**：`response_model=dict` 绕过自有 schema、裸 `dict` 入参回潮、路由层过胖、N+1 查询。
- 提升路线（详见 `千摊智脑-代码质量评审与团队提升方案.md`）：阶段0 安全地基 → 阶段1 契约一致性 → 阶段2 路由瘦身+数据层纪律 → 阶段3 质量门禁工程化（pre-commit + CI + review checklist）。

## 约定
- 交付物放 `E:\千摊\` 根；参考代码标注 REFERENCE ONLY，合入前需 code review。
- 用户偏好：中文沟通、指令短、习惯"视频标记+实操"、手机端访问（文件定位困难，优先用 present_files 预览）。
