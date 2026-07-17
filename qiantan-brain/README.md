# 千摊智脑 QianTan Brain

面向小微商户的环境感知型 AI 经营智能体与数字孪生决策系统。

为菜市场摊位、社区夫妻店等小微商户打造的 AI 经营助手。通过语音记账、视觉识别、环境感知和决策模拟，帮助不会打字的老板完成经营数字化。

---

## 产品形态

| 端 | 用户 | 技术 | 入口 |
|----|------|------|------|
| **微信小程序** | 摊主 / 员工 | 原生 WXML/WXSS/JS | 微信开发者工具 |
| **Web 管理后台** | 平台管理员 | React 18 + Vite + Ant Design 5 | `http://localhost:5174` |
| **FastAPI 后端** | 为以上两者提供 API | Python 3.11 + SQLAlchemy 2.0 | `http://127.0.0.1:8000` |

---

## 技术栈

| 模块 | 技术 |
|------|------|
| 前端（小程序） | 微信小程序原生 · 23 个页面 · Canvas 自绘图表 |
| 前端（管理后台） | React 18 · Vite · Ant Design 5 · Recharts · Axios |
| 后端 | FastAPI · Python 3.11 · SQLAlchemy 2.0 async · Alembic |
| 数据库 | PostgreSQL 16（生产） / SQLite（本地开发） |
| 鉴权 | 微信 OAuth + JWT（小程序） · 邮箱+bcrypt+HttpOnly Cookie（管理后台） |
| 语音识别 | 讯飞 ASR（支持方言；无凭证时走 Mock 演示模式） |
| 商品识别 | YOLOv8-nano · ONNX Runtime（权重为占位，需真实训练） |
| 天气 | 和风天气 API（默认 Key 为空走 Mock） |
| 边缘硬件 | Raspberry Pi 5 · 摄像头模块 · HX711 称重传感器 |
| 预测 | Facebook Prophet（按数据量自动回退） |
| 测试 | pytest · 404 个测试 |

---

## 微信小程序（23 个页面）

### 底部导航

| Tab | 功能 |
|-----|------|
| 🏠 **经营台** | 今日营收/利润/风险评分、天气、临期预警、最近记录、快捷入口 |
| 🎤 **记一笔** | 语音记账（讯飞）+ 文字记账，自动解析商品/数量/金额，支持确认/纠错/作废 |
| 📦 **库存** | 库存列表、排序/筛选/搜索、临期告警 |
| 🧠 **参谋** | AI 每日建议 + 三行式解释 + AI 行动执行 + 沙盘模拟 |
| 👤 **我的** | 经营快照、设备同步、13 个工具入口、摊位设置、帮助 |

### 工具页面

| 页面 | 功能 |
|------|------|
| **数字孪生** | 库存镜像/经营趋势图 7&30日/六维风险雷达/客单价曲线 |
| **POS 收银** | 选品→购物车→多支付（微信/现金/赊账）→挂单→退款→日结 |
| **采购管理** | 采购清单/下单/到货验收/退货/供应商付款 |
| **库存盘点** | 启动→逐项录入→差异原因→完成；支持断点续盘 |
| **经营报表** | 日报/周报/月报 + AI 点评 + 销售排行 + 趋势图 |
| **商品目录** | SKU 管理/别名/规格/单位换算/供应商比价+推荐 |
| **供应商档案** | 供应商 CRUD + 综合评分 + 黑名单 + 应付账款 |
| **财务管理** | 费用记账/月度报表/发票管理/微信&支付宝对账 |
| **运营中心** | 损耗记录/临期促销/客户赊账/数据导出 |
| **设备管理** | 树莓派设备注册/心跳监控/价签同步 |
| **拍照识货** | 拍照→YOLO识别→选择候选→入库/出库 |
| **食品安全** | 扫码追溯/批次状态/证书查看/问题上报 |
| **经营日历** | 节气/时令商品/4天天气预报/每日经营建议 |
| **员工管理** | 添加/编辑/停用员工，6 种角色 14 项权限 |
| **市场通知** | 市场公告/警告/紧急通知查看 |
| **租户中心** | 订阅套餐/用量配额+趋势图/账单记录 |

---

## Web 管理后台（12 个页面）

平台管理员通过邮箱+密码登录，JWT 仅存 HttpOnly Cookie。

| 页面 | 功能 |
|------|------|
| **数据看板** | 租户/商户/订阅总数、今日用量、套餐分布、营收趋势 |
| **运维监控** | 数据库/设备/AI/订阅健康检查 + 趋势图 + 健康评分 |
| **租户管理** | 租户列表/详情/编辑/暂停 + 接入向导（一步创建租户+订阅+商户） |
| **套餐管理** | 免费/专业/企业 三档套餐 CRUD + 配额 + 功能开关 |
| **订阅管理** | 订阅列表/详情/创建/升级/取消/激活 |
| **发票管理** | 发票生成/列表/标记已付/从订阅自动生成 |
| **用量监控** | 按租户查看配额/趋势/人工记账（审计） |
| **AI 运营** | AI Action 执行统计/成功率/按类型分布 |
| **设备监控** | 全平台设备列表/在线状态/错误告警 |
| **审计日志** | 管理员操作记录查询 |
| **管理员** | 平台管理员 CRUD + 5 种角色分配（仅超管） |
| **死信队列** | 失败事件查看/重试/标记解决 |

---

## SaaS 多租户体系

```
Tenant（租户/组织）
  └── Plan（套餐：free / pro / enterprise）
  └── Subscription（订阅 + 计费周期）
  └── Invoice（账单：draft → sent → paid → overdue → void）
  └── UsageRecord（按日用量：API调用/存储/商户数/语音时长）
  └── ApiKey（程序化访问密钥，SHA-256 哈希存储）
  └── Merchant（商户/摊主）
        └── StaffMember（员工：owner/manager/cashier/purchaser/stocker/market_admin）

PlatformAdmin（平台管理员角色）
  ├── super_admin      全部权限
  ├── ops_admin        运营管理（无计费）
  ├── billing_admin    计费管理
  ├── support_admin    支持工单
  └── auditor          只读审计
```

**门禁链**：`JWT 认证 → 租户状态检查 → 订阅有效性 → 套餐功能开关 → 配额检查`

**权限矩阵**：14 项细粒度权限（查看利润/修改价格/确认采购/供应商付款/赊账/退款/库存调整/记录损耗/日结/作废/导出/管理员工/锁定批次/销毁批次）

---

## 项目结构

```
qiantan-brain/
├── miniprogram/                 # 微信小程序 (23 页)
│   ├── pages/                   # 页面：经营台/记账/库存/参谋/我的 …
│   ├── components/              # 通用组件：语音按钮/库存图表/风险仪表 …
│   ├── utils/                   # 工具：离线同步/录音/AI流/图表/主题 …
│   └── images/                  # 图标资源
│
├── backend/                     # FastAPI 后端
│   ├── app/
│   │   ├── main.py              # 应用入口，路由注册
│   │   ├── config.py            # Pydantic Settings 配置
│   │   ├── database.py          # SQLAlchemy 引擎 + Alembic 启动
│   │   ├── models/              # 24 个数据模型
│   │   │   ├── merchant.py      # 商户（租户绑定 + 微信openid）
│   │   │   ├── saas.py          # 租户/套餐/订阅/发票/用量/密钥/管理员
│   │   │   ├── staff.py         # 员工 + 敏感操作审计
│   │   │   ├── market.py        # 市场/商户入场/巡检/投诉/通知
│   │   │   ├── inventory.py     # 库存记录 + 当前库存
│   │   │   ├── pos.py           # 销售订单/付款/日结/对账
│   │   │   ├── catalog.py       # SKU/别名/规格/单位/供应商
│   │   │   ├── purchase.py      # 采购清单 + 条目
│   │   │   ├── batch.py         # 批次生命周期 FIFO
│   │   │   └── …                # 更多模型
│   │   ├── schemas/             # Pydantic 请求/响应模型
│   │   ├── routers/             # 35 个路由模块
│   │   │   ├── voice.py         # 语音解析/确认/纠错/作废
│   │   │   ├── inventory.py     # 库存/盘点
│   │   │   ├── pos.py           # POS 收银
│   │   │   ├── purchase.py      # 采购管理
│   │   │   ├── advice.py        # AI 建议 + 沙盘
│   │   │   ├── twin.py          # 数字孪生
│   │   │   ├── staff.py         # 员工管理
│   │   │   ├── admin/           # Web 后台 API (10 个模块)
│   │   │   │   ├── auth.py      # 登录/登出/权限矩阵
│   │   │   │   ├── dashboard.py # 数据看板
│   │   │   │   ├── tenants.py   # 租户管理 + 接入流程
│   │   │   │   ├── plans.py     # 套餐管理
│   │   │   │   ├── subscriptions.py # 订阅管理
│   │   │   │   ├── invoices.py  # 发票管理
│   │   │   │   ├── usage.py     # 用量监控
│   │   │   │   ├── operations.py # AiOps/设备/运维
│   │   │   │   ├── admins.py    # 管理员管理
│   │   │   │   ├── audit.py     # 审计日志
│   │   │   │   └── export.py    # CSV 导出
│   │   │   └── tenant/
│   │   │       └── portal.py    # 租户自助（订阅/用量/发票）
│   │   ├── services/            # 18 个业务服务
│   │   │   ├── voice_parser.py  # NLU 文本→结构化
│   │   │   ├── advisor.py       # 建议生成引擎
│   │   │   ├── env_engine.py    # 环境系数引擎
│   │   │   ├── forecast.py      # Prophet 需求预测
│   │   │   ├── lifecycle.py     # 批次生命周期
│   │   │   └── …                # 更多服务
│   │   └── core/                # 核心模块
│   │       ├── security.py      # JWT 签发/校验 + 微信 code2session
│   │       ├── admin_security.py # 管理后台鉴权
│   │       ├── admin_permissions.py # 管理后台权限系统
│   │       ├── tenant_context.py # 租户上下文 + 门禁链
│   │       ├── quota.py         # 配额检查 + 用量记录
│   │       ├── audit.py         # 审计日志
│   │       └── middleware.py    # 请求ID/租户上下文中间件
│   ├── admin-web/               # React Web 管理后台
│   │   ├── src/
│   │   │   ├── pages/           # 12 个页面组件
│   │   │   ├── layouts/         # 管理后台布局
│   │   │   ├── context/         # AuthContext 认证上下文
│   │   │   ├── api/             # Axios 客户端
│   │   │   └── permissions/     # 前端权限门控
│   │   └── vite.config.js       # Vite 配置（API 代理 → :8000）
│   ├── tests/                   # 404 个测试
│   ├── scripts/
│   │   ├── seed_db.py           # 基础种子数据
│   │   └── seed_saas.py         # SaaS 种子（套餐/租户/管理员）
│   ├── migrations/              # Alembic 迁移
│   └── qiantan_dev.db           # SQLite 开发数据库
│
├── edge/                        # 树莓派边缘端
│   ├── main.py                  # 边缘端入口
│   ├── vision/                  # 摄像头 + YOLO 推理
│   └── weighing/                # HX711 称重模块
│
├── ml/                          # 机器学习
│   ├── train_yolo.py
│   ├── prepare_dataset.py
│   └── prophet_predict.py
│
├── datasets/                    # 训练数据集
├── docs/                        # 17 篇文档
└── reference/                   # 参考实现（离线同步）
```

---

## 快速开始

### 1. 启动后端

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

API 文档：`http://localhost:8000/docs`

### 2. 初始化 SaaS 数据

```bash
python -m scripts.seed_saas
```

创建三档套餐（免费/专业/企业）、演示租户、平台管理员。

### 3. 启动 Web 管理后台

```bash
cd admin-web
npm install
npx vite
```

打开 `http://localhost:5174`，使用 `admin@qiantan.com` / `Admin123!` 登录。

### 4. 微信小程序

用微信开发者工具打开 `miniprogram/` 目录。开发环境默认连接 `http://127.0.0.1:8000`。

### 5. 运行测试

```bash
cd backend
python -m pytest tests/ -v
```

404 个测试，覆盖全部核心功能。

---

## API 概览

### 小程序侧 (`/api/v1/*`)

| 模块 | 端点 | 说明 |
|------|------|------|
| 鉴权 | `POST /auth/wechat-login` | 微信 jscode2session 换 JWT |
| 语音记账 | `POST /voice/parse-text` | 自然语言文本解析 |
| 语音记账 | `POST /voice/confirm` | 确认入库 |
| 库存 | `GET /inventory/current` | 当前库存 |
| 库存 | `GET /inventory/alerts` | 临期告警 |
| 盘点 | `POST /inventory/stocktake/start` | 启动盘点 |
| 经营建议 | `GET /advice/daily` | AI 每日建议 |
| 沙盘模拟 | `POST /simulate/what-if` | What-if 分析 |
| 数字孪生 | `GET /twin/dashboard` | 经营仪表盘 |
| POS | `POST /pos/orders` | 创建销售订单 |
| 采购 | `GET /purchase/today` | 今日采购清单 |
| 报表 | `GET /reports/daily` | 日报 |
| 员工 | `GET /staff` | 员工列表 |
| 食品安全 | `GET /food-safety/trace/{code}` | 追溯查询 |

### 管理后台侧 (`/api/admin/*`)

| 模块 | 端点 | 说明 |
|------|------|------|
| 鉴权 | `POST /login` | 邮箱+密码登录 |
| 看板 | `GET /dashboard` | 平台概览统计 |
| 租户 | `GET /tenants` | 租户分页列表 |
| 租户 | `POST /tenants` | 租户接入（一站式） |
| 套餐 | `GET /plans` | 套餐列表 |
| 订阅 | `GET /subscriptions` | 订阅列表 |
| 发票 | `GET /invoices` | 发票列表 |
| 用量 | `GET /usage/{id}/quotas` | 租户配额状态 |
| 运维 | `GET /monitoring/overview` | 平台健康监控 |

### 租户自助 (`/api/v1/tenant/*`)

| 端点 | 说明 |
|------|------|
| `GET /subscription` | 本租户订阅信息 |
| `GET /usage/quotas` | 本租户用量配额 |
| `GET /usage/trend/{metric}` | 30 天用量趋势 |
| `GET /invoices` | 本租户发票列表 |

---

## 实现状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 微信小程序 | ✅ | 23 个页面，覆盖经营全流程 |
| FastAPI 后端 | ✅ | 35 个路由，404 个测试通过 |
| Web 管理后台 | ✅ | React + Ant Design，12 个功能页 |
| SaaS 多租户 | ✅ | 租户/套餐/订阅/发票/用量/门禁链 |
| 员工权限系统 | ✅ | 6 种角色 × 14 项权限 |
| 方言语音 ASR | 🟡 | 讯飞接口已接；无凭证时走 Mock |
| 语音领域解析 | ✅ | 商品/数量/金额/买卖类型 |
| YOLO 商品识别 | 🟡 | 推理管线已接；权重为占位 |
| 天气接口 | 🟡 | 和风天气已接；无 Key 走 Mock |
| 环境增强引擎 | ✅ | 温度/降雨/周末/节假日系数 |
| 商户偏好学习 | ✅ | 建议反馈 + 商户画像 |
| 商品生命周期 | ✅ | 批次/FIFO/临期/损耗预警 |
| 三行式建议 | ✅ | 建议/依据/风险 |
| What-if 沙盘 | ✅ | 进货量/价格/天气模拟 |
| 风险雷达 | ✅ | 六维真实计算 |
| 千摊经验云 | 🟡 | 差分隐私匿名聚合 |
| Prophet 预测 | 🟡 | 在线接线，按数据量回退 |
| PostgreSQL | 🟡 | Docker 可部署；本地默认 SQLite |
| 边缘端 | 🚧 | 代码补全；真机待验证 |

> ✅ 已完成 · 🟡 接口已接/演示模式 · 🚧 开发中

---

## 团队

- 成员 A：算法与硬件（YOLO、规则引擎、树莓派）
- 成员 B：全栈开发（小程序、后端、数据库、可视化）
- 成员 C：产品与文档（用户调研、答辩材料、商业计划书）

## License

MIT — 本项目为大学生创新创业训练计划作品
