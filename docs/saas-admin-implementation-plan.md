# 千摊智脑 SaaS 多租户后台 + Web 管理后台 实施方案

> 编制日期: 2026-07-13 | 基于现有 FastAPI 后端架构扩展

---

## 一、现有架构分析

### 1.1 已具备的基础

| 层面 | 现状 | SaaS 就绪度 |
|------|------|------------|
| **后端框架** | FastAPI 异步 + SQLAlchemy 2.0 async | ✅ 优秀，天然支持高并发多租户 |
| **多租户基础** | JWT 中 sub=merchant_id，已有 owner/employee/market_admin 三角色 | ⚠️ 有基础，但缺 Tenant 层和计费 |
| **数据库** | PostgreSQL 16（生产）/ SQLite（开发），Alembic 迁移 | ✅ 支持行级隔离 |
| **鉴权安全** | JWT 签发/校验/吊销，fail-closed 安全自检，CORS 白名单 | ✅ 安全基线扎实 |
| **业务模块** | 25 个路由、30+ 模型，覆盖语音记账、库存、POS、巡检等 | ✅ 业务功能完整 |
| **部署** | Docker Compose（db + backend），健康检查 | ✅ 容器化就绪 |
| **管理后台** | 仅有微信小程序端，无 Web 管理界面 | ❌ 需新建 |
| **SaaS 计费** | 无订阅/套餐/用量计量 | ❌ 需新建 |

### 1.2 核心差距

当前 `Merchant` 模型既是租户又是用户，没有独立的"组织/租户"概念。要做 SaaS，需要引入 **Tenant（租户/组织）** 层，让一个租户下可以包含多个商户（如一个连锁菜市场管理多个摊位）。

---

## 二、SaaS 多租户后端扩展方案

### 2.1 数据模型扩展

#### 2.1.1 新增 Tenant（租户/组织）模型

```
┌─────────────────────────────────────────────────────────┐
│  Tenant (租户/组织)                                      │
│  ├─ id: UUID                                            │
│  ├─ name: str          # 组织名（如"XX农贸市场管理公司"）  │
│  ├─ slug: str          # URL 友好标识                    │
│  ├─ plan_id: FK        # 当前订阅套餐                    │
│  ├─ status: str        # trial/active/suspended/expired │
│  ├─ max_merchants: int # 套餐限制的最大商户数             │
│  ├─ trial_ends_at: datetime                           │
│  ├─ created_at / updated_at                            │
└──────────────────────┬──────────────────────────────────┘
                       │ 1:N
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Merchant (商户) — 现有模型，增加 tenant_id            │
│  ├─ ...现有字段...                                      │
│  ├─ tenant_id: FK → tenants.id  ← 新增                 │
│  └─ role 现有: owner/employee/market_admin              │
└─────────────────────────────────────────────────────────┘
```

#### 2.1.2 新增 Subscription / Plan / Invoice 模型

| 模型 | 说明 | 关键字段 |
|------|------|---------|
| `Plan` | 套餐定义 | name(free/pro/enterprise), max_merchants, max_api_calls_monthly, price_monthly, price_yearly, features(JSON) |
| `Subscription` | 租户订阅记录 | tenant_id, plan_id, billing_cycle(monthly/yearly), status, current_period_start/end, canceled_at |
| `Invoice` | 账单 | tenant_id, subscription_id, amount, status(draft/paid/overdue/void), due_date, paid_at |
| `UsageRecord` | 用量计量 | tenant_id, metric(api_calls/storage_mb/merchant_count), value, recorded_at |
| `ApiKey` | API 密钥（程序化访问） | tenant_id, key_hash, name, scopes, last_used_at, expires_at |

#### 2.1.3 角色体系升级

```
角色层级（高→低）:
  platform_admin  — 平台超级管理员（管所有租户、套餐、计费）
  tenant_admin    — 租户管理员（管本组织下的商户、订阅）
  market_admin    — 市场管理员（管本市场的巡检、投诉、公告）— 现有
  owner           — 摊主 — 现有
  employee        — 员工 — 现有
```

### 2.2 多租户隔离机制

采用 **行级隔离 + 鉴权拦截** 方案（而非独立数据库/schema，适合当前规模）：

1. **所有业务表增加 `tenant_id` 外键** — 通过 Alembic 增量迁移
2. **鉴权中间件自动注入 tenant_id 过滤** — 在 `get_current_merchant` 基础上增加 `get_current_tenant` 依赖
3. **SQLAlchemy 事件监听器** — 自动为查询附加 `WHERE tenant_id = :current_tenant` 条件（可选，作为纵深防御）

```python
# 示例：扩展后的鉴权链
async def get_current_tenant(
    merchant: Merchant = Depends(get_current_merchant),
) -> Tenant:
    tenant = await db.get(Tenant, merchant.tenant_id)
    if tenant.status in ("suspended", "expired"):
        raise HTTPException(403, "租户已停用，请联系管理员")
    return tenant
```

### 2.3 新增路由模块

| 路由前缀 | 功能 | 角色 |
|----------|------|------|
| `/api/v1/admin/tenants` | 租户 CRUD、停用/启用 | platform_admin |
| `/api/v1/admin/plans` | 套餐管理 | platform_admin |
| `/api/v1/admin/subscriptions` | 订阅管理、升降级 | platform_admin |
| `/api/v1/admin/invoices` | 账单查看、标记已付 | platform_admin |
| `/api/v1/admin/usage` | 用量统计、配额查看 | platform_admin |
| `/api/v1/admin/metrics` | 平台级运营指标 | platform_admin |
| `/api/v1/tenant/members` | 租户内成员管理 | tenant_admin |
| `/api/v1/tenant/subscription` | 查看本租户订阅 | tenant_admin |
| `/api/v1/tenant/api-keys` | API 密钥管理 | tenant_admin |

### 2.4 用量计量与配额

- **中间件层**：每次 API 请求异步记录 `UsageRecord(tenant_id, metric="api_calls", value=1)`
- **配额检查**：请求开始前查询当月累计用量，超配额返回 `429 Too Many Requests`
- **存储计量**：定时任务统计 `uploads/` 目录大小，写入 `UsageRecord(metric="storage_mb")`
- **计量去重**：健康检查、OPTIONS 等不计入

### 2.5 计费流程

```
注册 → 创建 Trial 租户(free 套餐, 14天试用)
  → 试用期内可升级 pro/enterprise
  → 试用到期 → 降级 free（功能受限，不删数据）
  → 主动付费 → 创建 Subscription + Invoice
  → Invoice 状态: draft → paid（支付回调）
  → 到期续费 / 降级 / 取消
```

---

## 三、Web 管理后台方案

### 3.1 技术选型

| 层面 | 选择 | 理由 |
|------|------|------|
| **框架** | React 18 + Vite | 生态成熟，与小程序端技术栈解耦 |
| **UI 库** | Ant Design Pro 6 | 企业级管理后台开箱即用，表格/表单/图表完善 |
| **状态管理** | Zustand | 轻量，适合中后台 |
| **图表** | @ant-design/charts (G2) | 与 Ant Design 风格统一 |
| **请求** | axios + 统一拦截器 | JWT 自动注入、401 跳转登录 |
| **路由** | React Router v6 | 权限路由守卫 |
| **构建** | Vite 5 | 快速 HMR，生产构建优化 |
| **部署** | Nginx 静态托管 + 后端 API 代理 | 与现有 Docker 架构整合 |

### 3.2 目录结构

```
qiantan-brain/
├── backend/                 # 现有 FastAPI 后端
├── miniprogram/             # 现有微信小程序
├── admin-web/               # ← 新增 Web 管理后台
│   ├── src/
│   │   ├── api/             # API 请求封装
│   │   │   ├── client.ts    # axios 实例 + 拦截器
│   │   │   ├── auth.ts      # 登录/登出
│   │   │   ├── tenant.ts    # 租户管理 API
│   │   │   ├── billing.ts   # 计费 API
│   │   │   └── ...
│   │   ├── components/       # 通用组件
│   │   ├── layouts/
│   │   │   └── AdminLayout.tsx  # 侧边栏 + 顶栏布局
│   │   ├── pages/
│   │   │   ├── login/       # 登录页
│   │   │   ├── dashboard/   # 平台运营仪表盘
│   │   │   ├── tenants/     # 租户管理
│   │   │   ├── plans/       # 套餐管理
│   │   │   ├── subscriptions/ # 订阅管理
│   │   │   ├── invoices/    # 账单管理
│   │   │   ├── merchants/   # 商户管理
│   │   │   ├── markets/     # 市场管理
│   │   │   ├── usage/       # 用量分析
│   │   │   ├── settings/    # 系统设置
│   │   │   └── tenant/      # 租户端管理页（tenant_admin 视角）
│   │   ├── routes/          # 路由配置 + 权限守卫
│   │   ├── stores/          # Zustand stores
│   │   └── utils/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
└── docker-compose.yml       # 扩展: 增加 admin-web 服务
```

### 3.3 功能模块

#### 平台管理端（platform_admin）

| 模块 | 核心功能 |
|------|---------|
| **运营仪表盘** | 总租户数、活跃租户、MRR(月经常性收入)、新增趋势图、试用转化漏斗 |
| **租户管理** | 租户列表（搜索/筛选/排序）、创建/编辑、停用/启用、查看租户下商户、查看用量 |
| **套餐管理** | 套餐 CRUD、功能开关、价格设置、最大商户数/调用量配置 |
| **订阅管理** | 订阅列表、手动升降级、续期、取消、试用到期提醒 |
| **账单管理** | 账单列表、标记已付、导出 CSV、逾期账单告警 |
| **商户管理** | 全平台商户列表、查看详情、关联租户 |
| **市场管理** | 市场列表、巡检记录、投诉处理（复用现有 market_admin） |
| **用量分析** | 按 API/存储/商户数维度统计、租户用量对比、趋势图 |
| **系统设置** | 平台参数、管理员账号、操作审计日志 |

#### 租户管理端（tenant_admin）

| 模块 | 核心功能 |
|------|---------|
| **租户仪表盘** | 本组织数据概览：商户数/本月调用量/配额使用率 |
| **成员管理** | 本租户下商户列表、邀请成员、角色分配 |
| **订阅信息** | 当前套餐、用量 vs 配额、续费/升级入口 |
| **API 密钥** | 创建/查看/吊销 API Key |
| **商户详情** | 查看单个商户的库存、订单、经营数据 |

### 3.4 权限路由守卫

```typescript
// routes/guard.tsx
const RoleRoute = ({ roles, children }) => {
  const { user } = useAuthStore();
  if (!roles.includes(user.role)) return <Navigate to="/403" />;
  return children;
};

// 路由配置
<Route path="/admin/tenants" element={
  <RoleRoute roles={['platform_admin']}><TenantList /></RoleRoute>
} />
<Route path="/tenant/members" element={
  <RoleRoute roles={['tenant_admin', 'platform_admin']}><TenantMembers /></RoleRoute>
} />
```

### 3.5 登录方式

管理后台暂不走微信登录（小程序端走微信），改为：

1. **账号密码登录** — platform_admin / tenant_admin 用邮箱+密码登录
2. **JWT 复用** — 签发与小程序端相同结构的 JWT（sub=merchant_id, role=xxx），后端鉴权链无需改造
3. **后续扩展** — 可接入企业微信扫码、SSO 等

---

## 四、实施路线图

### 阶段一：SaaS 后端核心（预计 5-7 天）

| 序号 | 任务 | 涉及文件 |
|------|------|---------|
| 1.1 | 创建 Tenant / Plan / Subscription / Invoice / UsageRecord / ApiKey 模型 | `backend/app/models/saas.py` |
| 1.2 | Alembic 增量迁移：新建表 + Merchant 增加 tenant_id | `backend/migrations/versions/` |
| 1.3 | 扩展鉴权：新增 `get_current_tenant`、`require_role` 依赖 | `backend/app/core/security.py` |
| 1.4 | 新增 `platform_admin` 路由模块（tenants/plans/subscriptions/invoices/usage） | `backend/app/routers/admin/` |
| 1.5 | 新增 `tenant_admin` 路由模块（members/subscription/api-keys） | `backend/app/routers/tenant/` |
| 1.6 | 用量计量中间件 + 配额检查 | `backend/app/core/middleware.py` |
| 1.7 | 种子数据：创建默认 Free/Pro/Enterprise 套餐 + platform_admin 账号 | `backend/scripts/seed_saas.py` |
| 1.8 | 单元测试 + 接口冒烟测试 | `backend/tests/test_saas/` |

### 阶段二：Web 管理后台 MVP（预计 7-10 天）

| 序号 | 任务 |
|------|------|
| 2.1 | 项目脚手架：Vite + React + Ant Design Pro 初始化 |
| 2.2 | 登录页 + JWT 拦截器 + 路由守卫 |
| 2.3 | AdminLayout 布布（侧边栏菜单 + 顶栏用户信息） |
| 2.4 | 运营仪表盘（数据卡片 + 趋势图） |
| 2.5 | 租户管理（CRUD 列表 + 详情抽屉） |
| 2.6 | 套餐管理 + 订阅管理 |
| 2.7 | 账单管理 + 用量分析 |
| 2.8 | 商户管理 + 市场管理（复用现有 API） |

### 阶段三：部署整合（预计 2-3 天）

| 序号 | 任务 |
|------|------|
| 3.1 | admin-web Dockerfile（多阶段构建 → Nginx 静态） |
| 3.2 | docker-compose.yml 增加 admin-web 服务 |
| 3.3 | Nginx 反向代理配置（/api → backend, / → admin-web） |
| 3.4 | 后端 CORS 增加 admin-web 域名白名单 |
| 3.5 | 端到端验收 |

### 阶段四：计费集成（可选，后续迭代）

| 序号 | 任务 |
|------|------|
| 4.1 | 对接支付渠道（微信支付/支付宝） |
| 4.2 | 支付回调 → Invoice 状态流转 |
| 4.3 | 自动续费 / 到期降级定时任务 |
| 4.4 | 账单邮件通知 |

---

## 五、数据迁移策略

现有 `merchants` 表无 `tenant_id`，迁移策略：

1. 创建 `tenants` 表
2. 为每个已有 Merchant 创建一个对应的 Tenant（1:1，确保数据不丢）
3. 给 `merchants` 表增加 `tenant_id` 列，回填对应 Tenant ID
4. 将第一个 platform_admin 账号关联到一个特殊 Tenant（或独立于 Tenant 的平台管理员表）

迁移脚本通过 Alembic `upgrade` 实现，在 `op.bulk_update` 中回填。

---

## 六、技术风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| **存量业务表无 tenant_id** | 查询不带 tenant 过滤 → 跨租户数据泄漏 | 增量迁移加列 + 鉴权依赖强制注入 tenant_id |
| **小程序端鉴权改造** | 现有 merchant 直接关联，加 tenant 层后需适配 | 渐进式：tenant_id 可空，逐步回填，后端兼容无 tenant 的 merchant（降级为默认租户） |
| **用量计量性能** | 每请求写 DB 影响吞吐 | 异步写入（`asyncio.create_task`）+ Redis 缓冲批量落盘 |
| **计费准确性** | 支付状态不一致 | Invoice 状态机 + 定时对账任务 |

---

## 七、与现有代码的关系

本次扩展**不破坏**现有功能：

- 小程序端的 25 个路由模块**完全保留**，仅在鉴权链增加 `tenant_id` 注入
- 现有 `market_admin` 路由保持不变，管理后台前端直接复用其 API
- `Merchant` 模型只**增加** `tenant_id` 字段，不删除/重命名现有字段
- `docker-compose.yml` 只**增加** `admin-web` 服务，不改现有 `db`/`backend`

这是一次纯增量扩展，现有小程序用户无感知。
