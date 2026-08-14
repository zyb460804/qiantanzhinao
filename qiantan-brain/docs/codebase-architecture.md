# 千摊智脑（QianTan Brain）架构文档

> 面向马路摊贩的多租户 SaaS 经营系统：AI 记账、生鲜动态定价、食品安全合规、离线优先。
> 本文档基于 2026-08-14 对 `fix/security-correctness-systemic-audit` 分支的全量源码审计整理，
> 所有结论均有源码出处（`文件:行号`）。CI/pre-commit 的 YAML 内文未逐行核验处已标注。

## 1. 总览

```
┌─ 客户端层 ──────────────────────────────────────────────────┐
│ 微信小程序（24页，双身份，读写分级重试，离线队列 UI）              │
│ admin-web 平台后台（React 19 + antd，8 页，租户/套餐/设备运营）  │
├─ 接入层 ────────────────────────────────────────────────────┤
│ 商户 JWT + 员工权限（require_permission）                      │
│ 设备签名防重放（X-Api-Key/Timestamp/Nonce + scope）            │
│ SaaS 四道门禁：租户 → 订阅 → 套餐功能 → 配额（403/402/403/429） │
├─ 业务层 ────────────────────────────────────────────────────┤
│ POS → 渠道对账 → 日结封账    采购验收 → 批次 FIFO 成本          │
│ 食品安全 CCP/NCR/锁批次      语音记账（讯飞 ASR + 解析器 v2）    │
├─ 智能层 ────────────────────────────────────────────────────┤
│ Prophet 预测 / 库存优化 / 动态定价(Q10 衰减) / 统计异常检测      │
│   → Recommendation → AIAction(pending) → 人工执行 + 审计       │
├─ 数据层 ────────────────────────────────────────────────────┤
│ InventoryRecord 统一账本 + 幂等键唯一约束（exactly-once 核心）  │
│ Alembic 通配导入防漏表    APScheduler Worker（纯计费生命周期）   │
└─ 边缘层 ────────────────────────────────────────────────────┘
  树莓派5（YOLO+HX711 称重+SQLite 离线队列）  电子价签  OTA 灰度固件
```

## 2. 仓库结构

| 目录 | 内容 |
|---|---|
| `backend/app/` | FastAPI 主应用：`main.py`（API 入口）、`worker.py`（定时任务）、`routers/`（32 个）、`services/`（32 个）、`models/`、`schemas/`、`core/`（安全/租户/时区/配额） |
| `backend/admin-web/` | React 管理后台 |
| `backend/migrations/` | Alembic（`env.py` 通配导入 `models/__init__.__all__`，防新增表漏迁移） |
| `backend/tests/` | 55 个测试文件 ≈549 用例（API + 算法 + 对抗性 + 幂等/锁专项） |
| `miniprogram/` | 微信小程序：`app.js`（运行时中枢）、24 页面、6 组件、utils 工具层 |
| `edge/` | 树莓派边缘控制器（vision/ + weighing/ + 离线 SQLite 队列） |
| `ml/` | YOLO 训练/评估、Prophet 预测、合成数据生成 |
| `reference/offline-sync/` | 离线同步教学参考实现（与生产版协议不同，见 §5） |
| `scripts/` | 质量守卫：`check_large_files.py`、`wxss_lint.py`、种子数据 |

## 3. 核心设计原则

1. **一切皆账本**：POS、语音、离线队列、边缘设备四条录入通道全部收敛到
   `InventoryRecord` + 批次表。信任链 = 客户端 UUID 幂等键 → DB 唯一约束 →
   savepoint 隔离 → 死信兜底。网络任意重试，同一笔账最多落一条。
2. **AI 只建议，人来扣扳机**：四个引擎只产出 `Recommendation → AIAction(pending)`，
   执行必过人工点击，每次执行写 `PriceHistory`/`AuditLog`；
   唯一例外 `lock_batch`（食品安全风险可直接锁批次）。
3. **三个闭环**：钱（收款→渠道对账→日结封账）、租（Worker 停服→门禁拦截）、
   食（CCP 超标→NCR→锁批次）。

## 4. 业务链路

### 4.1 POS → 对账 → 日结

- `pay_sale_order`（`routers/pos.py:577`）：日结锁检查 → `SELECT FOR UPDATE` 行锁 →
  `transaction_id` 去重（重复返回 `duplicate:true`）→ Decimal("0.01") 金额校验 →
  组合支付/部分支付 → 赊账回款（幂等键 `sale-repay:{payment_id}`）。
- `_auto_reconcile_after_payment`（`routers/pos.py:1227`）：支付后 best-effort 触发，
  整段 try/except——对账失败不阻塞收款；仅当天该渠道已导入账单才真正对账。
- `reconcile_task`（`services/reconciliation.py:375`）：对账窗口按本地日界
  （CST UTC+8）切分后转 UTC 比对；两级降级匹配（transaction_id → order_no，
  候选取金额相等者）；四种差异（matched/amount_mismatch/channel_only/system_only）；
  重跑幂等且保留 resolved/ignored 历史差异；`|diff|≤0.01` 且无新差异才 balanced。
- `close_daily_settlement`（`routers/pos.py:1507`，需 `daily_settle` 权限）：
  拒绝未来日期/重复关闭；一次写 `DailySettlement`+`Reconciliation`+`AuditLog`；
  封账后当日开单/收款均被日结锁拦截（`test_settlement_lock.py`）。

### 4.2 离线同步（生产协议）

- 小程序 `utils/offline-sync.js`：`wx.storage` 持久化队列（`qt_offline_queue`，
  上限 1000 条，TTL 7 天）；`enqueue` 强制 `idempotency_key`（客户端 UUID v4）；
  状态机 pending→syncing→synced/failed(重试≥5 次)，指数退避 2s×2ⁿ 封顶 32s，
  仅 network/server 错误退避；`created` 与 `duplicate` 均视为成功。
- 后端 `services/offline_sync.py`：批量 `POST /inventory/offline-sync`，每条
  `begin_nested()` savepoint 隔离；先查 `(merchant_id, idempotency_key)` 去重，
  并发 `IntegrityError` 重查判 duplicate；商品三级兜底解析（id→名称→自动建
  「现金收款」类目）；purchase 建批次、sale/waste 走 FIFO 消耗（不足则回滚进死信）；
  失败项落 `DeadLetterEvent`（幂等去重，max_retries=3，+5min 重试）。
- 边缘端 `edge/main.py`：另一套协议——设备签名头（Nonce 防重放）投递
  `/edge/ingest/device`，`event_id` 唯一约束去重；本地 SQLite 队列 30 天清理。
- 注意：`reference/offline-sync/` 是教学参考（client_id 键、静态加密、conflict 态），
  生产版协议以本节为准。

### 4.3 语音记账

录音（16k/16bit/mono）→ 讯飞 ASR v2（`services/asr_iflytek.py`：HMAC-SHA256 签名
URL、1280B/帧流式、显式 `proxy=None` 防代理劫持、HTTP 降级、无凭证时空串回退文本
输入；方言 pd 参数与前端 profile 对齐）→ 解析器 v2（`services/voice_parser_v2.py`：
供应商抽取、包装/净重分离、别名→SKU→标准单位换算、`总额/净重`自动单价、
sha256 派生幂等键、未知事件强制 `needs_confirmation`，置信度
`1−0.1×缺失−0.05×猜测`）→ `VoiceLog` 状态机（pending→parsed→confirmed/voided，
支持 correct/edit/void；修正字段白名单 `extra="forbid"`，见 `schemas/voice.py`
C-1/L5 修复注释）→ confirm 落账 + FIFO 批次消耗。

## 5. AI 决策层

- **编排器** `build_daily_advice`（`services/advisor.py:269`）：环境因子 + 商户行为画像
  + 逐商品（库存/7d/30d 均线 → Prophet 预测（失败回退规则引擎）→ 库存优化 →
  画像个性化）→ 当日幂等落 `Recommendation`（GET 有副作用 + 客户端 GET 自动重试，
  按 `商户×商品×当天` 去重更新）→ 两阶段 flush 生成 pending `AIAction`。
- **动态定价**（`services/dynamic_pricing.py`）：Arrhenius Q10 质量衰减（温度+10°C
  衰减×2，半衰期=货架期/3）；品类货架期与弹性表；策略自动选择（关门≤2h/质量<0.3
  →出清，覆盖>3 天→库存驱动，双信号→组合，默认按货龄）；阶梯折扣（质量 0.9 全价
  →0.3 以下五折）；底价 `成本/(1−毛利率)`，出清底价 `max(成本×0.7, 原价×10%)`；
  三档激进度（0.6/1.0/1.5）与折扣硬上限。
- **异常检测**（`services/anomaly_detector.py`）：z-score / 修正 z-score（中位数+MAD）/
  IQR / 移动均线 / 季节性 / 零销量 / 数据错误 七检测器并联；另有断货风险与积压检查。
- **库存优化**（`services/inventory_optimizer.py`）：安全库存 z·σ·√LT（95% 服务水平）、
  再订货点、周期订货量（含在途扣减、提前期波动版本）。
- **执行链**（`routers/ai_actions.py`）：price（改价+PriceHistory）、purchase（生成
  采购单）、clearance（批量降价）、lock_batch（锁批次）；`executed_by` 只取 JWT
  注入（防伪造）；每次执行写 AuditLog。数字孪生 `routers/twin.py` 为只读镜像层
  （dashboard/库存镜像/经营镜像/六维风险雷达，FIFO COGS 优先、30 天均价兜底）；
  what-if 仿真在 `/advice` + 定价引擎 `simulate()`。

## 6. 多租户 SaaS

- `core/tenant_context.py`：ContextVar 存 tenant_id，请求中间件入口清零；
  商户 API 由 JWT→Merchant 自动注入，管理后台显式路径传入。
- 四道门禁依赖链：`require_active_tenant`(403) → `require_active_subscription`(402)
  → `require_plan_feature`(403) → `require_quota_check`(429，先检查后记账)；
  工厂糖 `PlanFeature("pos")` / `QuotaCheck("api_calls")`。
- 行级隔离为**约定式**：所有查询显式带 `WHERE tenant_id/merchant_id`，
  非数据库 RLS。
- ⚠️ `STRICT_TENANT_REQUIRED = False`（`core/tenant_context.py:47`）：
  迁移过渡期，未绑定租户仅告警放行，上线前须切 True。
- 通用状态机 `services/state_machine.py`：Tenant/Subscription/Invoice/AIAction
  转移表 + 409 校验，禁止路由直接赋值 status。

## 7. 后台 Worker（`app/worker.py`）

APScheduler AsyncIOScheduler，每 Job 独立 session，可选 Redis 分布式锁。
全部为计费/生命周期任务（AI 与对账均在请求路径按需执行）：

| 任务 | 频率 | 逻辑 |
|---|---|---|
| check_trial_expiry | 每小时 | 试用到期→有订阅转 active 否则 expired |
| check_subscription_expiry | 每日 02:00 | 过期→past_due→15 天→expired→租户 suspended 停服 |
| generate_invoices | 每日 03:00 | 自动续费订阅预生成账单（按周期幂等） |
| reset_monthly_quotas | 每月 1 日 | 滚动窗口设计，无需物理重置（空操作） |
| clean_expired_tokens | 每日 05:00 | 吊销 Token 30 天软删除 |

停服闭环：Worker 置 suspended → 门禁①拦截 → 欠费即全面停用，业务路由零改动。

## 8. 食品安全（`services/food_safety.py`）

简化版 HACCP，6 个 CCP：冷藏温度（分品类 4~10°C）/ 热柜（>60°C）/ 加工时间
（熟食<4h，环境>32°C 收紧 2h）/ 清洁消毒 / 来源可溯 / 交叉污染。
超标读数自动生成 NCR（按程度分级）；五维评分卡（25+25+20+15+15）映射 A~F；
`check_expiry` 与动态定价共用同一质量衰减模型。
⚠️ `CATEGORY_SHELF_LIFE` 在 `food_safety.py` 与 `dynamic_pricing.py` 各存一份（DRY 债）。

## 9. 边缘与设备（`edge/` + `models/device.py`）

- 树莓派控制器：摄像头→YOLO→HX711 称重→SQLite 离线队列→签名投递（可无硬件仿真运行）。
- 设备注册表 + **电子价签** `PriceDisplay`（每商户每 SKU 唯一，价格来源
  manual/ai_discount/clearance，同步状态机）——AI 改价可直达摊位价签屏。
- OTA 固件：SHA-256 校验 + `rollout_percentage` 灰度 + 最低硬件版本门槛；
  设备端模型版本上报与远程日志。

## 10. 小程序端（`miniprogram/`）

- **环境隔离**（`config/api.js resolveApiBase`）：develop 允许 storage 覆盖；
  trial/release **忽略 storage**（防开发覆盖泄漏进签名包）且强制 HTTPS，校验失败
  停止全部网络请求并弹配置错误。
- **双身份**：owner token + staffToken（PIN 登录，staff 优先）；员工 token 过期
  强制退出身份、**不静默回退 owner**；`hasPermission` 前端预判对应后端
  `require_permission`。
- **请求层**（`app.js _requestOnce`）：`code===0` 信封；写请求默认禁自动重试，
  `retrySafe:true` 才重试并自动挂 `Idempotency-Key` 头；读请求 5xx/429 指数退避
  （≤2 次）；登录单飞、请求去重。
- **离线触发点**：启动登录后 / 网络恢复监听 / 页面主动 sync。
- 设计系统 v4.2「市井账本风」，与 admin-web 通过 `theme/tokens.js` CSS 变量同源。

## 11. admin-web（`backend/admin-web/`）

React 19 + antd v5（`@ant-design/v5-patch-for-react-19`）+ react-router +
AuthContext（对接 `/api/v1/admin/auth`）。8 页：Login/Dashboard/Tenants/
TenantDetail/Plans/Devices/Monitoring/AiOps。品牌 CSS 变量运行时注入 `:root`，
与小程序色彩体系对齐。

## 12. 质量门禁

- `.github/workflows/ci.yml`（唯一工作流）、`.pre-commit-config.yaml`。
- 守卫脚本（docstring 注明 pre-commit 与 CI 共用）：
  - `scripts/check_large_files.py`：>1 MiB 拒绝；`.env/.pem/.key` 视为秘密拦截。
  - `scripts/wxss_lint.py`：WXSS 兼容性——禁 `@media`/`:root`/通配符/`vh vw rem`/
    本地 `url()`。
- 后端 55 个测试文件 ≈549 用例；docker-compose 三层（base/dev/prod）。
- ⚠️ 证据边界：ci.yml 具体 job 编排、pre-commit 完整 hook 清单、compose 服务
  拓扑未逐行核验（本文件生成会话无文件读取工具），如需补充请核对后更新本节。

## 13. 已知技术债清单

| 项 | 位置 | 说明 |
|---|---|---|
| 租户强制开关未开 | `core/tenant_context.py:47` | `STRICT_TENANT_REQUIRED=False` 过渡态，上线前必须切 True |
| 货架期常量双份 | `food_safety.py:243` / `dynamic_pricing.py:94` | 应抽公共常量模块 |
| 发票号时间戳取模 | `app/worker.py:226` | 注释自认应改 DB sequence |
| CI 内文未核验 | `.github/workflows/ci.yml` | 见 §12 证据边界 |

## 14. 技术栈速查

后端 Python 3.13 / FastAPI / SQLAlchemy async / Alembic / APScheduler / PostgreSQL
（测试可 SQLite）；小程序原生 + wx.storage；admin-web React 19 + antd v5；
ML YOLO + Prophet；边缘 httpx + websockets；部署 Docker Compose。
