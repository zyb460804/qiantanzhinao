# 千摊智脑小程序 代码审查报告

> 审查日期：2026-07-12
> 审查人：高级开发工程师（Senior Developer）
> 审查范围：miniprogram/ 前端 + backend/ 后端 + docs/ 文档

---

## 一、总评

**整体完成度：88/100**

项目整体质量扎实。后端 25 个路由模块全部有对应测试（31 个测试文件），前端 19 个页面中 13 个 FULL 实现 + 1 个 PARTIAL（calendar）+ 1 个设计参考页（styleguide）。团队在 MVP + Phase 2 范围内基本兑现了 README 的承诺。

但存在 **PRD 中规划的「经营日报主动触达」功能（P0-R1 ~ P0-R5）完全没有落地**，以及 1 个页面的硬编码数据问题、边缘端硬件集成仍为「待验证」状态。以下是详细分析。

---

## 二、与规划的对照分析

### 2.1 README 中的「实现状态」对照

| 功能 | README声明 | 实际审查结论 | 偏差 |
|------|-----------|-------------|------|
| 微信小程序 | ✅ 首页/语音/库存/建议/沙盘/数字孪生/盘点/采购/报表 | **一致** — 甚至超额完成（pos/vision/catalog/ops/devices/finance/trace 均有完整实现） | 无 |
| FastAPI 后端 | ✅ 84 测试通过 | **一致** — 31 测试文件覆盖全部路由 | 无 |
| 方言语音 ASR | 🟡 讯飞接口已接；演示模式兜底 | **一致** — voice.js demoMode 变量存在但未启用，当前走真实接口 | 无 |
| 语音领域解析 | ✅ 商品/数量/金额/买卖类型 | **一致** — parse-text 接口实现完整 | 无 |
| YOLO 商品识别 | 🟡 占位权重 | **一致** — vision 页面完工，但README 标注「需真实训练」 | 无 |
| 天气接口 | 🟡 默认 Key 为空走 Mock | **一致** — env/today 接口三层降级 | 无 |
| 千摊经验云 | 🟡 阈值匿名聚合 + Laplace 差分隐私；联邦学习规划中 | **一致** — cloud router + pytest 覆盖 | 无 |
| Prophet 预测 | 🟡 脚本 + 在线接线已完成 | **一致** | 无 |
| 边缘端 | 🚧 代码补全；真机待验证 | **一致** — edge/ 代码结构完整(camera/inference/hx711)，但标注 for 硬测 | 无 |

**结论：README 标注状态与代码实现高度一致，没有虚假标注。**

---

### 2.2 前端页面逐一检查

19 个注册页面逐一审查结果：

| # | 页面 | 路径 | 状态 | JS行 | WXML行 | API调用 |
|---|------|------|------|------|--------|---------|
| 1 | 经营台 | index | ✅ FULL | 118 | 74 | 3端点 |
| 2 | 记一笔 | voice | ✅ FULL | 177 | - | 5端点 |
| 3 | 库存 | inventory | ✅ FULL | 119 | - | 2端点 |
| 4 | 参谋 | advisor | ✅ FULL | 139 | - | 2端点 |
| 5 | 决策沙盘 | sandbox | ✅ FULL | 272 | 55 | 3端点 |
| 6 | 数字孪生 | dashboard | ✅ FULL | 344 | 87 | 5端点 |
| 7 | 拍照识货 | vision | ✅ FULL | 423 | 179 | 4端点 |
| 8 | 经营报告 | report | ✅ FULL | 554 | 205 | 3端点 |
| 9 | 采购清单 | purchase | ✅ FULL | 414 | 264 | 10+端点 |
| 10 | 库存盘点 | stocktake | ✅ FULL | 316 | 248 | 4端点 |
| 11 | 智能收银 | pos | ✅ FULL | 418 | 121 | 6+端点 |
| 12 | **经营日历** | **calendar** | **⚠️ PARTIAL** | **65** | **45** | **0端点** |
| 13 | 设计系统 | styleguide | ✅ FULL | 24 | 147 | 无需API |
| 14 | 商品管理 | catalog | ✅ FULL | 122 | 123 | 6+端点 |
| 15 | 经营管理 | ops | ✅ FULL | 110 | 122 | 6端点 |
| 16 | 设备管理 | devices | ✅ FULL | 51 | 45 | 4端点 |
| 17 | 财务管理 | finance | ✅ FULL | 60 | 73 | 3端点 |
| 18 | 批次追溯 | trace | ✅ FULL | 126 | 63 | 1端点 |
| 19 | 我的 | profile | ✅ FULL | 59 | - | 无API |

**TabBar 页（5 个）：index / voice / inventory / advisor / profile — 全部完善。**

---

## 三、发现的问题

### 🔴 严重问题（需立即修复）

#### P1：经营日报主动触达（PRD R1-R5）完全未实现

规划文档 `docs/prd-daily-report-push.md` 定义的 P0 需求**零落地**：

| 需求 | 状态 | 说明 |
|------|------|------|
| R1 首页日报卡片 | ❌ 未实现 | index.wxml 无日报组件，只有待办+工具入口 |
| R2 触发式订阅消息 | ❌ 未实现 | 无 wx.requestSubscribeMessage 调用 |
| R3 事件判定服务 | ❌ 未实现 | 后端无 daily_event service |
| R4 点击跳转闭环 | ❌ 未实现 | 未实现 recommendation_id 跳转定位 |
| R5 提醒开关 | ❌ 未实现 | profile 页无经营提醒开关 |

**影响：PRD 中的北极星指标（adoption_rate 0.65→0.80）没有技术落地路径。**

**建议：**
- 方案 A（推荐）：拆分 PRD 为独立 feature branch，按 P0→P1→P2 分批实现
- 方案 B（比赛优先）：若比赛答辩需要，至少实现 R1（首页日报卡片）+ R3（事件判定后端），其余做 Mock 展示

---

### 🟡 中等问题（需关注）

#### P2：calendar 页面全量硬编码

```javascript
// [P2 占位] 时令与策略为本地 mock；
// 后续可接 /env/solar-term 与 /advice/seasonal 接口。
// 当前无后端接口对接，数据全部硬编码。
```

数据不可变的经营日历无法产生实际经营价值。缺失 API：
- `GET /env/solar-term` — 节气数据接口（后端不存在此路由）
- `GET /advice/seasonal` — 季节性建议接口（后端不存在此路由）

**建议：** 若「经营日历」是比赛必须展示的功能，建议保留；否则可列为 Phase 3 或 scope cut。

#### P3：YOLO 模型权重为占位

edge/vision/model/ 目录仅含 README，无实际 `.onnx` 权重文件。当前 vision 页面上传实际图片时会走 демо 降级。如果比赛需要真机演示「拍照识货」，必须尽早训练/集成真实模型。

#### P4：边缘端硬件验证缺失

README 标注 🚧「真机待验证」— 树莓派的 camera + hx711（称重传感器）+ 离线同步链路代码虽已编写，但没有真机测试记录。

---

### 🔵 轻微问题（代码质量建议）

#### I1：全局变量污染 app.js

app.js 中全局函数过多（16 个方法），部分职责应拆分：
- `_requestOnce` / `_uploadOnce` → 独立 request.js 模块
- `_wechatCode` / `_authLogin` → 独立 auth.js 模块
- `getSkinByHour` / `resolveSkin` → 独立 theme.js 模块

#### I2：错误处理不一致

部分页面用 `console.error` 吞错误（dashboard / inventory），部分用 `wx.showToast`（voice / advisor）。建议统一使用 app.showToast() 做用户提示 + console.error 做日志。

#### I3：Canvas 2D 重复代码

sandbox.js、dashboard.js、report.js 各自实现了类似的 Canvas 2D 柱状图/折线图绘制逻辑，建议抽取为 charts 工具模块（如 `utils/chart.js`）。

#### I4：空状态文案差异化不足

多个页面的空状态提示文案过于通用（如 "还没有经营记录"），建议增加情景化引导（如 "早上8点前通常没有流水，别急 👋"）。

#### I5：saysReply 流式字幕重复

advisor.js 和 voice.js 各自实现了逐字打印逻辑，应抽取为 mixin 或工具函数。

---

## 四、未完成功能清单（对照 PRD + README）

### Phase 3（README 标注）
| 功能 | 优先级 | 阻塞项 |
|------|--------|--------|
| 实地测试 | P0 | 需要商户合作 |
| 论文撰写 | P0 | 需要实验数据 |
| 比赛答辩材料 | P0 | 依赖 demo-script.md |

### PRD 日报触达（未开始）
| 功能 | 优先级 | 阻塞项 |
|------|--------|--------|
| 首页日报卡片组件 | P0 | 需新建 daily-report 组件 |
| 订阅消息封装 | P0 | 需微信后台申请模板 |
| 事件判定服务 | P0 | 需后端新建 daily_event service |
| 点击跳转闭环 | P0 | 需 advisor 页支持 recommendation_id 参数 |
| 提醒开关 | P0 | UI控件+后端偏好存储 |
| 口语化文案 | P1 | 模板文案设计 |
| 频控 | P1 | 后端频控逻辑 |
| 采纳快捷入口 | P1 | 前端按钮+behavior/feedback 调用 |

### calendar 接口缺失
| 功能 | 优先级 | 说明 |
|------|--------|------|
| `/env/solar-term` | P2 | 节气数据接口 |
| `/advice/seasonal` | P2 | 季节性建议接口 |

### 边缘端真机验证
| 功能 | 优先级 | 说明 |
|------|--------|------|
| 树莓派 5 跑通 | P1 | camera + hx711 + 离线同步 |
| YOLO 真实权重训练 | P1 | 需要标注数据集 |
| 端到端联调 | P1 | 小程序→边缘端→后端 全链路 |

---

## 五、代码质量评分

| 维度 | 评分 | 评语 |
|------|------|------|
| 架构设计 | 9/10 | 清晰的分层：pages/components/utils，后端 routers/services/models 分离好 |
| 代码一致性 | 8/10 | `var app = getApp()` 模式统一使用，request 封装统一，WXML class 命名有规范 |
| 错误处理 | 6/10 | 部分页面吞错误、部分弹 Toast，不一致；但至少所有关键路径都有 catch |
| 测试覆盖 | 8/10 | 后端 31 个测试文件覆盖所有路由，前端缺少单元测试 |
| 性能 | 7/10 | 骨架屏使用得当，但大量页面在 onShow 时全量 reload，可加缓存 |
| 可维护性 | 7/10 | 命名清晰、注释充分，但 Canvas 代码和流式打印有重复，可抽取 |
| 安全 | 8/10 | JWT 认证、401 自动重登录、幂等键去重、差分隐私噪声均已实现 |
| **综合** | **7.7/10** | 工程化水平在大学生项目中属于上乘，但仍有提升空间 |

---

## 六、优先建议行动项

### 本周内（阻塞 Phase 3 比赛）
1. ⚡ **判断 PRD 日报触达是否纳入比赛范围** — 若是，R1 首页卡片 + R3 事件判定必须启动
2. ⚡ **calendar 页面数据接口对接** — 要么补齐后端 API，要么降级为纯静态展示
3. ⚡ **检查 YOLO 模型就绪度** — 若比赛需要真机识货演示，立即启动训练

### 本月内（代码质量提升）
4. 抽取 `utils/chart.js` 统一 Canvas 2D 绘图（减少 sandbox/dashboard/report 的重复代码）
5. 抽取 `utils/mixins/stream-text.js` 统一逐字打印逻辑
6. 拆分 app.js → request.js / auth.js / theme.js 独立模块
7. 前端引入至少 3 个关键页面的单元测试（voice/pos/inventory）

### 下阶段（Phase 3 完）
8. 真机部署验证（树莓派 5 跑通完整链路）
9. 实地商户测试 + 数据采集
10. 论文 + 答辩 PPT（已有 demo-script.md 和 experiment-report.md 框架）

---

## 七、正面认可

团队在以下方面做得很好：

1. **后端质量扎实**：25 个路由、31 个测试、84 个测试通过，有完整的服务层抽象和种子数据
2. **前端页面超额完成**：README 承诺的 9 个核心页面落地了 13+ 个完整功能页，还加了 pos/finance/devices/catalog 等增值功能
3. **文档完整性高**：14 份文档覆盖 API / 数据库 / 部署 / 硬件 / 训练 / 答辩 / 隐私等
4. **安全与合规意识到位**：差分隐私、幂等去重、JWT 轮转、隐私设计文档俱备
5. **离线与容错设计好**：offline-sync.js 离线队列+同步、所有 API 有降级演示模式
6. **代码注释清晰**：关键逻辑有注释，calendar 明确标注 [P2 占位]，不虚假承诺

---

> 报告由 Senior Developer（吴八哥）出具，请团队根据优先建议行动项确定下阶段排期。
