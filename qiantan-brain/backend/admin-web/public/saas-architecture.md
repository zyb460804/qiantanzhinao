# 千摊智脑 SaaS 多租户架构文档

> 版本：1.0 | 更新：2026-07-13

## 一、系统概述

千摊智脑（Qiantan Brain）是一个面向菜市场/摊位商户的智能经营辅助系统，采用 SaaS 多租户架构，支持多租户隔离、套餐管理、订阅计费和用量配额控制。

### 技术栈

后端：FastAPI + SQLAlchemy 2.0 (async) + Alembic + SQLite/PostgreSQL
前端：React 18 + Vite 5 + Ant Design 5 + @ant-design/pro-components
鉴权：JWT（双 token 体系：管理员 + 商户）

### 项目结构

```
backend/
  app/
    main.py                      # FastAPI 入口，路由注册 + 中间件
    config.py                    # 全局配置（环境变量驱动）
    database.py                  # 异步引擎 + Session 管理
    core/
      admin_security.py          # 平台管理员 JWT 鉴权
      security.py                # 商户 JWT 鉴权
      tenant_context.py           # 租户上下文（ContextVar + 中间件）
      quota.py                   # 配额检查与用量记录
      middleware.py              # RequestID 中间件
    models/
      saas.py                    # 7 个 SaaS 模型
      merchant.py                # 商户模型（含 tenant_id）
      auth.py                    # AuthRevokedToken
      ...
    routers/
      admin/                     # 管理后台 API
        auth.py                  # 登录/登出/me
        dashboard.py             # 数据概览
        tenants.py               # 租户管理 + 接入流程
        plans.py                 # 套餐 CRUD
        subscriptions.py         # 订阅管理
        invoices.py              # 发票管理
        usage.py                 # 用量监控
  admin-web/                     # 前端管理后台
    src/
      pages/                     # 7 个页面
      layouts/AdminLayout.jsx    # ProLayout 侧边栏
      api/client.js              # Axios 实例 + JWT 拦截器
      context/AuthContext.jsx    # 认证状态管理
  migrations/                    # Alembic 迁移
  scripts/                       # 种子脚本
```

## 二、多租户架构

### 角色层级

```
platform_admin (PlatformAdmin)    平台超级管理员，管理所有租户/套餐/计费
  └─ tenant_admin (Merchant)     租户管理员，管理本租户内的商户
     └─ market_admin (Merchant)  市场管理员
        └─ owner (Merchant)      摊主
           └─ employee (Merchant) 员工
```

### 数据隔离机制

系统采用行级隔离（Row-Level Isolation）策略：

- Merchant 表通过 `tenant_id` 外键关联到 Tenant 表
- 每个请求通过 `TenantContextMiddleware` 清除上一次的 ContextVar，确保隔离
- `get_current_merchant` 依赖在加载 Merchant 后自动注入 `tenant_id` 到 ContextVar
- 所有租户数据查询应带 `WHERE tenant_id = get_current_tenant_id()`
- 管理后台 API（`/api/admin/*`）绕过隔离（平台级操作），但需显式传入 tenant_id

### ContextVar 机制

```python
# app/core/tenant_context.py
_tenant_id_var: ContextVar[uuid.UUID | None] = ContextVar("tenant_id", default=None)

def get_current_tenant_id() -> uuid.UUID | None
def set_tenant_id(tenant_id) -> None
def clear_tenant_id() -> None
def require_tenant_id() -> uuid.UUID  # 无 tenant_id 时抛 403
```

## 三、数据模型

### 7 个 SaaS 模型

| 模型 | 表名 | 说明 |
|------|------|------|
| Tenant | tenants | 租户（组织/市场），SaaS 层的顶层实体 |
| Plan | plans | 套餐（免费版/专业版/企业版） |
| Subscription | subscriptions | 订阅记录（租户×套餐，含状态生命周期） |
| Invoice | saas_invoices | SaaS 发票（避免与 POS 发票冲突） |
| UsageRecord | usage_records | 用量记录（按日累计，含 metric+date 唯一约束） |
| ApiKey | api_keys | 租户 API 密钥（SHA-256 哈希存储） |
| PlatformAdmin | platform_admins | 平台管理员账号 |

### 关键字段说明

**Tenant**
- `slug`：URL 友好标识，唯一
- `status`：trial / active / suspended / expired
- `trial_ends_at`：试用期结束时间
- `plan_id`：当前套餐（冗余字段，与 Subscription 同步）

**Subscription**
- `status`：trialing / active / past_due / canceled / expired
- `billing_cycle`：monthly / yearly
- `current_period_start/end`：当前计费周期
- `canceled_at`：取消时间（当前周期结束前仍有效）
- `auto_renew`：是否自动续费
- 唯一约束：`(tenant_id, status)` — 每个租户同一状态仅一条订阅

**UsageRecord**
- `metric`：api_calls / storage_mb / merchant_count / voice_seconds
- `recorded_date`：日期（YYYY-MM-DD）
- `value`：当天累计值
- 唯一约束：`(tenant_id, metric, recorded_date)` — 每租户每指标每天一条

**Invoice**
- `invoice_no`：发票号（格式 INV-YYYYMM-NNNN）
- `status`：draft / sent / paid / overdue / void
- `line_items`：JSON 数组，记录明细
- `payment_method`：wechat_pay / alipay / bank_transfer / manual

## 四、API 参考

### 鉴权体系

| 体系 | JWT Issuer | sub | 用途 |
|------|-----------|-----|------|
| 管理员 | qiantan-admin | PlatformAdmin.id | 管理后台所有 API |
| 商户 | qiantan-brain | Merchant.id | 业务 API |

两个体系共用 `jwt_secret` + HS256，但 decode 时校验 `iss` 防止跨体系 token 混用。

### 管理后台 API（/api/admin/）

**鉴权（auth.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/admin/login | 邮箱+密码登录，返回 JWT |
| POST | /api/admin/logout | 登出（吊销 token） |
| GET | /api/admin/me | 获取当前管理员信息 |

**数据概览（dashboard.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/admin/dashboard | 租户/商户/套餐/订阅统计 |
| GET | /api/admin/dashboard/plan-distribution | 套餐租户分布 |

**租户管理（tenants.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/admin/tenants | 租户接入（创建租户+试用订阅+首个商户） |
| GET | /api/admin/tenants | 分页列表（搜索+状态筛选） |
| GET | /api/admin/tenants/{id} | 租户详情 |
| PUT | /api/admin/tenants/{id} | 更新租户（状态/联系/套餐/备注） |

**套餐管理（plans.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/admin/plans | 套餐列表 |
| POST | /api/admin/plans | 创建套餐 |
| PUT | /api/admin/plans/{id} | 更新套餐 |
| DELETE | /api/admin/plans/{id} | 停用套餐（软删除） |

**订阅管理（subscriptions.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/admin/subscriptions | 分页列表（状态+租户筛选） |
| GET | /api/admin/subscriptions/{id} | 订阅详情 |
| POST | /api/admin/subscriptions | 创建订阅（分配套餐） |
| PUT | /api/admin/subscriptions/{id} | 更新（升级/降级/变更周期） |
| POST | /api/admin/subscriptions/{id}/cancel | 取消订阅 |
| POST | /api/admin/subscriptions/{id}/activate | 激活订阅（试用转正式） |

**发票管理（invoices.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/admin/invoices | 分页列表（状态+租户筛选） |
| GET | /api/admin/invoices/{id} | 发票详情 |
| POST | /api/admin/invoices | 创建发票 |
| PUT | /api/admin/invoices/{id} | 更新发票（状态/支付方式/备注） |
| POST | /api/admin/invoices/{id}/mark-paid | 标记已付 |
| POST | /api/admin/invoices/generate-from-subscription/{sub_id} | 从订阅自动生成发票 |

**用量监控（usage.py）**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/admin/usage/{tenant_id}/overview | 用量概览（套餐+全部配额） |
| GET | /api/admin/usage/{tenant_id}/quotas | 所有指标配额状态 |
| GET | /api/admin/usage/{tenant_id}/current/{metric} | 当前月份用量 |
| GET | /api/admin/usage/{tenant_id}/trend/{metric} | 最近 N 天趋势 |
| POST | /api/admin/usage/{tenant_id}/record | 手动记录用量 |

## 五、核心流程

### 租户接入流程

```
POST /api/admin/tenants
  ├── 1. 校验 slug 唯一性 + 套餐有效性
  ├── 2. 创建 Tenant (status=trial, trial_ends_at=now+14天)
  ├── 3. 创建 Subscription (status=trialing, 计费周期=monthly)
  ├── 4. 创建首个 Merchant (role=owner, tenant_id=绑定)
  └── 5. 初始化 UsageRecord (api_calls=0, storage_mb=0, merchant_count=1)
```

### 订阅生命周期

```
trialing → active → past_due → expired
                ↓
            canceled (当前周期结束前仍有效)
```

- 创建租户时自动创建 trialing 订阅
- 试用期结束后可手动激活为 active
- 可随时取消（canceled），当前周期结束前仍有效
- 升级/降级通过 PUT 更新 plan_id，previous_plan_id 记录原套餐

### 配额检查流程

```
1. 获取租户当前套餐 → Plan.max_api_calls_monthly / max_storage_mb / max_merchants
2. 查询当月累计用量 → SUM(UsageRecord.value) WHERE recorded_date LIKE 'YYYY-MM-%'
3. 比较：current >= limit → exceeded = True
4. 超限时返回 warning，可配合中间件拦截 API 请求
```

## 六、前端页面

### 路由结构

| 路径 | 页面 | 功能 |
|------|------|------|
| /login | Login | 管理员登录 |
| /dashboard | Dashboard | 数据概览（8 个统计卡片 + 套餐分布） |
| /tenants | Tenants | 租户列表 + 新建租户 Modal |
| /tenants/:id | TenantDetail | 租户详情 + 编辑 |
| /plans | Plans | 套餐 CRUD |
| /subscriptions | Subscriptions | 订阅列表 + 详情 + 取消/激活 |
| /invoices | Invoices | 发票列表 + 详情 + 标记已付 |
| /usage | Usage | 用量监控（配额进度条 + 趋势表） |

### 侧边栏菜单

```
数据概览 (Dashboard)
租户管理 (Tenants)
套餐管理 (Plans)
订阅管理 (Subscriptions)
发票管理 (Invoices)
用量监控 (Usage)
```

## 七、安全模型

### JWT 双 Token 体系

管理员 Token：
- `iss = "qiantan-admin"`
- `sub = PlatformAdmin.id`
- `role = super_admin / ops_admin`
- 有效期：7 天（10080 分钟）
- 吊销：通过 AuthRevokedToken 表查 jti

商户 Token：
- `iss = "qiantan-brain"`
- `sub = Merchant.id`
- `role = owner / employee / market_admin / tenant_admin`
- decode 时校验 iss，拒绝管理员 token 访问业务 API

### 密码安全

- PlatformAdmin 密码使用 bcrypt 哈希（`$2b$12$...`）
- 种子脚本创建的初始密码为 `ChangeMe123!`，生产环境必须修改
- `verify_password` 函数检测 `$2` 前缀判断是否 bcrypt，否则返回 False

### 生产安全自检

`settings.validate_security()` 在应用启动时检查：
- JWT secret 不能为默认值
- `auth_allow_fallback` 必须为 False（生产环境不允许 fallback 鉴权）
- 违规时 fail-fast 拒绝启动

## 八、部署指南

### 开发环境

```bash
# 后端
cd backend
export DATABASE_URL="sqlite+aiosqlite:///./qiantan_dev2.db"
python -m uvicorn app.main:app --reload --port 8000

# 前端
cd backend/admin-web
npm install
npm run dev  # 端口 5174，代理 /api → 127.0.0.1:8000
```

### 数据库初始化

```bash
# Alembic 迁移（建表）
cd backend
alembic upgrade head

# 种子数据（套餐 + 演示租户 + 管理员）
python scripts/seed_saas.py
```

### 生产部署要点

- 设置 `DATABASE_URL` 为 PostgreSQL 连接串
- 设置 `JWT_SECRET` 为强随机字符串（至少 32 字节）
- 确保 `AUTH_ALLOW_FALLBACK=false`
- 配置 `CORS_ORIGINS` 为具体域名白名单
- 使用 HTTPS + 反向代理（Nginx/Caddy）
- 定期备份数据库
- 监控 `auth_revoked_tokens` 表大小，定期清理过期记录

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| DATABASE_URL | sqlite:///qiantan_dev.db | 数据库连接串 |
| JWT_SECRET | (dev default) | JWT 签名密钥 |
| JWT_ALGORITHM | HS256 | JWT 签名算法 |
| JWT_EXPIRE_MINUTES | 10080 | Token 有效期（分钟） |
| AUTH_ALLOW_FALLBACK | false | 是否允许 fallback 鉴权 |
| CORS_ORIGINS | * | CORS 白名单（逗号分隔） |
| DEBUG | false | 调试模式 |
| PLATFORM_ADMIN_EMAIL | admin@qiantan.com | 种子管理员邮箱 |
| PLATFORM_ADMIN_PASSWORD | ChangeMe123! | 种子管理员密码 |
