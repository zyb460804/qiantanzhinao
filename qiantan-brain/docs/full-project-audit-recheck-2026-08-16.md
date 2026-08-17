# 千摊智脑 复审报告（2026-08-16）
> ⚠️ 历史快照：本文数字与代码引用为撰写当时的状态（2026-08-16 删减收口后已有变动，如经验云/offline-media/voice_parser_v2 等已删除），现状以根 README 与 [docs/README.md](README.md) 为准。

> 日期：2026-08-16 | 方式：对 2026-07-26 全量审计与 2026-07-27 参赛提升方案中的 P0/P1 发现逐条读码复核，并检查前端/小程序/基础设施
> 性质：**只读静态审查，未修改任何业务代码**（本会话无 shell 环境，未实跑 pytest/ruff）

---

## 0. 结论

7-26 审计后已完成**一轮大规模修复**：P0/P1 中绝大多数已落地，CI / pre-commit / 缺失迁移 / 算法依赖均已补齐，代码质量明显提升。
但仍残留 **1 处 P0 级授权漏洞**（巡检伪造）与 **2 处多租户收口问题**，另有若干新发现（经验云全局预算可被跨租户打爆、审计 IP 未接代理头等）。
整体判断：从「开发环境能跑」到「生产可交付」还剩最后几步。

---

## 1. 已确认修复（对照 7-26 审计）

| 原编号 | 问题 | 状态 | 证据 |
|---|---|---|---|
| P0-1 | market_admin 全部 12 端点零授权 | ⚠️ 部分修复（见 R1） | market_admin.py 新增 _require_market_member + 部分端点角色校验 |
| P0-2 | Login.jsx 明文超管口令 | ✅ | Login.jsx 已无默认账号文案 |
| P1-1 | numpy 未声明 | ✅ | requirements.txt 增加 numpy==2.2.6 / scipy==1.15.3 / pandas==2.2.3 |
| P1-2 | 4 张表缺迁移 | ✅ | 新增 m4b5c6d7e8f9 / n5c6d7e8f9a0；27 个迁移为单一线性链 |
| P1-3 | scipy 未声明 | ✅ | 已入 requirements.txt |
| P1-4 | debug 默认 True 短路安全自检 | ✅ | config.py debug 默认 False；validate_security() fail-closed，dev 豁免也不放过默认 JWT 密钥 |
| P1-5 | debug 下确定性 mock openid | ✅ | 按 app_env 判定 + 进程随机盐 + 生产 503（fail-closed） |
| P1-6 | seed 默认口令 | ⚠️ 部分（见 R3） | 生产强制 PLATFORM_ADMIN_PASSWORD，dev 仍留默认 |
| P1-7 | 员工权限靠 X-Staff-Id 头 | ✅ | staff.py 优先取 token 的 role/staff_id claim；X-Staff-Id 降级为兼容 |
| P1-8 | revoke_token 写 naive 时间戳 500 | ✅ | tz 归一化 + IntegrityError 幂等（与 admin_security 对齐） |
| P1-9 | 追溯 LIKE 通配符注入 | ✅ | 格式白名单 + autoescape=True（lookup_trace / qr-image / feedback 三处） |
| P2 部分 | cloud 无鉴权 | ✅ | cloud.py 三端点均挂 get_current_merchant |
| P2 部分 | ai_actions 异常回传客户端 | ✅ | 500 返回固定文案 + request_id，堆栈只进日志 |
| P2 部分 | 管理员密码策略无调用点 | ✅ | admin/admins.py 的 AdminCreate/AdminUpdate 接入 validate_password + 字节数校验 |
| P2 部分 | voice 录音上传绕过统一层 | ✅ | voice.js 改走 app.uploadFile，处理员工 401 降级 |
| P2 部分 | recorder.js 死代码 | ✅ | voice.js 已 require utils/recorder |
| P2 部分 | profile 离线队列计数崩溃 | ✅ | JSON.parse 有 '[]' 兜底；同步失败有正确 toast |
| P2 部分 | 审计 IP 无视代理头 | ✅（限流）/ ❌（审计日志） | rate_limiter.py 支持 X-Forwarded-For+TRUSTED_PROXIES；core/audit.py 仍取直连（见 R5） |
| 基础设施 | — | ✅ | .github/workflows/ci.yml（含 alembic upgrade head + alembic check 门禁）、.pre-commit-config.yaml、app/core/device_auth.py |

---

## 2. 🔴 残留问题

### R1 🔴 P0 级残余 — 仍可伪造食安巡检 / 处置投诉
文件：backend/app/routers/market_admin.py
- create_inspection：只校验操作者属于该市场（_require_market_member），**无角色校验**，且 body 中 merchant_id **未校验目标商户是否属于该市场** → 任意市场成员可给任意商户写入 pass/fail 巡检结论。
- create_complaint / resolve_complaint：任意市场成员可代他人投诉、处置他人投诉。
- 角色门禁可绕过：create_staff（staff.py）允许创建 role=market_admin 的员工，owner 自助创建后 PIN 登录即可拿到 market_admin 角色 → create_market/register_merchant/create_notice 的角色校验形同虚设。
- 另：resolve_complaint 中 `c.resolved_at = None  # use server time`，模型无 onupdate → resolved_at 恒为 NULL（新发现小 bug）。
- 大量 body: dict 无 Pydantic 校验，缺字段直接 500。

### R2 🔴 多租户隔离未收口
- tenant_context.py:47 STRICT_TENANT_REQUIRED = False（过渡期放行）。
- market 五张表（models/market.py）仍无 tenant_id 列，市场数据无行级隔离。

### R3 🟠 默认口令链路未清干净
- seed_saas.py:57 dev 环境仍默认 Admin123!。
- admin-web/public/saas-architecture.md:339 仍写默认邮箱；根 README:14、摊主故事包.md 仍引导「默认口令见脚本」。
- 全库无 must_change_password 字段、无首登强制改密。

### R4 🟠 经验云查询预算 = 全局共享（新发现，跨租户 DoS）
- experience_cloud.py 的 _check_budget key 是全局字符串（weather_impact_rules / category_benchmarks / top_products）：任一商户调用满 100 次后，**全平台该端点静默返回空数据**直到进程重启。
- ε 口径问题依旧：模块级 dict（重启清零、多 worker 不一致）、stdlib random 噪声源、每次响应恒报 epsilon=1.0 无组合会计。

### R5 🟠 审计日志 IP 仍取直连地址
- core/audit.py:37 仍用 request.client.host（rate_limiter 已升级，audit 未跟上）→ 反代下审计 IP 全是 nginx 容器地址。

### R6 🟠 幂等表仍存响应全文
- idempotency_middleware.py 会把 2xx 响应体完整落库（若客户端给登录请求带幂等键，JWT 会进业务库）。

### R7 🟡 其他
- pos.py:1357 仍硬编码 fee_rate=Decimal("0.006")，绕过 reconciliation._channel_fee_rate 的商户配置（魔法数字另见 payment.py:30 / reconciliation.py:63）。
- models/staff.py:79 员工 PIN 明文存储（注释自认非生产方案）。
- 前端未修：AiOps.jsx 失败静默降级空数据；Devices.jsx / Usage.jsx 只加载前 100 条（离线故障设备可能不可见）；client.js 401 硬跳转无提示无 returnUrl。
- 死代码：miniprogram/utils/offline-media.js（375 行，全库无 require）。
- 超长文件：pos.py 1822 行 / purchase.py 1411 行 / catalog.py 1262 行（超过 800 行红线）。
- req_clean.txt 与 requirements.txt 严重漂移（旧版本号），死文件。
- trace_qr_image 二维码域名硬编码 qiantan.example.com（代码内 TODO，未抽 settings）。
- 数字口径不一致：app.json 注册 25 页（README 称 24）；静态统计 796 个 test_ 函数（7-26 称 549 个测试通过，可能已增长）；251 个端点装饰器（7-27 称 249）。

---

## 3. 建议行动清单

| 优先级 | 动作 | 涉及文件 |
|---|---|---|
| P0（1-2 天） | market_admin 写操作统一角色校验 + 目标商户归属校验 + Pydantic schema；禁止自助创建 market_admin 员工 | market_admin.py、staff.py、models/market.py |
| P0 | 存量 tenant_id 回填 → STRICT_TENANT_REQUIRED=True；market 表补 tenant_id + 迁移 | tenant_context.py、models/market.py、migrations/ |
| P0 | PlatformAdmin 增加 must_change_password + 首登强制改密；清理文档/静态资源默认口令 | models/saas.py、seed_saas.py、admin_security.py、README、saas-architecture.md |
| P1（2-3 天） | 经验云预算改 per-principal + 修 ε 口径；audit IP 接代理头；幂等表对登录响应豁免；pos 费率复用配置 | experience_cloud.py、audit.py、idempotency_middleware.py、pos.py、reconciliation.py |
| P2 | 前端分页加载；删 req_clean.txt 与 offline-media.js；修 resolved_at；二维码域名配置化；统一 README 数字并实跑 pytest 定版 | 前端 + 文档 |

---

## 4. 方法说明

- 全部结论基于对当前工作区代码的静态阅读（read/grep/glob），未执行 pytest/ruff（本会话无 shell）。
- 7-26 审计中标注「未复核」的项（P1-5/P1-6/P1-7/P1-8 等）本次已逐条读码复核。
- 新发现项（R4、resolved_at bug、market_admin 角色门禁可绕过）建议修复前再实跑验证。

---

## 5. 对抗性测试结果（2026-08-16，实跑确认）

测试文件：backend/tests/test_adversarial_audit_2026_08_16.py
运行：`python -m pytest tests/test_adversarial_audit_2026_08_16.py -v`（venv Python 3.13.14 / pytest 9.1.1，3.03s）

### 5.1 漏洞确认（6/6，全部实跑 200 而非期望 403/不落库）

| # | 攻击链 | 结果 |
|---|--------|------|
| A1 | 普通 owner（仅市场成员）POST /market-admin/inspections 给竞争对手写 result=fail | **200 = 伪造巡检成功** |
| A2 | market_admin 的巡检 merchant_id 指向**市场外**商户 | **200 = 目标归属校验缺失** |
| A3 | 普通成员 POST /market-admin/complaints 代他人投诉 | **200 = 代投诉成功** |
| A4 | 普通成员 PUT /complaints/{id}/resolve 处置他人投诉 | **200 = 处置成功** |
| A5 | owner 创建 market_admin 员工 → PIN 登录 → 用自封角色建市场 | **200 = 角色门禁绕过** |
| B1 | 带 Idempotency-Key 的 /auth/wechat-login → token 出现在 IdempotencyRecord.response_body | **JWT 明文落库确认** |

### 5.2 修复/设计确认（5/5 通过）

| # | 验证点 | 结果 |
|---|--------|------|
| C1 | owner 直接建市场（真实 JWT 链路） | 403 ✅ |
| C2 | 未绑定租户商户访问租户自助接口（STRICT_TENANT_REQUIRED=False 过渡期） | 200（与代码一致，留档） |
| C3 | 经验云预算 key 为全局字符串，商户 B 被商户 A 耗尽后首次调用即拒 | 缺陷成立（留档） |
| C4 | trace 通配符 % / _ 被格式白名单拦截（P1-9 回归） | 400 ✅ |
| C5 | 跨租户 register_merchant 仍被 403（C-10 回归） | 403 ✅ |

### 5.3 修复验证清单（修复后重跑本测试文件应全部变绿）

- R1-A1/A2/A3/A4：market_admin.py 的 create_inspection / create_complaint / resolve_complaint 增加与 create_notice 一致的角色校验（market_admin/tenant_admin/platform_admin），并对 body.merchant_id 做「目标商户属于该市场」归属校验（A2）。
- R1-A5：staff.py create_staff/update_staff 禁止自助创建/提升 market_admin（该角色应仅由平台/租户管理员授予），或引入市场管理员绑定表。
- R6-B1：idempotency_middleware.py 对敏感路径（/auth/wechat-login、/auth/refresh、/admin/login）跳过响应体缓存，或对 response_body 加密/掩码。
- R4-C3：experience_cloud._check_budget 的 key 改为 per-principal（merchant_id/tenant_id 维度），并考虑把预算移到 Redis 以跨进程一致。

