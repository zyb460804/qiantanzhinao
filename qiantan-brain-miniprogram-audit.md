# 千摊智脑 · 小程序前端代码审查报告

**日期**：2026-07-12  
**审查人**：Senior Developer（高级开发工程师）  
**审查范围**：`miniprogram/` 全部 JS 文件（app.js + 15 个页面 + 3 个 utils + 组件）  
**后端状态**：✅ 已修复并运行（FastAPI 0.115.6，168 routes，auth 链路全通）

---

## 总体评价

前端代码**整体结构清晰**，`app.js` 作为统一入口封装了登录、鉴权、请求、上传、皮肤管理，页面代码职责分明。主要问题集中在**两套离线队列的协调**、**setInterval 泄漏**、和**少数边界处理不够严谨**。

---

## P1 — 影响功能正确性的问题

### 1. POS 离线队列与 `offline-sync.js` 是两套独立机制

| 组件 | 队列键 | 触发时机 | 同步方式 |
|------|--------|----------|----------|
| `pos.js` | `pendingPosOrders` (wx Storage) | `onShow()` | 自己遍历串行提交 |
| `offline-sync.js` | `qt_offline_queue` (wx Storage) | `app.js onLaunch` + 网络恢复 | SyncEngine 引擎 |

**互不知对方存在**。`app.js` 启动时只调 `offlineSync.getQueue().sync()`，不处理 POS 的 `pendingPosOrders`。POS 依赖自己的 `onShow` 触发 `syncPendingOrders()`。

**当前不会丢数据**（POS 自己会在 onShow 同步），但架构上不统一——两套队列、两套幂等键格式、两套重试策略。建议统一到 `offline-sync.js` 的 SyncEngine 模式，POS 订单也走同一个队列。

---

### 2. `voice.js` 流式打字的 `setInterval` 泄漏

```javascript
// voice.js:48-60 — streamReply
var t = setInterval(function () {
  i++;
  self.setData({ streamingText: text.slice(0, i) });
  if (i >= text.length) {
    clearInterval(t);  // ← 只有这里清理
    ...
  }
}, 38);
```

**场景**：用户在流式打字过程中离开 voice 页面（switchTab 到其他 tab），`onUnload` 不会清理这个 interval。后续 `setData` 会报错或操作已销毁的页面实例。

`advisor.js:65` 的 `saysReply` 有**完全相同的问题**。

**修复**：在 `onHide`/`onUnload` 中记录 interval ID 并清理。

---

### 3. 前端所有请求都在传冗余的 `merchant_id`

每一页的 `app.request()` 都传了 `merchant_id`：
```javascript
app.request({ url: '/twin/dashboard', data: { merchant_id: mid } })
```

但后端所有路由已经接入 `get_merchant_id`（从 JWT token 提取身份），**请求中的 merchant_id 参数被完全忽略**。

**影响**：
- 无功能影响（后端正确忽略）
- 给新开发者错误的安全印象：以为 `merchant_id` 可以客户端传
- 增加请求体积（每条请求多几十字节）

**建议**：渐进式清理，先删掉 `voice.js`/`index.js` 等高频页面的冗余参数，确认不影响后再全量移除。

---

### 4. `finance.js` 费用查询没有参数时后端返回 422

```javascript
// finance.js:27 — loadExpenses
app.request({ url: '/expenses?start=' + start.toISOString().slice(0, 10) + '&end=' + today })
```

URL 拼接方式本身没问题（GET 请求），但如果 `start`/`end` 计算异常（如时区问题导致格式不对），后端会 422。实测正常传参返回 `[]`（空数组）。

**建议**：用 `data` 参数传 query params，`app.request()` 会自动处理 GET 的 query string。

---

## P2 — 代码质量 / 可维护性问题

### 5. `voice.js:155` 的 `loadTodayCount` 干了太多事

函数名叫 `loadTodayCount`，实际上：
1. 调 `/voice/logs?page=1&limit=20` → 设置 `recentLogs`
2. 调 `/voice/today-count` → 设置 `todayCount`

两件事混在一个函数里，且第一个请求的 `recentLogs` 设置会在 `confirmRecord` 成功后再次被覆盖（`loadTodayCount()` 在 confirm 成功后又被调了一次）。

**建议**：拆成 `loadTodayCount()` + `loadRecentLogs()`。

---

### 6. `ops.js` 导出功能未实现

```javascript
// ops.js:106
doExport: function (e) {
  wx.showToast({ title: '导出功能需在服务端下载，请联系管理员', icon: 'none' });
}
```

功能入口在 UI 上显示，但点击只弹 toast。**要么实现，要么隐藏入口**。

---

### 7. `index.js:105` 硬编码中文 URL

```javascript
app.request({ url: '/env/today?city=%E4%B8%8A%E6%B5%B7' })
```

`%E4%B8%8A%E6%B5%B7` = "上海"。对大多数摊主用户来说上海可能不是他们的位置。

**建议**：从用户设置/微信定位获取城市，或提供设置入口。

---

### 8. 全局使用 `var self = this` 模式

ES5 风格的闭包保持 `this` 引用，小程序支持 ES6 箭头函数（基础库 2.0+），可以用箭头函数替代：

```javascript
// 现在
var self = this;
app.request(...).then(function (d) { self.setData(...) });

// 建议
app.request(...).then((d) => { this.setData(...) });
```

**好处**：减少代码噪音，降低 `self.xxx` 写错的风险。

---

### 9. `voice.js` demo 模式硬编码在生产代码中

```javascript
// voice.js:85-91
if (this.data.demoMode) {
  var mockTexts = ['今天进了白菜50斤，三毛钱一斤', ...];
  ...
}
```

Demo 逻辑散落在业务代码里。**建议**：移到独立的 mock service 模块，通过配置开关注入。

---

## P3 — 优化建议

### 10. 无全局错误上报

所有 `catch` 分支要么 `console.error` 要么吞掉了。生产环境无法感知前端错误。

**建议**：在 `app.js` 的 `onError`/`onUnhandledRejection` 中接入错误上报（如 Sentry / 微信后台）。

---

### 11. TabBar 图标确认

10 个 tabbar 图标文件均存在，尺寸需确认符合微信规范（推荐 81×81）。

---

### 12. `app.request()` 超时统一 15s

上传音频（voice/upload）可能需要更长时间，当前 `app.uploadFile` 默认 30s 合理，但 `app.request` 统一 15s。

---

## API 端点连通性测试（2026-07-12 实测）

| 端点 | 状态 | 备注 |
|------|------|------|
| `GET /api/v1/health` | 200 ✅ | |
| `POST /api/v1/auth/wechat-login` | 200 ✅ | dev mock 生效 |
| `GET /api/v1/auth/me` | 200 ✅ | JWT 鉴权正常 |
| `GET /api/v1/twin/dashboard` | 200 ✅ | |
| `GET /api/v1/inventory/current` | 200 ✅ | |
| `GET /api/v1/voice/logs` | 200 ✅ | |
| `GET /api/v1/advice/daily` | 200 ✅ | |
| `GET /api/v1/catalog/skus` | 200 ✅ | |
| `GET /api/v1/expenses` | 200 ✅ | 需传 start/end 参数 |
| `GET /api/v1/ops/waste-reasons` | 200 ✅ | |

---

## 优先级排序

| 序号 | 问题 | 严重度 | 修复难度 |
|------|------|--------|----------|
| 1 | setInterval 泄漏（voice + advisor） | P1 | 低（加 onHide 清理） |
| 2 | POS + offline-sync 双队列 | P1 | 中（需重构统一） |
| 3 | 请求中冗余 merchant_id | P1 | 低（渐进式删除） |
| 4 | ops 导出功能空壳 | P2 | 中（需实现或隐藏） |
| 5 | 硬编码城市 | P2 | 低（加设置项） |
| 6 | loadTodayCount 职责不清 | P2 | 低（拆分函数） |
| 7 | var self = this → 箭头函数 | P3 | 低（逐文件迁移） |
| 8 | demo 模式散落 | P3 | 低（提取模块） |
| 9 | 无错误上报 | P3 | 中（接入 Sentry 等） |

---

## 后端已知配置提醒

本地开发环境已配置：
- **FastAPI 0.115.6**（严禁升级到 0.139.0）
- **wechat-login dev mock**（`debug=True` 自动生效）
- `auth_allow_fallback=False`（生产安全，正常运行）

生产部署前需：
1. 设 `WECHAT_APPID` / `WECHAT_SECRET` 环境变量
2. 设 `DEBUG=false`（关闭 dev mock 和安全降级）
3. 设 `JWT_SECRET` 为 ≥32 字节生产密钥
