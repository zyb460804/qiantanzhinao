# 核心四页改造 PRD（经营台 · 记一笔 · 参谋 · 库存）

> **版本**: v1.0 | **日期**: 2026-07-12 | **范围**: index, voice, advisor, inventory

---

## 前置原则

**不改动页面信息架构**。这三个页面的结构已经是经过思考的——经营台的总览+待办+工具入口、记一笔的双模式+状态机、参谋的推送→建议→沙盘三级递进，方向都对。改造聚焦三件事：

1. **去伪存真**——把硬编码变数据驱动
2. **补交互缺口**——下拉刷新、草稿保护、进度反馈
3. **削冗余优化**——减少重复 setData、修骨架屏时机

---

# 一、经营台 (index)

## 1.1 现有问题

| 问题 | 严重度 | 影响 |
|------|--------|------|
| `rebuildTasks()` 被调用 3-4 次，每次 setData 全量 | 中 | 页面抖动，浪费性能 |
| 骨架屏固定 420ms，与 API 响应脱节 | 中 | 快网闪白屏，慢网先露空白再出内容 |
| 无下拉刷新 | 高 | 摊主无法手动刷新，只能切 Tab |
| API 失败全页面归零 | 高 | 网络波动时用户看到空页面 |
| `riskScore` 加载但从未在 WXML 渲染 | 低 | 浪费数据流 |
| `lowStockCount` 判定一刀切（qty ≤ 10） | 低 | 品类差异未考虑 |

## 1.2 改造方案

### 改 1：下拉刷新

```
onPullDownRefresh → 重新 loadHomeData() + loadWeather()
→ wx.stopPullDownRefresh()
```

在 `index.json` 加 `"enablePullDownRefresh": true`。无需额外 UI 改动，使用微信原生下拉动画。

### 改 2：骨架屏与数据加载解耦

**现状**：`onReady` 中 `setTimeout(420ms)` 写死。

**改后**：

```
onShow → 发起 3 个 API → 全部返回后 → 关闭骨架屏
                      → 任一失败 → 保留骨架屏 → 错误提示
```

具体实现：用计数器 `_pendingRequests = 3`，每个 API 完成 `_pendingRequests--`，到 0 后关闭骨架屏。超时 8s 强制关闭（防止永久卡死）。

### 改 3：合并 setData，削减少量调用

**现状**：`rebuildTasks()` 在 3 个 API 的 `.then()` 和 `.catch()` 中各调一次。

**改后**：用 `Promise.allSettled()` 或收集所有数据后统一调用一次 `rebuildTasks()` + 一次 `setData`：

```javascript
function loadHomeData() {
  var self = this;
  var results = { dashboard: null, inventory: null, logs: null };

  Promise.all([
    app.request({ url: '/twin/dashboard' }).then(function(res) { results.dashboard = res.data; }),
    app.request({ url: '/inventory/current' }).then(function(res) { results.inventory = res.data; }),
    app.request({ url: '/voice/logs', data: { page: 1, limit: 3 } }).then(function(res) { results.logs = res.data; })
  ]).then(function() {
    self.setData({
      todayRevenue: results.dashboard.today_revenue || 0,
      todayCost: results.dashboard.today_cost || 0,
      todayProfit: results.dashboard.today_profit || 0,
      inventoryCategoryCount: results.inventory.category_count || 0,
      inStockCount: results.inventory.in_stock_count || 0,
      expiringCount: results.dashboard.expiring_count || 0,
      recentRecords: (results.logs || []).slice(0, 3),
      tasks: self.rebuildTasks(results),
      loading: false,
    });
  }).catch(function() {
    self.setData({ loading: false, loadError: true });
  });
}
```

### 改 4：API 失败降级缓存

加一个本地缓存层：API 成功时把 `dashboard/inventory/logs` 数据存到 `wx.setStorageSync('homeCache', ...)`，失败时读缓存作为降级展示，并在顶部显示「数据可能不是最新，下拉刷新 →」的弱提示条。

### 改 5：显示风险分（小改动，大价值）

把目前已计算但浪费的 `riskScore` 在库存状态卡片旁加一个风险指示：

```
库存状态                                        
品类 12    在库 286kg    临期 3    风险 ██░░ 35/100
```

风险分颜色：0-30 绿色、31-60 琥珀、61-100 红色。

---

## 1.3 用户故事

> 作为摊主，当我下拉「经营台」页面时，希望看到最新的经营数据，以便确认刚刚记的账已经反映在数字里。

> 作为摊主，当菜市场网络不好数据加载失败时，希望至少能看到上一次的数据，以便我仍然能大概了解经营状况。

## 1.4 P0 / P1

| 优先级 | 改动 | 工作量 |
|--------|------|--------|
| P0 | 下拉刷新 | 0.1 人日 |
| P0 | 合并 3 次 setData 为 1 次 | 0.2 人日 |
| P0 | 骨架屏改为数据驱动关闭 | 0.1 人日 |
| P1 | API 失败读缓存降级 | 0.3 人日 |
| P1 | 风险分展示 | 0.1 人日 |

---

# 二、记一笔 (voice)

## 2.1 现有问题

| 问题 | 严重度 | 影响 |
|------|--------|------|
| 模式切换丢草稿 | 高 | 文字输入后切语音，已输入内容消失 |
| 上传无进度 | 中 | 用户不知道还要等多久 |
| 流式字幕与 API 解耦 | 中 | 字幕播完了 API 还没返回，体验断档 |
| 最近记录无「加载更多」 | 低 | 只有 5 条，无分页 |
| 撤销无反撤销 | 低 | 误操作无法恢复 |
| demoMode 代码残留 | 低 | 生产代码中有 mock 分支 |

## 2.2 改造方案

### 改 1：草稿保护

切换模式前检查：

```javascript
switchToText: function() {
  // 语音模式 → 文字模式：不做特殊处理，清空语音状态即可
  this.setData({ mode: 'text' });
},

switchToVoice: function() {
  var self = this;
  if (this.data.textInput && this.data.textInput.trim()) {
    wx.showModal({
      title: '切换到语音',
      content: '文字输入的内容将会丢失，确定切换吗？',
      success: function(res) {
        if (res.confirm) {
          self.setData({ mode: 'voice', textInput: '' });
        }
      }
    });
  } else {
    this.setData({ mode: 'voice' });
  }
},
```

### 改 2：上传进度

利用 `wx.uploadFile` 的 `onProgressUpdate` 回调（基础库 2.10.0+）：

```javascript
var uploadTask = wx.uploadFile({
  url: app.apiBase + '/voice/upload',
  filePath: tempFilePath,
  name: 'file',
  formData: { dialect: this.data.dialect },
  success: function(res) { /* ... */ },
});

uploadTask.onProgressUpdate(function(res) {
  self.setData({ uploadProgress: res.progress }); // 0-100
});
```

状态文案变化：`正在上传 45% → 正在识别 → 整理中`

### 改 3：流式字幕与 API 响应时序对齐

**现状流程**：
```
上传完成 → ASR文本返回 → streamReply() 打字动画 → 动画结束 → parseText() API → 结果
                            ↑_____ 此时 API 可能还没返回，字幕消失，空白等待 _____↑
```

**改后流程**：
```
上传完成 → ASR文本返回 → 显示原文卡片
         → streamReply() 打字（直到 API 返回为止，不设定时结束）
         → parseText() API 返回 → 打断打字 → 显示结果
```

关键：`streamReply` 不设固定字数限制，改为轮询检查 `_parseResult` 是否已有值，有则立即中断打字跳到结果展示。

### 改 4：最近记录支持加载更多

底部加「查看更多记录 →」，点击跳转一个新的 `voice-history` 页面（或展开更多条）。

### 改 5：清除 demoMode

删掉 `voice.js` 中所有 `if (demoMode)` 分支。如需开发测试，通过 `app.globalData.debugMode` 控制。

### 改 6：反撤销（P2）

撤销时在记录卡片位置显示 3 秒倒计时的「已撤销 · 点此恢复」提示，期间可恢复。

---

## 2.3 用户故事

> 作为摊主，当我在文字模式下打了一段内容后误触切换到语音，希望我的文字不要丢失。

> 作为摊主，当我录音上传时，希望看到上传进度，以便知道大概要等多久。

> 作为摊主，当 AI 在整理我的语音时，希望 AI 的"思考中"状态不要提前消失，以便知道它还在工作。

## 2.4 P0 / P1

| 优先级 | 改动 | 工作量 |
|--------|------|--------|
| P0 | 草稿保护（切换确认弹窗） | 0.1 人日 |
| P0 | 清除 demoMode | 0.1 人日 |
| P0 | 流式字幕与 API 对齐 | 0.2 人日 |
| P1 | 上传进度条 | 0.2 人日 |
| P1 | 最近记录加载更多 | 0.3 人日 |
| P2 | 撤销反撤销 | 0.2 人日 |

---

# 三、参谋 (advisor)

## 3.1 现有问题

| 问题 | 严重度 | 影响 |
|------|--------|------|
| **主动推送是硬编码** | **致命** | 「黄瓜临期」「明日小雨」永久显示，与实际数据无关——用户一旦发现这是假的，整个「智能参谋」的可信度崩塌 |
| 无建议反馈 | 高 | 后端无法学习，建议质量无法迭代 |
| 无建议历史 | 中 | 无法回顾「上次听了参谋的建议后效果如何」 |
| 建议不分时段 | 中 | 早市清货策略和晚市补货策略不同，但建议一样 |
| 加入采购默认 qty=10 | 低 | 硬编码默认值不合理 |
| 环境条数据失败时与推送矛盾 | 低 | 环境条不显示但推送硬编码了天气信息 |

## 3.2 改造方案

### 改 1（核心）：主动推送数据驱动化

这是**最高优先级改动**，不改这个参谋就是伪智能。

**设计思路**：主动推送不应是前端写死的数据，而应来自后端。利用已有的数据源动态生成：

| 推送卡片 | 触发条件 | 数据来源 |
|----------|---------|---------|
| 「xxx 商品临期 N 斤，今晚清货」 | `GET /twin/dashboard` 中 `expiring_count > 0` | 结合 `/inventory/current` 找到临期商品名 |
| 「明日有雨，叶菜走量放缓」 | `GET /env/today` 中降雨概率 > 60% | 天气接口 |
| 「连续 3 天销量下降，检查一下」 | `GET /reports/daily` 中 `revenue_change_pct < 0` 连续 3 天 | 报告接口 |
| 「今天还没记账，别忘了」 | `GET /voice/today-count` 中 count = 0 | 语音记账接口 |
| 「xx 商品库存不足，建议补货」 | `GET /inventory/current` 中 `lowStockCount > 0` | 库存接口 |

**规则引擎（P0 前端实现，P2 迁移后端）**：

```javascript
function buildPushCards(dashboard, inventory, weather, voiceCount, dailyReport) {
  var cards = [];

  // 规则1：临期提醒
  if (dashboard.expiring_count > 0) {
    cards.push({
      id: 'expiry',
      icon: '⏰',
      title: '有 ' + dashboard.expiring_count + ' 件商品临期，建议尽快处理',
      action: { text: '查看库存', page: '/pages/inventory/inventory' },
      urgency: 'high',
    });
  }

  // 规则2：天气预警
  if (weather && weather.rain_probability > 60) {
    cards.push({
      id: 'weather',
      icon: '🌧',
      title: '明日降雨概率 ' + weather.rain_probability + '%，叶菜走量可能放缓',
      action: { text: '调整进货', page: '/pages/sandbox/sandbox' },
      urgency: 'medium',
    });
  }

  // 规则3：今日未记账提醒
  if (voiceCount === 0) {
    cards.push({
      id: 'no_record',
      icon: '📝',
      title: '今天还没记账，别忘了记录今天的进账',
      action: { text: '去记账', page: '/pages/voice/voice' },
      urgency: 'high',
    });
  }

  // 规则4：连跌提醒
  if (dailyReport && dailyReport.revenue_change_pct < 0) {
    cards.push({
      id: 'decline',
      icon: '📉',
      title: '今日营收较昨日下降 ' + Math.abs(dailyReport.revenue_change_pct) + '%，关注一下',
      action: { text: '看报告', page: '/pages/report/report' },
      urgency: 'medium',
    });
  }

  return cards.slice(0, 3); // 最多 3 条
}
```

**关键**：推送卡片不再有「采纳/完成」按钮——推送的本质是**通知**而非**待办**。点击卡片跳转到对应页面执行操作。

### 改 2：建议反馈机制

每条建议卡片增加 👍 / 👎 按钮：

```javascript
onFeedback: function(e) {
  var adviceId = e.currentTarget.dataset.id;
  var feedback = e.currentTarget.dataset.type; // 'helpful' | 'not_helpful'

  app.request({
    url: '/behavior/feedback',
    method: 'POST',
    data: {
      recommendation_id: adviceId,
      feedback: feedback,
    }
  });

  this.setData({ ['feedback_' + adviceId]: feedback });
}
```

已有的 `POST /behavior/feedback` 接口可以使用，但需要确认其 schema 是否支持 `recommendation_id`。如果不支持，需扩展接口。

反馈后视觉变化：👍 变为实心绿色，👎 变为实心灰色，表示已反馈。

### 改 3：建议历史（P2）

参谋页面底部增加「历史建议 →」，跳转新页面展示过去 7 天的建议列表（含是否采纳的状态）。

需要后端新增 `GET /advice/history?days=7` 接口。

### 改 4：环境条数据兜底

环境条 API 失败时的处理：

- 不隐藏整个环境条，改为显示「天气数据暂不可用」的灰色弱提示
- 确保推送规则不引用失败的环境数据（避免硬编码天气数据与真实数据矛盾）

### 改 5：采购数量从建议中推断

在 `addToPurchase` 中，从建议的 `quantity_multiplier` 推断默认数量：

```javascript
var suggestedQty = it.suggested_qty || (it.qty * 0.8) || 5; // 建议数量 > 80%当前量 > 默认5
```

不再硬编码 `10`。

---

## 3.3 用户故事

> 作为摊主，当我有临期商品或天气即将变化时，希望参谋能基于真实数据主动提醒我，而不是显示固定的假消息。

> 作为摊主，当参谋给我一条建议时，我想告诉它「有用」或「没用」，以便它以后给我的建议更准。

> 作为摊主，当参谋建议我进货 50 斤时，我希望能看到它是基于什么判断的（天气/历史销量/当前库存），这样我才能放心照做。

## 3.4 P0 / P1

| 优先级 | 改动 | 工作量 |
|--------|------|--------|
| P0 | 主动推送改为数据驱动（前端规则引擎） | 0.5 人日 |
| P0 | 环境条兜底处理 | 0.1 人日 |
| P1 | 建议反馈 👍👎 | 0.2 人日 |
| P1 | 采购默认数量从建议推断 | 0.1 人日 |
| P2 | 建议历史页面 | 0.5 人日（含后端接口） |

---

# 四、库存 (inventory)

## 4.1 现有问题

库存页面相比前三页问题较少——结构清晰（分类柱状图 + 批次热力图 + 列表），功能可用。主要在交互细节：

| 问题 | 严重度 | 影响 |
|------|--------|------|
| 无下拉刷新 | 中 | 记账后库存不会自动更新 |
| 柱状图/热力图 Canvas 2D 初始化有时空白 | 低 | 首次加载或 resize 时偶现 |
| 列表无搜索 | 中 | 商品多了找不到 |
| 无库存预警视觉强化 | 低 | 低库存/临期商品在列表中不够显眼 |

## 4.2 改造方案

### 改 1：下拉刷新

同经营台，加 `enablePullDownRefresh`。

### 改 2：列表搜索

在库存列表顶部加一个搜索栏：

```
┌─────────────────────────────────┐
│  🔍  搜索商品名称               │
└─────────────────────────────────┘
```

前端过滤：输入时从已加载的库存列表中 `filter(item => item.name.includes(keyword))`。不做后端搜索（库存量小，前端过滤足够）。

### 改 3：库存预警视觉强化

列表中低库存（qty ≤ 阈值）或临期（3 天内到期）的商品行左边加彩色竖线：

- 低库存：琥珀色左边框
- 临期：红色左边框
- 两者都有：红色左边框 > 琥珀色（临期优先级更高）

### 改 4：空状态优化

当库存为空时，展示引导而非空白：

```
┌─────────────────────────────────┐
│                                 │
│         📦                      │
│     还没有库存记录              │
│   去记一笔语音记账，自动入库     │
│                                 │
│      [ 去记一笔 → ]             │
│                                 │
└─────────────────────────────────┘
```

---

## 4.3 用户故事

> 作为摊主，当我刚记完账后下拉库存页面，希望看到库存数据已更新。

> 作为摊主，当我有几十种商品时，希望通过搜索快速找到特定商品。

> 作为摊主，当我打开库存页面时，希望临期和低库存的商品一眼就能看到，不用逐行检查。

## 4.4 P0 / P1

| 优先级 | 改动 | 工作量 |
|--------|------|--------|
| P0 | 下拉刷新 | 0.1 人日 |
| P0 | 库存预警彩色左边框 | 0.1 人日 |
| P0 | 空状态引导 | 0.1 人日 |
| P1 | 列表搜索 | 0.2 人日 |

---

# 五、四页改造总览

## 工作量汇总

| 页面 | P0 人日 | P1 人日 | 关键价值 |
|------|---------|---------|---------|
| 经营台 | 0.4 | 0.4 | 性能+稳定性 |
| 记一笔 | 0.4 | 0.5 | 核心体验打磨 |
| 参谋 | 0.6 | 0.3 | **去伪存真，建立信任** |
| 库存 | 0.3 | 0.2 | 易用性补全 |
| **合计** | **1.7** | **1.4** | **总 3.1 人日** |

## 优先级排序

```
参谋 P0（致命问题，信任崩塌）
  → 经营台 P0（高频页面，体验提升大）
    → 记一笔 P0（核心功能打磨）
      → 库存 P0（低风险快速补）
        → 全部 P1
```

## 后端缺口

| 接口 | 用途 | 页面 | 优先级 |
|------|------|------|--------|
| `GET /advice/history?days=7` | 建议历史 | 参谋 | P2 |
| 扩展现有 `/behavior/feedback` 支持 `recommendation_id` | 建议反馈 | 参谋 | P1 |
| `GET /inventory/search?keyword=xxx` | 库存搜索（可选，前端过滤可替代） | 库存 | P2 |

## 需要同步的后端改动

当前 `/advice/daily` 接口已经返回 `recommendations`（含商品名、建议内容、把握度、判断依据），数据结构够用，不需要改。

`/behavior/feedback` 当前 schema 需要确认是否支持 `recommendation_id` 字段。查看 `schemas/behavior.py`：
- `POST /behavior/feedback` 的 `FeedbackRequest` schema 需要检查。

---

## 六、风险

| 风险 | 缓解 |
|------|------|
| 参谋主动推送规则引擎如果条件判断有 bug，可能推送错误/矛盾信息 | 规则引擎抽离为独立模块 + 单元测试 |
| 经营台合并 setData 后，如果 Promise.all 中某个 API 超时，会阻塞全部展示 | 加 8s 超时，超时后只展示已返回的数据 |
| 记一笔草稿保护可能被摊主觉得「多此一举」 | 仅在文字内容 > 10 字时才弹窗确认 |
| 库存搜索前端过滤在数据量大时（>1000 条）可能卡顿 | 当前小微商户库存量不会超过 200 SKU，前端过滤够用 |
