# 千摊智脑 全面体检报告
> ⚠️ 历史快照：本文数字与代码引用为撰写当时的状态（2026-08-16 删减收口后已有变动，如经验云/offline-media/voice_parser_v2 等已删除），现状以根 README 与 [docs/README.md](README.md) 为准。

> 日期：2026-07-26 | 方式：12 个智能体并行审计（4 路代码审计 + 测试运行 + 依赖检查 + 6 轮对抗性复核）
> 范围：FastAPI 后端 · React 管理后台 · 微信小程序 · 依赖版本
> 性质：**只读审计，未修改任何代码**

---

## 0. 体检结论

| 维度 | 结论 |
|------|------|
| 测试套件 | ✅ **549 个测试全部通过**（Python 3.13.14 / pytest 9.1.1，89.5s，0 失败 0 错误 0 跳过） |
| 后端安全 | ⚠️ 基线不差（无裸 SQL、RBAC + 审计链路完整、设备侧防重放），但有 **1 处授权完全缺失**与**多处 fail-open 默认值** |
| 后端工程健康 | ⚠️ 仓库卫生良好（DB 文件/uploads 均已 gitignore，零追踪），但**干净环境部署必炸**（numpy 未声明 + 4 张表缺迁移） |
| 管理后台 | ⚠️ 架构健康（HttpOnly Cookie 无 localStorage XSS 面、路由守卫双层无绕过、无 console 残留），但**登录页明文写着超管密码** |
| 小程序 | ✅ 较上次审计明显改善（旧 12 项已修 7 项、console.log 零残留），剩余为弱网与登录态边界问题 |
| 依赖版本 | ℹ️ 后端受 fastapi 版本锁约束、前端整体停在上一代大版本生态，**自洽无零散失配** |

**总体判断**：功能完整度与测试覆盖是这个项目的强项；短板集中在「从开发环境走向生产部署」的最后一公里——授权边界、默认值安全性、部署可重现性。

---

## 1. P0 — 必须立即修复（2 项，均经对抗性复核确认）

### P0-1 🔴 CRITICAL｜市场管理后台 API 完全没有授权校验

**文件**：[market_admin.py](../backend/app/routers/market_admin.py)（全部 12 个端点）

整个 `/api/v1/market-admin` 只做了「登录校验」（`get_current_merchant`），**没有任何**「是不是市场管理员 / 是不是本市场成员」的授权校验，且 `market_id` / `merchant_id` 直接取自请求体：

| 端点 | 行号 | 任意注册商户可以做什么 |
|------|------|----------------------|
| `create_market` | L39 | 创建任意市场 |
| `list_market_merchants` | L55-79 | 传任意 `market_id` 枚举该市场全部商户（merchant_id、摊位号、品类、食安评分、状态） |
| `register_merchant` | L82-97 | 把任意商户塞进任意市场，并伪造 `license_number` |
| `create_inspection` | L138-156 | **为任意商户伪造食品安全巡检结论**（result 可填 pass/fail） |
| `resolve_complaint` | L220-234 | 把别人的投诉标记 resolved 并写入任意 resolution |
| `create_notice` | L273-287 | 向任意市场发布官方公告 |

**复核结论**：4 类可能的缓解措施全部不成立——① 五张 market 表（`models/market.py`）都没有 `tenant_id` 列，`TenantContextMiddleware` 无从过滤；② `Merchant.role` 已定义 `market_admin` 角色、`core/admin_permissions.py` 也提供了 `get_current_admin` + `require_admin_permission` 并被所有 `admin_*` 路由使用，**唯独本文件未接入**；③ `main.py` 中间件无路径级授权；④ `main.py:149` 无条件注册该路由，生产可达。

对比证据：`merchant_id != merchant.id` 归属检查在其他 10 个路由出现 **43 次**（catalog.py 单文件 16 次），本文件 **0 次** —— 这是漏改，不是设计选择。

> 复核唯一修正：`list_market_merchants` 响应中并不返回 `license_number`（该字段只能通过 `register_merchant` 伪造写入）。核心问题不受影响。

**修复方向**：该路由属监管侧能力，应改用独立管理端鉴权（`get_current_admin` + `require_admin_permission`），或新增 market_admin 身份绑定表；所有 `market_id`/`merchant_id` 必须先验证归属再落库。

---

### P0-2 🔴 CRITICAL｜登录页明文展示默认超管账号密码

**文件**：[Login.jsx:131](../backend/admin-web/src/pages/Login.jsx#L131)

登录卡片底部**无条件**渲染 `默认账号 admin@qiantan.com / Admin123!`，无任何 DEV 环境判断，生产构建同样包含。

**复核确认**：该凭据是真实可用的——`scripts/seed_saas.py:45-46` 以此为默认值、L179-183 创建 `role="super_admin"` 的 PlatformAdmin；`super_admin` 在 `admin_permissions.py:200-207` 自动拥有全部权限（含 ADMIN_MANAGE / EXPORT_DATA / TENANT_SUSPEND）。全后端**无 `must_change_password` 字段、无首登强制改密**，仅有 seed 脚本的 print 警告与文档提醒，不构成技术控制。

**连带暴露面**：`README.md:243`、`docs/摊主故事包.md:190`、`admin-web/public/saas-architecture.md`（作为静态资源可直接访问）。

**修复方向**：删除该提示文案 → seed 脚本去掉口令默认值改为环境变量缺失即报错 → PlatformAdmin 增加 `must_change_password` 并在登录响应强制跳转改密 → 清理文档中的明文口令。

---

## 2. P1 — 上线前必修（8 项）

### 2.1 部署阻断类

| # | 严重级 | 问题 | 位置 | 状态 |
|---|--------|------|------|------|
| P1-1 | HIGH | **numpy 是启动硬依赖但三个 requirements 文件均未声明** | requirements.txt | ✅ 已复核确认 |
| P1-2 | HIGH | **4 张在用表缺失 Alembic 迁移，生产报 relation does not exist** | models/dead_letter.py:18 等 | ✅ 已复核确认 |
| P1-3 | HIGH | scipy 被无守卫导入且测试直接调用，未在任何 requirements 声明 | inventory_optimizer.py:508/569 | 未复核 |

**P1-1 细节**：`inventory_optimizer.py:28` 顶层 `import numpy as np`，导入链 `main.py:16-18 → advice.py:22 → advisor.py:26 → inventory_optimizer` 全链无条件分支、无 try/except，**uvicorn 启动即执行**。`scripts/deploy.sh cmd_dev` 仅 `pip install -r requirements.txt` 后直接启动 → 干净环境必然 ModuleNotFoundError。`feature_engineering.py:20` 同样顶层导入。旁证：`vision_model_onnx.py` 对 numpy 采用函数内延迟导入，说明项目有防护惯例但这两个模块遗漏。

**P1-2 细节**：`dead_letter_events`、`device_firmwares`、`device_model_versions`、`device_remote_logs` 在全部 21 个迁移文件中均未创建，但被线上代码使用：`edge.py:221/295/350`、`admin/operations.py:826`、`health_monitor.py:23-28`，复核还额外发现 `services/offline_sync.py:263-279`（同步失败时写死信表，本身会因表缺失而失败，**导致故障诊断数据丢失**）。当前 dev 库靠 `database.py:80` 的 `create_all` 回退掩盖了漂移，而 `config.py:22` 明确该回退仅限 dev/test。

---

### 2.2 安全 fail-open 类

| # | 严重级 | 问题 | 位置 | 状态 |
|---|--------|------|------|------|
| P1-4 | HIGH | **生产安全自检被 debug 短路，而 debug 默认 True** | config.py:12 / :130 | ✅ 已复核确认 |
| P1-5 | HIGH | 微信登录在 debug 下用**确定性** mock openid 放行 | core/security.py:80-85 | 未复核 |
| P1-6 | HIGH | seed 脚本内置默认超管口令、无首登强制改密 | scripts/seed_saas.py:46 | 未复核（与 P0-2 同源） |

**P1-4 细节**：`validate_security()` 首行即 `if self.debug: return`，而 `debug: bool = True` 是默认值，且无任何 validator 把 debug 与 app_env 绑定。只要部署时没显式注入 `DEBUG=false`，即便 `APP_ENV=production`：

- 默认 JWT 密钥 `dev-secret-please-override-with-env-in-prod`（config.py:70，**明文在仓库里**）不会被拦截 → 任何人可离线签发任意 token，且 `admin_security.py:62` 复用同一密钥 → **一把钥匙同时打穿商户端与管理端**
- `database.py:25` `echo=settings.debug` → SQL 全量回显（含 INSERT 参数里的 password_hash、邮箱）
- `admin/auth.py:126` `secure=not settings.debug` → 管理员会话 Cookie 以非 Secure 下发

`docker-compose.yml:38` 的 `DEBUG:-false` 兜住了容器路径（prod compose 还强制注入 JWT_SECRET），但**裸 uvicorn / systemd / k8s manifest 部署代码层完全 fail-open**。加重证据：`backend/.env.example:3` 写的是 `DEBUG=true`；`saas-architecture.md:338` 错误声称 DEBUG 默认 false，与代码矛盾。

**P1-5 与 P1-4 叠加即构成生产环境完整认证绕过**：`wechat_code2session` 在未配置微信凭证 + debug 为真时返回 `dev_openid_ + sha256("qiantan-dev:" + code)[:24]`，攻击者完全控制 code → 可用任意 code 无限创建商户账号（自动建 Merchant 并签发 7 天 JWT），或猜到开发期用过的 code 即可确定性重算 openid 登录该商户。且 `/auth/wechat-login` 应用层无任何限流。

---

### 2.3 授权与会话类

| # | 严重级 | 问题 | 位置 |
|---|--------|------|------|
| P1-7 | HIGH | **员工权限由客户端 `X-Staff-Id` 头决定，缺失该头即自动获得 owner 全权** | staff.py:42-82（L55 `role = "owner"` 为默认值） |
| P1-8 | HIGH | 商户登出无法真正吊销令牌：aware datetime 写入 naive TIMESTAMP，PostgreSQL 下必然 500 | core/security.py:186 |
| P1-9 | HIGH | 公开追溯接口 LIKE 通配符未转义，可无鉴权枚举全平台批次与供应商 | food_safety.py:181 |

- **P1-7**：员工无独立令牌、共用商户 JWT，任何终端只要**不发** `X-Staff-Id` 头就拿到 owner 全部权限。受影响的实际高风险操作：`pos.py:786` 退款、`pos.py:1481` 日结、`operations.py:86` 报损。代码注释已自承是「过渡方案」，但该方案等于把授权决策交给客户端。
- **P1-8**：`revoke_token` 写入 tz-aware datetime，而该列是 `sa.DateTime()`（TIMESTAMP WITHOUT TIME ZONE），asyncpg 会抛 DataError → logout 返回 500、事务回滚 → jti 未入吊销表 → **令牌在剩余 7 天有效期内继续可用**。交叉证据：管理员侧 `revoke_admin_token`（admin_security.py:160-161）已显式做 `.replace(tzinfo=None)` 归一化，**商户路径漏改**；SQLite 开发环境不报错，所以测试覆盖不到。
- **P1-9**：`BatchLifecycle.qr_data.contains(trace_code)` 的 `autoescape` 默认 False，参数中的 `%`/`_` 直接生效 → 请求 `/food-safety/trace/%25` 即可匹配任意批次并返回 product_name、supplier_name、origin、certificates 等跨租户商业敏感信息，配合前缀通配可盲枚举遍历。该端点显式无鉴权（消费者扫码用），批次表本身不含 merchant 过滤。

---

## 3. P2 — 中等技术债（选摘 12 项）

### 后端

| 问题 | 位置 | 说明 |
|------|------|------|
| 管理员创建/改密未接入密码策略 | admin/admins.py:37 | `password_policy.validate_password` **全库无调用点**，只靠 Pydantic `min_length=8`，`88888888` 可作 super_admin 口令 |
| 经验云跨商户聚合接口完全无鉴权 | cloud.py:18/32/43 | 差分隐私预算是模块级 dict（进程重启清零、多 worker 各自计数），未鉴权+未限流可反复请求把噪声平均掉还原真值 |
| CORS 白名单混入 `*` 时与 credentials 同时生效 | main.py:105-117 | 只处理了「恰好等于 `*`」，`CORS_ORIGINS=*,https://admin.example.com` 会走 else 分支 |
| 审计与限流的客户端 IP 取直连地址 | core/audit.py:37 | 不看 X-Forwarded-For 且 `TRUSTED_PROXIES` 默认空集、全仓无该变量 → 反代下审计 IP 全是 nginx 容器地址、IP 维度限流失效；副作用是可用 5 次错误尝试锁死指定管理员 15 分钟。另 `deploy/nginx/conf.d/qiantan.conf` include 了不存在的 `proxy_params`，会让 nginx 启动失败 |
| 幂等中间件把含 JWT 的响应体明文落库 | idempotency_middleware.py:237 | 登录响应体（含新签发令牌）长期驻留业务库 |
| 支付路径整段自动对账 `except Exception: pass` 且无日志 | pos.py:1248 | 账实不符只能靠人工核对才暴露 |
| POS 硬编码费率 0.006 绕过按商户配置的费率查询 | pos.py:1239 | 绕过 `reconciliation.py:40-53` 的 `_channel_fee_rate`；该魔法数字重复 4 处 |
| AI 动作执行把原始异常直接回客户端 | ai_actions.py:282 | SQLAlchemy 异常文本携带表名/列名/约束名 |
| 员工 PIN 码明文存储 | models/staff.py:79 | 无哈希、无复杂度校验 |
| 3 处 N+1 查询 | pos.py:492 / inventory.py:592 / advisor.py:312 | 第 3 处在 `/advice/daily` 主链路上，每商品 3-4 条查询 |
| 7 个文件超 800 行红线 | pos.py **1587** / purchase.py 1289 / catalog.py 1241 等 | pos.py 是变更热点 + 超长文件叠加 |
| req_clean.txt 与 requirements.txt 漂移 | req_clean.txt | 缺 4 个运行时依赖，且丢失了 fastapi 版本钉死警告 |

### 管理后台

| 问题 | 位置 | 说明 |
|------|------|------|
| AiOps 把接口失败降级为空数据 | AiOps.jsx:27 | `.catch(() => ({items:[]}))` → 错误与真实无数据不可区分；且统计卡片基于最多 100 条截断数据计算 |
| 设备监控硬截断前 100 台 | Devices.jsx:45 | 搜索/筛选只在前 100 条内生效，**离线故障设备可能完全不可见**（这是监控页） |
| 用量监控租户下拉只加载前 100 个 | Usage.jsx:27 | 超过 100 的租户永远无法被选中 |
| 401 硬跳转丢失上下文 | api/client.js:48 | 整页刷新、无 returnUrl、无「会话过期」提示，填写中的表单全丢 |

### 小程序

| 问题 | 位置 | 说明 |
|------|------|------|
| profile 离线队列计数恒显 0 | profile.js:172 | `JSON.parse` 原生数组必然抛 SyntaxError 被空 catch 吞掉。**复核后由 HIGH 降为 MEDIUM**：目前生产代码无任何地方调用 `enqueue()`（POS 用自己的 key），该 storage key 从未被写入，故当前显示 0 与事实一致 —— 属**潜伏缺陷**，一旦接入即静默显形 |
| voice 录音上传绕过 `app.uploadFile` | voice.js:97-129 | 手工拼 Authorization 头，缺失 ensureLogin 前置与 401 刷新重试 → token 过期时用户反复重录反复失败 |
| 录音 onStop/onError 每次录音重复注册 | voice.js:75-83 | 团队已写了修复版封装 `utils/recorder.js`（注释明确写着这个修复），但**该文件从未被 require，是死代码**，voice.js 仍用会泄漏的裸写法 |
| POS 双离线队列互不相通 | pos.js:262（旧审计 P1-1 未修复） | 两套幂等键两套重试；且网络恢复钩子只同步 `qt_offline_queue`，POS 单必须等用户再进 POS 页才补传 |
| 手动同步全部失败也提示「同步成功」 | profile.js:197 | `SyncEngine._run` 对单条失败只 markFailed 后 resolve，不 reject |
| 用户主动写操作断网零反馈 | advisor.js:364 / voice.js:199 / profile.js:281 | 加入采购清单、撤销记账、保存偏好失败均无提示，`saveProfile` 甚至无条件弹「偏好已保存」 |
| 死代码 ~450 行 | offline-media.js(375) + recorder.js(69) | 全库无 require |

---

## 4. 依赖升级评估

### 4.1 后端（8 个直接依赖过期）

| 包 | 当前 | 最新 | 风险 | 建议 |
|----|------|------|------|------|
| **fastapi** | 0.115.6 | 0.140.0 | 🚫 **forbidden** | **不升**。requirements.txt 顶部注释：>=0.139.0 在 Python 3.13 上 `include_router` 静默失效导致全部路由 404（2026-07-12 已排查） |
| redis | 5.2.1 | 8.0.1 | 🔴 major | 跨 3 个大版本，连接池/异步 API/返回类型均有破坏性变更，暂不动 |
| bcrypt | 4.2.1 | 5.0.0 | 🔴 major | 涉及密码哈希安全路径，暂不动 |
| alembic | 1.13.0 | 1.18.5 | 🟡 minor | 可升，升级后需核对 autogenerate 输出 |
| email-validator | 2.2.0 | 2.3.0 | 🟢 safe | 可升 |
| prometheus-client | 0.25.0 | 0.26.0 | 🟢 safe | 可升 |
| prometheus-fastapi-instrumentator | 8.0.2 | 8.1.0 | 🟢 safe | 可升 |
| sentry-sdk | 2.66.0 | 2.66.1 | 🟢 safe | 可升 |

**外加必须补充的声明**：`numpy`、`scipy`（见 P1-1 / P1-3）

### 4.2 管理后台（11 个直接依赖过期，其中 9 个大版本）

| 包 | 当前 | 最新 | 风险 |
|----|------|------|------|
| react / react-dom | 18.3.1 | 19.2.8 | 🔴 major |
| antd | 5.29.3 | 6.5.2 | 🔴 major（antd 6 面向 React 19 生态） |
| @ant-design/icons | 5.6.1 | 6.3.2 | 🔴 major（与 antd 6 配套，不可单独升） |
| vite | 5.4.21 | 8.1.5 | 🔴 major（跨 3 个大版本） |
| @vitejs/plugin-react | 4.7.0 | 6.0.4 | 🔴 major（6.x 要求 Vite 6+） |
| react-router-dom | 6.30.4 | 7.18.1 | 🔴 major（v7 合并 Remix 架构） |
| eslint / @eslint/js | 9.39.5 | 10.x | 🔴 major（仅影响开发环境） |
| recharts | 3.9.2 | 3.10.1 | 🟢 safe |
| prettier | 3.9.5 | 3.9.6 | 🟢 safe |

**关键判断**：这 9 个大版本升级**存在生态耦合**（antd 6 依赖 React 19、plugin-react 6 依赖 Vite 6+），必须整体迁移，不能零散升。当前 React 18 + Vite 5 + antd 5 是一个**自洽的技术栈选择，无零散失配**。

### 4.3 推荐的升级策略

**立即可做（零风险）**：6 个 safe 级 + alembic + 补 numpy/scipy 声明
**不建议现在做**：前端 9 个大版本整体迁移（工作量大、对当前目标无收益）、redis/bcrypt 大版本
**永久禁止**：fastapi >= 0.139.0（除非先在 Python 3.13 上验证 `include_router`）

---

## 5. 功能升级建议

基于对 `docs/` 下 PRD 与代码实现的比对，以下是**已规划但未落地**的能力：

### 5.1 经营日报主动触达（PRD 已写、代码未实现）

`docs/prd-daily-report-push.md` 完整定义了「首页摘要卡片 + 触发式微信订阅消息」，但：
- 小程序全库**无 `requestSubscribeMessage` 调用** → 订阅消息链路完全未实现
- 后端 `reports.py` 有 `/daily` `/weekly` `/trends` 等拉取接口，但无推送侧逻辑

PRD 自己指出的价值锚点：`behavior/profile.adoption_rate` 基线约 0.65（35% 建议未被采纳），触达→采纳链路有明显断点。这是**投入产出比最高的功能升级**——PRD 已经写完，只差实现。

### 5.2 多租户强隔离收尾

`core/tenant_context.py:47` `STRICT_TENANT_REQUIRED = False` 仍是过渡期设置（tenant_id 为空时仅 WARNING 不阻断）。配合 P0-1 发现的 market 表无 tenant_id 列，说明多租户改造尚未收口。建议：补 market 表 tenant_id → 清理存量空 tenant_id 数据 → 翻转开关为 True。

### 5.3 CI 补齐部署可重现性校验

P1-1/P1-2 这两个「干净环境必炸」的问题，本质是 CI 缺两道门禁：
1. 干净虚拟环境 `pip install -r requirements.txt` + 启动冒烟测试
2. `alembic upgrade head` 后 metadata 与库结构 diff 为空

加上这两道，同类问题不会再复发。

---

## 6. 审计方法说明

- **测试**：真实执行 `pytest tests/ -q`，549/549 通过（无需补装依赖，说明现有 `.venv` 已被手动补全过）
- **对抗性复核**：对 4 路审计中各自最严重的 CRITICAL/HIGH 发现共 6 条做了独立复核，复核智能体被明确要求「目标是推翻它」。结果：**6 条全部确认为真**（0 条被推翻），其中 1 条严重级下调（小程序离线队列 HIGH → MEDIUM，因当前触发路径不可达）、1 条标题措辞被修正（market_admin 的执照信息只能伪造写入、不能读取）
- **未复核项**：其余 HIGH/MEDIUM/LOW 发现由单一审计智能体基于实际读码给出，未经二次复核，修复前建议自行验证行号

**统计**：12 个智能体 · 267 次工具调用 · 106 万 token · 23 分钟
