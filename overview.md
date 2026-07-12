# 小程序工具功能实现与代码质量提升 — 完成总览

## 任务概述

对千摊智脑小程序 18 个页面 + 8 个工具模块进行了全量代码审查，发现并修复了 18 类问题，涵盖严重 BUG 修复、占位功能实现、代码质量优化三个层面。同时撰写了团队技术指导文档，帮助团队避免重复犯错。

## 完成的工作

### 第一批：严重 BUG 修复（8 项）

这些 BUG 导致功能完全不可用或存在资源泄漏：

1. **trace 溯源页渲染崩溃** — WXML 中调用页面方法 `statusColor()`/`isSafe()` 等，微信不支持；`noAuth: true` 字段名错误导致公开页面被强制登录；"重新查询"按钮传 event 给 loadTrace 导致报错。三项全部修复。
2. **offline-media 上传 404** — `app.globalData.token` 字段名错（应为 `accessToken`）；URL 拼接重复 `/api/v1`。两项修复后离线媒体上传恢复正常。
3. **devices 设备类型选择失效** — `onDevField` 只读 `e.detail.value`，type-chip 点击时为 undefined。改为从 `dataset.val` 兜底读取。
4. **recorder 监听器泄漏** — `onStop`/`onError` 在 `startRecording` 内部注册，每次调用叠加新监听器。改为模块顶层注册一次。
5. **voice/advisor 定时器泄漏** — `clearInterval` 清理 `streamText()` 返回的 `{cancel}` 对象，无效。改为调用 `.cancel()`。
6. **pos 结算样式失效** — WXML 中 `Math.abs()` 表达式无法求值。改为 JS 预计算 `isBalanced` 布尔值。
7. **calendar mascot 渲染失败** — `calendar.json` 未声明 mascot 组件。补全声明并加 `onPullDownRefresh`。
8. **index 调试 Toast 残留** — `onLoad` 中 `wx.showToast({title:'onLoad OK'})` 每次进首页都弹。清理 9 处 console.log + 调试 Toast。

### 第二批：占位功能实现（7 项）

这些功能此前是空壳或占位 Toast：

9. **ops 导出往来账与客户搜索** — "导出往来账"是死按钮（弹"不支持"）；客户搜索框存在但列表未过滤。实现了 accounts 导出（本地数据生成 CSV）和实时搜索过滤。
10. **finance 月度图表** — require 了 chart.js 但从未调用。实现了月度收支柱状图，picker 日期选择器，补全错误处理，统一 JSON 风格。
11. **profile 4 个占位功能** — 操作指南/常见问题/隐私政策/用户协议全是"即将上线"Toast。创建了独立的 doc 文档展示页，4 份实用文档内容。同时修复了 applySkin 不尊重手动皮肤、sale_qty 语义错误。
12. **calendar 备货建议** — "小智按天气推演"文案误导（实为硬编码规则）。改为诚实表述"按天气提醒"；采购量从 10 改为更保守的 5。
13. **stocktake 损耗金额** — 盘点进行中底部损耗金额硬编码 `--`。实现了基于已提交项的实时预估损耗金额（差异数 × 单位成本）。
14. **report 月报字段** — 月报模式 sales_ranking/health_score 等全置零；ai_summary 是硬编码模板。补全了 health_score 计算（基于利润率），ai_summary 改为诚实的数据汇总表述。
15. **vision 成本入库** — confirmStockIn 构造的文本不含 unitCost。改为拼接成本信息并传 unit_cost 字段；retakePhoto 不再强制开相机。

### 第三批：代码质量优化（3 项）

16. **sandbox 优化** — `/inventory/current` 结果未使用浪费请求；`runSimulation` 重复请求 `/vision/categories`。改为缓存 product_id 映射；renderChart 用 `wx.nextTick` 替代 `setTimeout`。
17. **inventory 清理** — WXML 有两个重复搜索框；stock-chart 组件声明但未使用。删除重复搜索框，移除未用组件声明。
18. **report Canvas 时序** — `setTimeout(..., 280)` 等 DOM 就绪存在竞态。改为 `wx.nextTick`。

### 技术指导文档

撰写了 `docs/frontend-code-standards.md`，包含：
- 微信小程序 7 大陷阱（WXML 方法调用、streamText 返回值、字段名一致性、URL 拼接、type-chip 点击、监听器重复注册、Math.abs 表达式）
- 代码规范（错误处理、Canvas 时序、组件注册、onShow 缓存、日期 picker、公开页面鉴权）
- 工具模块使用指南
- 后续技术债优先级
- 代码审查 Checklist

## 关键决策

- **doc 页面方案**：profile 的 4 个占位功能通过创建独立的 `pages/doc/doc` 页面实现，而非内嵌弹窗。原因：文档内容较长，独立页面体验更好，且方便后续扩展。
- **accounts 导出方案**：用本地已加载的 customers 数据生成 CSV，而非调后端端点。原因：后端无 `/ops/export/accounts` 端点，本地数据已足够。
- **月报 health_score 方案**：用利润率 × 2 估算（0-100）。原因：月报无商品级排行数据，利润率是最可得的健康指标。

## 涉及文件

修改 22 个文件，新增 4 个文件：
- `pages/trace/trace.js` + `.wxml`
- `pages/voice/voice.js`
- `pages/advisor/advisor.js`
- `pages/devices/devices.js`
- `pages/pos/pos.js` + `.wxml`
- `pages/calendar/calendar.js` + `.wxml` + `.json`
- `pages/index/index.js`
- `pages/ops/ops.js` + `.wxml`
- `pages/finance/finance.js` + `.wxml` + `.json`
- `pages/profile/profile.js`
- `pages/stocktake/stocktake.js` + `.wxml`
- `pages/report/report.js` + `.wxml`
- `pages/sandbox/sandbox.js`
- `pages/vision/vision.js`
- `pages/inventory/inventory.wxml` + `.json`
- `utils/offline-media.js`
- `utils/recorder.js`
- `app.json`
- **新增**: `pages/doc/doc.js` + `.wxml` + `.json`
- **新增**: `docs/frontend-code-standards.md`

## 后续建议

1. **POS + offline-sync 双队列统一**（P1）— 当前两套独立队列，建议统一到 SyncEngine
2. **城市硬编码**（P2）— index.js 和 advisor.js 都硬编码上海，需加商户设置项
3. **dashboard onShow 缓存**（P2）— 参照 index.js 的 TTL 缓存模式
4. **全局错误上报**（P3）— app.js 的 onError 接入微信后台或 Sentry
