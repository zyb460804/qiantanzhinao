# 千摊智脑 第一性原则审查 + 对抗性测试报告
> ⚠️ 历史快照：本文数字与代码引用为撰写当时的状态（2026-08-16 删减收口后已有变动，如经验云/offline-media/voice_parser_v2 等已删除），现状以根 README 与 [docs/README.md](README.md) 为准。

> 日期：2026-08-16
> 方式：只读代码审查（后端/小程序/管理后台三个维度）+ 全量 pytest + 现有对抗性测试实跑
> 结论：核心经营主链路扎实；主要问题集中在「安全权限门禁」「SaaS 多租户未闭环」「演示壳/死代码」「前端静默截断/静默失败」。

---

## 0. 测试证据

运行：`cd qiantan-brain/backend && .\.venv\Scripts\python.exe -m pytest tests/ -q --tb=short`

```
865 tests collected
859 passed
6 failed
```

6 个失败全部来自 `tests/test_adversarial_audit_2026_08_16.py`，均为漏洞确认：

| # | 漏洞 | 实际结果 |
|---|------|---------|
| 1 | 普通市场成员可伪造他人巡检结论 | 200，应为 403 |
| 2 | 巡检可指向市场外商户 | 200，应为 403 |
| 3 | 普通成员可代他人投诉 | 200，应为 403 |
| 4 | 普通成员可处置他人投诉 | 200，应为 403 |
| 5 | owner 可自封 `market_admin` 并创建市场 | 200，应为 403 |
| 6 | 带幂等键登录时 JWT 明文落库 | token 出现在 `IdempotencyRecord.response_body` |

---

## 1. 后端模块第一性原则结论

### 1.1 核心且合理（应保留）

- `voice`：语音/文字记账，真实落库并联动库存与往来账。
- `inventory`：库存/盘点/预警，真实聚合 SQL。
- `pos`：开单/支付/退款/日结，幂等与资金流覆盖完整。
- `purchase`：采购/验收/入库/付款，业务闭环真实。
- `operations`：报损/临期/客户往来/导出。
- `expense`：费用/月报/发票。
- `reconciliation`：渠道账单对账。
- `food_safety` + `batch`：批次追溯/锁定/召回，状态机真实。
- `staff`：员工 PIN/权限/限流。
- `auth`：登录链路真实，但租户绑定缺失（见 P0）。

### 1.2 死代码/未接线（建议删或补接线）

| 服务 | 证据 | 建议 |
|------|------|------|
| `services/backup.py` | 全仓无 app 内引用 | 接入 worker 或删除 |
| `services/cache.py` | 全仓无 app 内引用 | 删除 |
| `services/feature_engineering.py` | 全仓无 app 内引用 | 删除或移入实验目录 |
| `services/food_safety.py`（HACCP） | 只被测试引用，路由未用 | 接入 food_safety 路由或删除 |
| `services/audit_archiver.py` | 无调用，worker 注释 TODO | 接入 worker 或删除 |
| `services/supplier_scorer.py` | 只被测试引用，catalog 另有实现 | 统一评分服务后删除一套 |
| `services/voice_parser_v2.py` + `unit_service.py` | 只被测试引用，线上用 v1 | v1/v2 二选一 |
| `core/rls.py` | 表名/列名与真实 schema 不匹配 | 不修就删，避免伪隔离 |

### 1.3 演示/Mock 冒充真实能力

- `vision.recognize`：无模型返回 placeholder/随机结果。
- `weather`：无 Key 时返回 mock 且 environment 路由会落库。
- `tenant/portal.py`：无租户时返回写死的免费版/999 配额。
- `daily-checklist`：冷柜/清洁恒为 pending。
- `device.price-display/sync`：只改 DB 状态，无实际推送。
- `admin/operations.py` dead-letter retry：只改状态，不重放任务。

---

## 2. 小程序页面第一性原则结论

### 2.1 核心页面

`index / voice / inventory / advisor / profile / pos / purchase / stocktake / ops`

### 2.2 应清理/收敛的页面

| 页面 | 判断 | 理由 |
|------|------|------|
| `pages/styleguide/styleguide` | 开发残留/孤儿 | 注册于 app.json，但全仓无任何跳转入口 |
| `pages/insight/insight` | 演示壳 | 只有展示卡片，没有采纳/改价/加入采购等动作 |
| `pages/sandbox/sandbox` | 演示壳 | 试算结果无法落地到采购/改价 |
| `pages/vision/vision` | 公测占位 | 明确“识货公测中”，模型未达生产可信度 |

### 2.3 页面设计不合理点

1. **分析页职责重叠**：advisor / dashboard / report / insight / sandbox 均展示建议/KPI，边界不清。
2. **“我的”页过载**：单页 17 个工具 + 快照 + 设备 + 设置 + 帮助。
3. **加载失败伪装成空状态**：inventory/catalog/supplier/finance/ops 失败后显示“暂无数据”。
4. **敏感管理页无前端权限门控**：员工也能看到员工管理/租户中心/财务/导出入口。
5. **首页全失败时锁死离线入口**：语音/POS/采购/盘点本可离线使用。
6. **finance 与 ops 导出互相踢皮球**。
7. **采购待办数据源不一致**：本地 `purchaseDraft` 与后端 `/purchase/from-advice` 两条通道。

---

## 3. 管理后台第一性原则结论

### 3.1 核心合理页面

`Dashboard / Tenants / TenantDetail / Plans / AuditLog / Admins`

### 3.2 主要问题

| 问题 | 证据 | 优先级 |
|------|------|--------|
| Devices / AiOps / Usage 只加载前 100 条 | `Devices.jsx:45`、`AiOps.jsx:27-31`、`Usage.jsx:27` | P1 |
| Tenants 批量导出按钮无行为 | `Tenants.jsx:264-268` | P1 |
| Subscriptions / Invoices 缺新建/升级/开票入口 | 后端已有接口但前端无入口 | P1 |
| 死信队列后端完整，前端无页面 | `admin/operations.py:817-927` | P1 |
| TenantDetail 编辑/风险 Tab 未按权限隐藏 | `TenantDetail.jsx:263-266,753-755` | P1 |
| Monitoring 订阅服务检查恒为 normal | `admin/operations.py:747-753` | P2 |
| Onboarding 与 Tenants 新建 Modal 重复 | `Onboarding.jsx:100-122` vs `Tenants.jsx:302-344` | P2 |
| Dashboard 活动流失败静默降级 | `Dashboard.jsx:92` | P2 |

---

## 4. 优先级整改建议

### P0（先修，1-2 天）

1. `market_admin.py` 巡检/投诉/解决统一加角色校验，并校验目标商户归属。
2. `staff.py` 禁止 owner 自助创建/提升 `market_admin`。
3. `idempotency_middleware.py` 对登录/刷新等敏感响应跳过缓存或脱敏。
4. 多租户收口：新商户注册绑定默认租户；`STRICT_TENANT_REQUIRED=True`；业务路由统一挂租户/订阅/套餐/配额门禁。
5. 删除或重做 `core/rls.py`，避免“伪隔离”。

### P1（2-3 天）

6. 删死代码或补接线：backup/cache/feature_engineering/HACCP/audit_archiver/supplier_scorer/voice_parser_v2/unit_service。
7. 演示逻辑生产 fail-closed：vision 无模型 503、weather 无 Key 不落库、tenant 无租户 403。
8. 前端补权限门控与错误态；首页断网保留离线入口。
9. `reports/daily` 补 `order_count`，修复客单价口径。
10. 管理后台 Devices/AiOps/Usage 改服务端分页；Tenants 补导出或删按钮；Subscriptions/Invoices 补计费操作；新增死信队列页面。

### P2（体验优化）

11. 统一分析页边界：dashboard=今日实况、report=历史报表、advisor=可执行建议；insight 并入 advisor 或补执行按钮。
12. 移除 styleguide 生产入口；统一 dashboard 命名。
13. finance/ops 导出合一；catalog/supplier 比价去重。
14. 摊主自定义低库存阈值；追溯二维码域名配置化；设备价目屏真实推送；媒体上传增加受控下载。

---

## 5. 一句话结论

> 千摊智脑的“账本主链路”是真实且合理的；不合理的是“竞赛/演示壳”“未接线死代码”“SaaS 门禁未上生产”和“前端静默截断/静默失败”。下一步应先修 6 个对抗性漏洞与多租户收口，再清理死代码和演示壳，最后补管理后台运营闭环。

---

## 6. 整改完成状态（2026-08-16 并发实施后）

### 6.1 安全 P0（已完成）
- `market_admin.py`：巡检/投诉/解决增加 `market_admin/tenant_admin/platform_admin` 角色校验；目标商户必须属于该市场；补充 Pydantic 请求模型；修复 `resolved_at`。
- `staff.py`：owner 禁止创建/提升 `market_admin`，仅 `tenant_admin/platform_admin` 可授予。
- `idempotency_middleware.py`：登录/刷新/staff 登录/admin 登录等敏感路径仍保留幂等记录但不再缓存明文 JWT 响应体。
- `tests/test_adversarial_audit_2026_08_16.py`：已更新为新的安全期望，**11/11 通过**。

### 6.2 多租户收口（已完成）
- `auth.py`：新商户登录自动绑定默认租户；老商户无租户时幂等补绑。
- `tenant_context.py`：`STRICT_TENANT_REQUIRED=True`，未绑定租户访问租户自助接口返回 403。
- 新增 `tests/test_tenant_closure_2026_08_16.py`，覆盖新商户绑定/老商户补绑/默认租户幂等/未绑定 403。

### 6.3 小程序收敛（已完成）
- 移除 `pages/styleguide/styleguide` 生产注册。
- 新增 `utils/permissions.js`，员工身份按权限过滤工具入口。
- inventory/catalog/supplier/finance/ops 增加加载失败错误态与重试。
- 首页断网时保留“记一笔/收银/采购/盘点”离线入口。
- insight 定价建议加“去处理”，进货建议加“去采购”；sandbox 结果加“去采购”。
- 修复 finance/ops 导出循环、摊位设置静默失败、advisor 静默失败、dashboard 命名。

### 6.4 后端死代码清理（已完成）
- 删除：`backup.py`、`cache.py`、`feature_engineering.py`、`food_safety.py`（HACCP 服务）、`supplier_scorer.py`、`voice_parser_v2.py`、`unit_service.py`、`core/rls.py` 及对应测试。
- 新增 `services/supplier_scoring.py`，catalog 评分收敛为单一实现。
- `audit_archiver.py` 接入 `worker.py` 每日 06:00 归档任务。

### 6.5 管理后台闭环（已完成）
- Devices/AiOps 服务端分页与过滤；Usage 租户搜索/分页并读取 `?tenant_id=`。
- Tenants 批量导出接通并使用 `ConfirmWithReason`。
- Subscriptions 新增新建/升级；Invoices 新增手工开票/从订阅生成/作废。
- 新增 DeadLetters 页面 + 路由 + 菜单。
- TenantDetail 编辑/风险 Tab 权限门控；Monitoring 订阅服务检查改为真实状态。
- AdminLayout 菜单分组。

### 6.6 最终验证
- 后端全量：`797 passed`（`pytest tests/ -q`）。
- 前端小程序：改动 JS `node --check` 通过，`app.json` 合法。
- 管理后台：`npx eslint src --max-warnings=0` 通过（沙箱内 `npm run build` 受 EPERM 限制未能产出产物，代码静态校验已通过）。

