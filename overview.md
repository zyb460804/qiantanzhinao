# 千摊智脑 — 全量功能实施总览

> **定位**：从进货、称重、收款、库存到日结，靠说话就能完成经营的生鲜摊位智能账本。
> **技术栈**：FastAPI + SQLAlchemy 2.x Async + 微信小程序 + 树莓派边缘端 + YOLO/Prophet
> **测试基线**：109 passed, 1 skipped（pytest）

---

## 一、已实施功能全览（7 轮迭代）

### 第 1 轮：P0 基础功能

| 功能 | 后端 | 小程序 |
|------|------|--------|
| 全局错误处理与统一状态 | — | `app.js`：onError/onPageNotFound/onUnhandledRejection + request() 结构化错误 + uploadFile + showToast 防重复 + checkApiHealth |
| 经营记录撤销/修改/作废 | `inventory.py`：is_voided/voided_at/void_reason + `batch.py`：rollback_batch_on_void + `voice.py`：void/edit 接口 | `record-card` 组件增强 + `voice.wxml` 撤销按钮 |
| 库存盘点与校准 | `stocktake.py` 模型 + `inventory.py`：start/submit/complete 盘点（SQL 聚合 + 差异调整 + 幂等） | `pages/stocktake/`：双 Tab + 快捷调整 + 差异原因 |
| 首页增强 | — | 6 卡操作区（快速记账/拍照识货/库存盘点/今日建议/采购清单/经营报告） |

### 第 2 轮：P1 核心功能

| 功能 | 后端 | 小程序 |
|------|------|--------|
| 商品视觉识别 | `vision.py`：3 模式（边缘端/演示/占位）+ 图片校验 + 反馈接口 | `pages/vision/`：拍照→压缩→识别→确认入库 |
| 经营日报/周报 | `reports.py`：日报/周报/趋势/排行（估算毛利口径） | `pages/report/`：三模式切换 + Canvas 趋势图 + AI 总结 |
| AI 采购清单闭环 | `purchase.py`：生成→编辑→确认入库（幂等+偏差追踪） | `pages/purchase/`：可编辑清单 + 偏差显示 + 一键入库 |

### 第 3 轮：计划书对照审计修复

| 领域 | 修复内容 |
|------|----------|
| 数字孪生利润口径 | `twin.py`：利润改为 `estimated_gross_profit`，六维风险真实计算（HHI 品类集中度、现金流失衡、7日交易笔数） |
| 讯飞 ASR 真实服务 | `asr_iflytek.py`：HMAC-SHA256 + WebSocket 流式识别 + 方言 + 降级 HTTP |
| 天气服务 | 中国法定节假日（2025-2026）+ 数据来源标识（qweather/mock/cache） |
| YOLO 训练框架 | `train_yolo.py` + `evaluate_yolo.py` + ONNX 导出；`inference.py` ONNX Runtime 真推理（无权重降级 demo） |
| Prophet 预测服务 | `forecast.py`：<7天规则 / 7-30天移动平均 / >30天 Prophet + 天气节假日回归 / 自动回退 |
| 测试补全 | 新增 29 测试用例（reports/purchase/stocktake/vision），多商户隔离覆盖 |

### 第 4 轮：Alembic 启动统一 + 鉴权落地

| 领域 | 内容 |
|------|------|
| Alembic 启动建表 | `init_db()` 改走 `alembic upgrade head`，存量库自动 stamp 接管，彻底废弃 `create_all` |
| 微信登录 + JWT | `security.py`：`get_current_merchant` + PyJWT + code2session + 令牌吊销；auth/login/refresh/logout/me 接口 |
| 鉴权全量覆盖 | 8 个路由模块身份均来自 Bearer token，不再信任客户端 merchant_id |
| 测试 | 新增 `test_auth_api.py`（5 用例：登录/鉴权/刷新/吊销/隔离），总测试数 100→105→109 |

### 第 5 轮：Edge 鉴权 + 全局时区统一

| 领域 | 内容 |
|------|------|
| Edge 鉴权收口 | `edge.py` 接入 JWT 依赖，body merchant_id 仅作一致性校验（不一致→403） |
| 时区工具 | 新建 `core/timezone.py`：`utc_now`/`local_now`/`utc_today_start`/`local_today_start`/`parse_iso_datetime` |
| 时区规范 | 服务端时间戳（created_at/voided_at 等）→ UTC；event_time 暂保留本地（待数据迁移后统一 UTC） |
| 影响范围 | 16 个文件统一时区调用，修复 `reports.py` today_start 混用 bug |

### 第 6 轮：P1-C Decimal + P0-B SKU + P1-D 账户

| 领域 | 内容 |
|------|------|
| 金额 Decimal 化 | `purchase.py`/`voice.py`/`pos.py`/`batch.py` 核心路径全部 Decimal 运算，消灭 float 中间精度丢失 |
| SKU 查询层切换 | `inventory.py` 库存查询 + `batch.py` 批次 FIFO + `pos.py` 销售扣减均优先按 sku_id，无 SKU 回退 product_id |
| 供应商/往来账 | `accounts_service.py`：应付/应收写入与余额查询；`purchase.py`/`voice.py` 自动生成往来流水 |

### 第 7 轮：一次性收尾

| 领域 | 内容 |
|------|------|
| POS 收银 + 日结对账 | `pos.py` 模型（SaleOrder/SaleOrderItem/Payment/DailySettlement/Reconciliation）+ 路由（创建订单→支付→日结） |
| 离线同步 client_id | `inventory_records`/`voice_logs` 加 `client_id`/`client_reference` 列与唯一索引，幂等批量入账端点 |
| AI 行动追踪 | `ai_actions.py`：AI 建议自动生成可执行任务，采纳/完成追踪 |
| 修复 | `reports.py` today_start NameError、`inventory.py` coalesce 兼容、`purchase.py` AttributeError |

---

## 二、当前架构全景

```
qiantan-brain/
├── backend/           # FastAPI + SQLAlchemy 2.x Async
│   ├── app/
│   │   ├── core/      # security, timezone, config
│   │   ├── models/    # 26 张业务表（inventory, voice, batch, product, purchase, stocktake, audit, pos, accounts, ai_action…）
│   │   ├── routers/   # 15 个路由模块（advice, inventory, voice, vision, purchase, reports, twin, pos, accounts, ai_actions, edge, auth…）
│   │   └── services/  # advisor, batch, behavior, forecast, experience_cloud, lifecycle, offline_sync, accounts_service, asr_iflytek…
│   ├── migrations/    # Alembic 基线 5242218be814（26 张表，空库可独立建库）
│   └── tests/         # 17 个测试文件，109 passed
├── miniprogram/       # 微信小程序（17 个页面）
│   ├── pages/         # index, voice, advisor, vision, report, purchase, stocktake, dashboard, pos, sandbox, profile…
│   ├── components/    # record-card, mascot, icon…
│   └── utils/         # offline-sync, storage, recorder
├── edge/              # 树莓派边缘端
│   ├── vision/        # ONNX 推理 + picamera2 拍摄
│   └── weighing/      # HX711 称重
├── ml/                # YOLO 训练/评估 + Prophet 预测 + 合成数据
└── datasets/          # 15 类商品分类定义
```

---

## 三、关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 鉴权 | JWT (PyJWT) + 微信 code2session | 身份只来自 token，绝不信任客户端 merchant_id |
| 数据库迁移 | Alembic 启动优先，存量库自动 stamp | 统一迁移源，废弃 create_all |
| 时区 | 服务端时间戳 UTC，event_time 暂本地 | 兼容历史数据，待迁移后全栈 UTC |
| 金额 | Decimal（NUMERIC 列）+ Decimal 运算 | 消灭 float 精度丢失 |
| 库存 | 批次 FIFO + 作废回滚 | 真实账本核心机制 |
| 利润口径 | 估算毛利 = 收入 − FIFO 已售成本 | 不与现金流混淆 |

---

## 四、测试覆盖

```
109 passed, 1 skipped in ~3s

测试文件（17 个）：
test_advice_api / test_auth_api / test_batch / test_edge_auth / test_env_engine
test_experience_cloud / test_offline_sync / test_purchase_api / test_reports_api
test_simulator / test_stocktake_api / test_unit_service / test_vision_api
test_voice_api / test_voice_parser / test_voice_parser_v2
```

---

## 五、本轮收口更新（2026-07-12）

- 校准 `qiantan-status-verified.md`：Prophet 在线接入、差分隐私、Dashboard 7/30 + 客单价曲线 3 项已代码化完成，从「真实缺口」移入「已落地能力」。
- 补齐/更新核心文档：`docs/test-report.md`、`docs/deployment-guide.md`、`docs/hardware-guide.md`、`docs/model-training.md`、`docs/demo-script.md`、`docs/privacy-design.md`、`docs/asr-integration.md`、`docs/experiment-report.md`。
- 生成本轮交付件：`千摊智脑-全量收口总览.md`。

## 六、后续路线图

详见 `qiantan-status-verified.md` 的「真实缺口」部分。核心待推进：

1. **YOLO 真实训练权重** — 采集/标注训练图片，产出 `best.pt`/ONNX
2. **树莓派实机最小闭环** — camera → ONNX → HX711 → 本地 SQLite → 上传
3. **商户实地测试 + 消融实验**
4. **补外部凭证** — 讯飞 ASR / 和风天气 API Key
5. **P2 功能** — 消息通知、离线同步端到端、Web 经营后台、AI 经营问答
