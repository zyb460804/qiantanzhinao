# 千摊智脑管理后台 AI 提示词与研发规范包

> 版本：V1.0  
> 日期：2026-07-13  
> 配套文档：`docs/saas-admin-upgrade-blueprint-v2.md`  
> 用法：先复制“公共上下文”，再追加具体任务提示词。不要只给 AI 一句“优化后台”。

---

## 1. 公共上下文提示词

以下内容应放在每次设计或开发任务的最前面：

```text
你正在升级“千摊智脑”SaaS 管理后台。

项目背景：
- 业务是农贸市场/摊主经营数字化与智能决策。
- 后端为 FastAPI + SQLAlchemy 2 async + Alembic。
- 管理后台为 React 18 + Vite 5 + Ant Design 5 + Ant Design Pro Components。
- 当前已有登录、平台概览、租户、套餐、订阅、发票、用量页面。
- 后端已有 Tenant、Plan、Subscription、Invoice、UsageRecord、ApiKey、PlatformAdmin、AdminAuditLog。
- 平台角色至少包含 super_admin、ops_admin，后续扩展 billing_admin、support_admin、auditor。
- 小程序是摊主业务端，Web 后台是平台和组织管理端，不要把二者的信息架构混在一起。

产品目标：
从普通 SaaS CRUD 后台升级为“平台运营 + 客户成功 + 商业化 + AI 治理 + 设备运维”的运营控制台。

设计关键词：
可信、专业、克制、清晰、经营感、行业温度。主品牌色使用稳重绿色，不使用大面积紫色渐变和无意义科技光效。

强制约束：
1. 不破坏现有 API 和业务行为，除非任务明确要求并同步迁移。
2. 不覆盖或回滚其他人正在进行的修改。
3. 所有写操作必须考虑权限、审计、幂等、异常状态和二次确认。
4. 所有租户数据必须考虑 tenant_id 隔离，禁止信任前端传入的 tenant_id。
5. 页面必须有 loading、empty、error、403 状态。
6. 金额使用 Decimal/字符串或最小货币单位，不使用浮点数进行账务计算。
7. 时间必须明确时区，展示统一为 YYYY-MM-DD HH:mm。
8. 前端优先使用 TypeScript strict、路由懒加载、服务端分页、URL 可复现筛选。
9. 不为“显得高级”增加无业务价值动画、图表、渐变和卡片。
10. 输出必须包含：现状分析、改动方案、文件清单、关键代码、风险、测试、验收标准。
```

---

## 2. 总控开发提示词

适合让编码 AI 完成一个完整迭代：

```text
[粘贴公共上下文]

任务：完成千摊智脑管理后台 V2 第一阶段升级。

本阶段范围：
1. 将路由页面改为 React.lazy 懒加载，并合理拆分 antd/pro-components/recharts vendor chunk。
2. 增加统一 AppErrorBoundary、403、404、500 页面。
3. 建立 permissions 模块，实现菜单、路由、按钮三级权限控制。
4. 新增审计日志前端页面并接入现有 /api/admin/audit-logs。
5. 把租户详情升级为 Tabs 工作台：概览、组织资料、订阅账单、用量配额、操作记录。没有后端数据的标签页使用明确占位，不伪造接口。
6. 建立 design tokens 和统一 PageHeader/ListPage/DetailPage 状态模板。

执行要求：
- 先检查当前文件结构、路由、API client、鉴权上下文和后端响应结构。
- 给出实施计划后直接改代码，不只给建议。
- 尽量保留现有页面业务逻辑，做渐进式重构。
- 不引入大型新依赖，新增依赖必须解释必要性。
- 为权限工具、API 错误处理和关键页面增加测试。
- 执行 lint、test、build；若项目没有对应脚本，补齐最小可用配置。
- 构建后报告主包和主要异步 chunk 大小，与改造前 1765.09 kB / gzip 557.88 kB 对比。

输出：
- 变更摘要
- 修改文件列表
- 权限矩阵
- 测试结果
- 构建体积对比
- 遗留风险
```

---

## 3. UI/UX 设计提示词

### 3.1 全局视觉升级

```text
[粘贴公共上下文]

请为管理后台建立可开发落地的视觉系统，不要只输出情绪板。

需要产出：
1. 品牌色、语义色、中性色、图表色板。
2. 字体层级、间距、圆角、阴影、边框、页面宽度。
3. Ant Design ConfigProvider token 映射。
4. 仪表盘、列表、详情、表单四类页面模板。
5. Button、Tag、Alert、Table、Form、Drawer、Modal 的使用规范。
6. loading、empty、error、403、disabled、danger 状态规范。
7. 浅色和暗色模式对比度要求。
8. 给出 TypeScript token 文件和 CSS Variables 示例。

视觉方向：
- 主色为稳重经营绿，如 #167A5A。
- 页面背景为低对比浅灰绿，卡片保持白色。
- 不使用大面积渐变，不把所有指标做成彩色卡片。
- 数据表格优先保证阅读密度，金额和数量右对齐。
- AI 页面体现“可解释和可治理”，不是聊天机器人皮肤。

验收：
- 普通文本对比度达到 WCAG AA。
- 状态不能只靠颜色表达。
- 所有设计参数能映射到代码 token。
```

### 3.2 平台总览页面

```text
[粘贴公共上下文]

重构 Dashboard 页面，使其回答“今天平台是否正常、哪里需要处理”。

数据区块：
- KPI：活跃租户、试用转化、MRR、逾期金额、API 成功率。
- 趋势：租户增长、收入、API 调用量。
- 结构：套餐分布、租户健康度、功能渗透率。
- 待办：试用即将到期、配额超 80%、账单逾期、设备离线、同步失败。
- 最近动态：租户创建、套餐变更、人工改账、管理员高风险操作。

要求：
- 如果现有 API 暂无某指标，建立 typed adapter 和明确的 unavailable 状态，不编造数据。
- 每个 KPI 显示口径、时间范围和变化率。
- 点击指标跳转到带 URL 筛选条件的目标列表。
- 图表支持空、错、加载、下载。
- 页面必须适配 1280、1440、1920 宽度；不以手机管理为主，但 768 宽度可用。

请输出页面信息架构、组件树、数据契约、交互说明、代码修改和测试。
```

### 3.3 租户 360° 详情页

```text
[粘贴公共上下文]

将 TenantDetail 从“资料展示 + 编辑表单”升级为客户成功工作台。

顶部摘要：
- 租户名称、slug、状态、套餐、创建时间。
- 健康度、商户数/上限、API 用量/上限、订阅到期日、欠费状态。
- 主操作：编辑资料、调整套餐、延长试用、暂停/恢复、生成账单。

Tabs：
1. 概览
2. 组织资料
3. 商户与成员
4. 订阅与账单
5. 用量与配额
6. 设备与同步
7. AI 使用
8. 风险与审计

要求：
- 使用嵌套路由或 URL query 保存当前 Tab。
- 各 Tab 独立请求、懒加载、可错误重试。
- 危险操作展示影响说明并要求填写原因。
- 没有 API 时先定义接口契约与 EmptyState，不写假数据。
- 展示最近变更时间线。
- 所有操作纳入权限点。

请先列出现有 API 可直接支持的区块和必须新增的 API，再实施前端可完成部分。
```

---

## 4. 权限与安全提示词

### 4.1 后端 RBAC

```text
[粘贴公共上下文]

为平台管理 API 实现细粒度 RBAC。

现状：
- PlatformAdmin 有 role 字段，目前至少 super_admin、ops_admin。
- 多数 admin router 只依赖 get_current_admin，相当于所有管理员拥有同等权限。

目标：
1. 定义权限点常量/枚举。
2. 建立角色到权限点映射，至少支持 super_admin、ops_admin、billing_admin、support_admin、auditor。
3. 实现 require_admin_permission(permission) FastAPI dependency。
4. 为 tenants/plans/subscriptions/invoices/usage/audit/export 各端点声明明确权限。
5. 权限拒绝返回结构化 403，包含 request_id，但不泄漏敏感信息。
6. 高风险写操作记录成功和失败审计。
7. 增加权限矩阵测试，覆盖每种角色的允许和拒绝路径。

安全约束：
- 前端权限不是安全边界。
- super_admin 拥有所有权限，但不可用字符串 contains 等模糊判断。
- 数据导出、人工改用量、标记账单已支付必须是独立权限。
- 不允许 role 由请求体修改当前登录用户自身权限。

输出权限矩阵、修改文件、迁移需求、测试结果和兼容性说明。
```

### 4.2 会话安全

```text
[粘贴公共上下文]

评估并升级管理后台登录会话安全。

现状：JWT 存在 localStorage，Axios 使用 Authorization Bearer。

请给出两阶段方案：
- 短期兼容方案：不大改后端情况下，降低 XSS 和令牌风险。
- 推荐生产方案：HttpOnly Secure SameSite Cookie + CSRF 防护 + Refresh Token 轮换。

需要明确：
- 登录、刷新、退出、吊销、空闲超时、多设备退出流程。
- CORS、Cookie、CSRF 配置。
- 前后端改动文件。
- 数据库是否需要 session/refresh token 表。
- 迁移期间如何兼容旧 Bearer Token。
- 安全测试和回滚方案。

不要只说“使用 HttpOnly Cookie”，要给出完整的数据流和边界条件。
```

### 4.3 多租户隔离审计

```text
[粘贴公共上下文]

对当前 FastAPI + SQLAlchemy 项目进行多租户隔离审计和修复规划。

任务：
1. 列出所有包含 tenant_id、merchant_id 或间接归属租户的模型。
2. 追踪所有读取、写入、导出、后台任务、离线同步、设备和媒体路径。
3. 找出依赖前端 tenant_id、缺少过滤、可通过资源 ID 越权访问的位置。
4. 区分平台跨租户接口和普通租户接口。
5. 为每个风险给出最小修复和纵深防御方案。
6. 增加跨租户负向测试：租户 A 不得读写租户 B 的资源。

输出按 P0/P1/P2 排序，必须包含具体文件、函数、数据流和测试样例。
```

---

## 5. AI 运营功能提示词

### 5.1 AI 治理后台

```text
[粘贴公共上下文]

设计并实现“AI 运营中心”第一版。

已有能力：
- Recommendation/每日建议
- AIAction，动作类型包括 clearance、purchase、price、stock、custom
- AIAction 状态包括 pending、executed、rejected、failed、cancelled
- 语音解析、视觉识别、行为画像和环境建议

第一版页面：
1. AI 总览
2. 建议与动作列表
3. 动作详情
4. 失败原因分析
5. 运营反馈标注

动作详情必须展示：
- 租户/商户
- 来源建议
- 输入摘要
- 规则/Prompt/模型版本
- 动作类型、payload
- 风险等级
- 状态时间线
- 执行人
- 执行结果或错误
- 反馈标注

安全规则：
- price、purchase、clearance 默认视为高风险或中风险动作。
- 高风险动作必须人工确认，且后端再次校验业务前置条件。
- 不允许仅凭模型输出直接拼接执行参数。
- 每次执行必须幂等、可审计、可失败重试但不能重复扣减库存或生成重复采购单。

请输出数据模型增量、API、页面、权限点、状态机、测试和上线灰度方案。
```

### 5.2 提示词版本管理

```text
[粘贴公共上下文]

为千摊智脑设计 Prompt Registry 与灰度发布机制。

数据模型至少包含：
- prompt_key
- version
- scene
- system_prompt
- user_template
- input_schema
- output_schema
- model
- model_parameters
- risk_level
- rollout_scope
- status: draft/testing/canary/active/retired
- evaluation_result
- created_by/approved_by
- created_at/activated_at

功能：
- 编辑草稿
- 使用固定样本测试
- 对指定租户灰度
- 发布全量
- 一键回滚
- 对比两个版本的输出和指标

约束：
- 生产运行只引用不可变版本。
- Prompt 中不得存密钥或个人敏感信息。
- 输出必须经过 JSON Schema 校验。
- 高风险业务动作还需要确定性规则校验。
- 发布和回滚必须审计。

请给出数据库模型、API、页面流程、版本状态机和评测方案。
```

### 5.3 经营建议系统提示词模板

以下用于真正的经营建议模型，不用于编码：

```text
你是“千摊智脑经营顾问”，服务对象是农贸市场摊主。

你的任务：根据库存、保质期、采购、销售、天气、节假日、价格和历史行为，生成可解释、可执行、风险受控的经营建议。

原则：
1. 不虚构缺失数据；缺失时明确说明。
2. 先判断风险，再给建议。
3. 建议必须给出依据、预期收益、可能风险、执行窗口。
4. 涉及改价、采购、清货、库存调整时输出结构化动作草案，不直接宣称已执行。
5. 不提供食品安全违法建议，不建议隐瞒过期、变质或来源不明商品。
6. 金额、数量和时间必须使用输入数据中的单位。
7. 当置信度不足时，要求人工确认或补充数据。

只输出符合以下 JSON 结构的数据：
{
  "summary": "一句话结论",
  "risk_level": "low|medium|high",
  "confidence": 0.0,
  "evidence": ["依据"],
  "recommendations": [
    {
      "title": "建议标题",
      "reason": "原因",
      "expected_impact": "预期影响",
      "risk": "风险",
      "action_window": "建议执行时间",
      "requires_confirmation": true,
      "action": {
        "type": "clearance|purchase|price|stock|custom",
        "payload": {}
      }
    }
  ],
  "missing_data": ["缺失字段"]
}
```

---

## 6. 后端 API 提示词

### 6.1 统一错误与响应

```text
[粘贴公共上下文]

统一后台 API 的成功、分页和错误响应。

要求：
- 成功响应包含 code、message、data、request_id。
- 分页统一 items/page/page_size/total/has_next。
- 错误 code 使用稳定机器码，不让前端依赖中文 message。
- 400、401、403、404、409、422、429、500 有统一映射。
- 业务冲突优先使用 409，例如订阅状态非法、账单重复生成、slug 冲突。
- 所有错误携带 Request ID。
- 不把数据库异常、SQL、堆栈返回前端。
- 保持现有接口兼容，给出渐进迁移方案。

请实现异常类、全局 handler、响应类型、示例迁移和测试。
```

### 6.2 状态机

```text
[粘贴公共上下文]

把 Tenant、Subscription、Invoice、AIAction 的状态流转改为显式状态机。

要求：
- 为每种实体定义允许的 from → to。
- 状态变更必须通过服务层方法，禁止路由直接赋值。
- 记录操作人、原因、时间、前后状态。
- 重复请求支持幂等。
- 非法流转返回 409 和稳定错误码。
- 补充并发测试，避免两个请求同时完成互斥状态变更。
- 保持已有数据库字段兼容，必要时新增 history 表。

请先列出现有状态值和调用位置，再实施服务层、路由改造和测试。
```

---

## 7. 前端工程提示词

### 7.1 TypeScript 渐进迁移

```text
[粘贴公共上下文]

制定并执行 admin-web 的渐进式 TypeScript 迁移。

要求：
- 开启 strict，不使用大面积 any。
- 先迁移 API client、鉴权、路由、权限、公共组件，再迁移页面。
- 为 Tenant、Plan、Subscription、Invoice、Usage、Admin 定义领域类型。
- 区分 API DTO 和页面 ViewModel。
- Axios 返回值类型必须明确。
- 不在一次提交中重写所有页面；每一步都能 build。
- 提供 JS/TS 共存期间的配置和最终清理计划。

输出迁移顺序、文件清单、类型定义、风险和每阶段验收。
```

### 7.2 数据请求层

```text
[粘贴公共上下文]

使用 TanStack Query 重构服务端状态管理。

目标：
- 建立稳定 query key 工厂。
- 列表筛选与分页进入 query key。
- 详情、列表、统计缓存策略分开。
- 写操作成功后精确失效相关缓存。
- 处理取消请求、重试、401、403、409。
- 不把表单本地状态放进 Query。
- 不在每个页面重复 message.error(error.message)。

先以 tenants 和 tenant detail 为样板，再给出扩展到 subscriptions/invoices/usage 的模式。
```

### 7.3 性能优化

```text
[粘贴公共上下文]

优化 admin-web 生产包体积。

已知基线：
- 主 JS 1765.09 kB
- gzip 557.88 kB
- Vite 大于 500 kB 警告

要求：
- 分析依赖构成。
- 路由级 React.lazy。
- 合理拆分 react、antd/pro-components、recharts。
- 图表只在需要页面加载。
- 检查重复依赖和错误的全量导入。
- 不通过单纯调高 chunkSizeWarningLimit 隐藏问题。
- 构建后给出前后对比。

目标：首屏主包 gzip 小于 250 kB，且路由切换无明显卡顿。
```

---

## 8. 测试提示词

### 8.1 前端测试

```text
[粘贴公共上下文]

为管理后台建立分层测试基线。

单元测试：
- 权限判断
- 状态/金额/时间格式化
- API 错误映射
- Query key

组件测试：
- 租户列表筛选、分页、空状态、错误重试
- 危险操作确认
- 无权限按钮隐藏/禁用

E2E：
- 管理员登录
- 新建租户
- 调整套餐
- 创建/激活/取消订阅
- 生成账单并标记支付
- 查看用量与审计
- ops_admin 越权被拒绝

要求：
- 测试数据可重复执行。
- 测试之间不依赖执行顺序。
- 至少覆盖一个正常路径、一个异常路径、一个权限路径。
- 输出测试脚本、夹具策略和 CI 命令。
```

### 8.2 后端测试

```text
[粘贴公共上下文]

补齐 SaaS 管理 API 的安全与一致性测试。

必须覆盖：
- 未登录 401
- 无权限 403
- 资源不存在 404
- 状态冲突 409
- 参数错误 422
- 重复幂等请求
- 跨租户资源访问拒绝
- 并发订阅变更
- 重复账单生成
- 人工改用量及冲正
- 审计日志内容

测试应验证数据库最终状态，而不只验证 HTTP code。
```

---

## 9. 代码审查提示词

```text
[粘贴公共上下文]

请对本次管理后台变更做严格代码审查。

优先检查：
1. 权限绕过和仅前端限制。
2. 跨租户访问。
3. 账务浮点、重复执行、状态覆盖。
4. JWT、Cookie、CSRF、XSS。
5. 审计缺失或敏感数据进入日志。
6. React stale closure、重复请求、竞态、卸载后更新。
7. Query 缓存失效错误。
8. 表格筛选与后端参数不一致。
9. 破坏已有 API 兼容性。
10. 缺少 loading/empty/error/403 和测试。

输出只包含真实可执行问题，按 P0/P1/P2/P3 排序。每个问题给出文件、行号、影响、触发条件和修复建议。没有问题时明确说明，并列出剩余测试盲区。
```

---

## 10. UI 文案规范

### 10.1 按钮

推荐：

- 新建租户
- 调整套餐
- 生成账单
- 标记已支付
- 暂停租户
- 恢复租户
- 导出当前结果

避免：

- 确定
- 操作
- 处理
- 执行
- 提交数据

### 10.2 状态文案

| 内部值 | 中文展示 |
|---|---|
| trial | 试用中 |
| active | 正常 |
| suspended | 已暂停 |
| expired | 已到期 |
| trialing | 试用订阅 |
| past_due | 已逾期 |
| canceled | 已取消 |
| draft | 草稿 |
| paid | 已支付 |
| overdue | 已逾期 |
| void | 已作废 |
| pending | 待处理 |
| executed | 已执行 |
| rejected | 已拒绝 |
| failed | 执行失败 |
| cancelled | 已取消 |

不要在不同页面把同一状态翻译成不同文字。

### 10.3 错误文案模板

```text
标题：未能生成账单
说明：该订阅在当前计费周期已存在账单。
操作：查看已有账单 / 返回订阅详情
辅助信息：Request ID: req_xxx
```

### 10.4 确认文案模板

```text
标题：暂停“XX 农贸市场”吗？
影响：租户成员将无法继续访问业务功能，已有数据不会删除。
生效：立即生效。
恢复：平台管理员可随时恢复。
必填：暂停原因。
按钮：取消 / 确认暂停
```

---

## 11. API 与代码命名规范

### 11.1 路径

- 资源使用复数名词：`/tenants`、`/subscriptions`。
- 状态动作可使用子资源动作：`/subscriptions/{id}/activate`。
- 高风险动作必须幂等，支持 `Idempotency-Key`。
- 平台端统一 `/api/admin`，租户端统一 `/api/tenant`，业务端保持 `/api/v1`。

### 11.2 权限点

使用 `resource.action`：

```text
tenant.read
tenant.update
subscription.change
invoice.mark_paid
usage.adjust
audit.read
```

### 11.3 前端文件

- React 组件：PascalCase。
- hooks：`useXxx`。
- API：`tenantApi.ts`。
- Query keys：`tenantKeys.ts`。
- 权限常量：`permissions.ts`。
- 页面入口保持薄，不堆积所有业务逻辑。

### 11.4 分支与提交建议

```text
feat(admin): add tenant 360 workspace
feat(rbac): enforce invoice mark-paid permission
fix(billing): prevent duplicate invoice generation
refactor(admin-web): lazy-load route pages
test(tenant): reject cross-tenant resource access
```

---

## 12. 交付模板

每次让 AI 完成任务时，要求最终按此格式汇报：

```text
1. 完成内容
2. 关键设计决策
3. 修改文件
4. 数据库/API 变更
5. 权限与审计
6. 测试结果
7. 构建/性能结果
8. 兼容性与迁移
9. 遗留风险
10. 下一步建议
```

---

## 13. 第一条最推荐直接使用的提示词

如果准备马上继续开发，直接使用下面这条：

```text
[先粘贴“公共上下文提示词”]

请直接在当前仓库完成“后台 V2 Phase 0：稳住基线”。

范围：
1. 将 App.jsx 的所有业务页面改为 React.lazy + Suspense 路由懒加载。
2. 配置 Vite manualChunks，拆分 react、antd/pro-components、recharts。
3. 增加统一的 403、404、500 页面和 React ErrorBoundary。
4. 建立 theme/tokens，替换 AdminLayout 中硬编码的 #667eea，主色改为 #167A5A。
5. 暗黑模式跟随系统设置并持久化。
6. 统一 API 错误解析，展示 request_id；禁止每个页面重复拼错误文案。
7. 增加最小 ESLint、Prettier、Vitest 配置和 npm scripts。
8. 为权限工具和 API 错误解析增加单元测试。

约束：
- 不改变现有后端 API。
- 不重写业务页面。
- 不引入不必要的大型依赖。
- 不覆盖其他未提交修改。
- 完成后运行 lint、test、build。
- 报告构建前基线 1765.09 kB / gzip 557.88 kB 与构建后结果。

先检查现状并给出 5 步以内计划，然后直接实施。
```
