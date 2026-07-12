# 千摊智脑 · 小程序前端代码规范与技术指导

> **文档版本**: v1.0  
> **日期**: 2026-07-12  
> **编写人**: Senior Developer（高级开发工程师）  
> **适用范围**: 千摊智脑小程序前端团队全体成员  
> **背景**: 基于 2026-07-12 全量代码审查与 18 项修复实践总结

---

## 一、本次重构总览

本次对小程序 18 个页面 + 8 个工具模块进行了全量审查，共发现并修复 **18 类问题**：

| 类别 | 数量 | 代表问题 |
|------|------|----------|
| 严重 BUG（功能不可用） | 8 | trace 页 WXML 调用方法、offline-media 鉴权失效、recorder 监听器泄漏 |
| 占位/空壳功能 | 7 | finance 图表未实现、profile 4 个"即将上线"Toast、ops 导出死按钮 |
| 代码质量问题 | 3 | inventory 重复搜索框、report Canvas 时序竞态、sandbox 重复请求 |

**修复涉及文件**: 22 个（.js / .wxml / .json）  
**新增文件**: 4 个（doc 页面 + 本文档）  
**新增页面**: pages/doc/doc（文档展示页）

---

## 二、必读：微信小程序 7 大陷阱

以下 7 个陷阱在本次审查中全部命中，是团队最常犯的错误。**每个开发者必须熟记**。

### 陷阱 1：WXML 不支持调用页面方法

```html
<!-- ❌ 错误：WXML 的 mustache 语法不支持调用方法 -->
<text>{{statusColor()}}</text>
<text>{{isSafe() ? '✓' : '⚠'}}</text>
<view wx:for="{{certList()}}">

<!-- ✅ 正确：在 JS 中算好派生字段，写入 data，WXML 只引用字段 -->
<text>{{statusColor}}</text>
<text>{{bannerIcon}}</text>
<view wx:for="{{certList}}">
```

**规则**: WXML 数据绑定只能引用 `data` 字段和 WXS 函数。任何需要计算的派生值，必须在 JS 的 `setData` 中预先算好。本次 trace 页因此导致整个溯源结果页无法渲染。

### 陷阱 2：streamText 返回的是对象，不是 timer id

```javascript
// ❌ 错误：用 clearInterval 清理 streamText 的返回值
this._typingTimerId = streamText(text, onUpdate, done);
// ...
clearInterval(this._typingTimerId); // 无效！返回的是 {cancel: function}

// ✅ 正确：调用 .cancel() 方法
this._typingTimerId = streamText(text, onUpdate, done);
// ...
if (this._typingTimerId && typeof this._typingTimerId.cancel === 'function') {
  this._typingTimerId.cancel();
}
```

**规则**: 使用第三方工具函数时，必须确认其返回值类型。`utils/stream-text.js` 的 `streamText()` 返回 `{cancel: function}` 对象，不是 `setInterval` 的 timer id。本次 voice.js 和 advisor.js 都踩了这个坑，导致页面隐藏后定时器持续运行。

### 陷阱 3：字段名一致性（token vs accessToken）

```javascript
// ❌ 错误：offline-media.js 读 app.globalData.token
var token = app.globalData.token || ''; // 永远是空字符串！

// ✅ 正确：app.js 中定义的是 accessToken
var token = app.globalData.accessToken || '';
```

**规则**: 全局字段名必须在 `app.js` 中统一定义，其他文件引用时必须核对。建议在 `app.js` 顶部用注释列出所有 `globalData` 字段名清单。

### 陷阱 4：URL 拼接双重路径

```javascript
// ❌ 错误：apiBase 已含 /api/v1，再拼一次变成 /api/v1/api/v1/media/upload
url: (app.globalData.apiBase || '') + '/api/v1/media/upload'

// ✅ 正确：apiBase 已含 /api/v1，直接拼端点路径
url: (app.globalData.apiBase || '') + '/media/upload'
```

**规则**: `app.globalData.apiBase` 的值是 `http://127.0.0.1:8000/api/v1`，已包含版本前缀。所有 `app.request()` 和手动拼接 URL 时，只需追加端点路径（如 `/voice/upload`），不要重复 `/api/v1`。

### 陷阱 5：type-chip 点击的 e.detail.value 为 undefined

```javascript
// ❌ 错误：onDevField 只读 e.detail.value，chip 点击时为 undefined
onDevField: function (e) {
  var v = e.detail.value; // type-chip 点击时这里是 undefined
  df[f] = v;
}

// ✅ 正确：区分 input 和 chip 点击
onDevField: function (e) {
  var f = e.currentTarget.dataset.field;
  var v = (e.detail.value !== undefined && e.detail.value !== null)
    ? e.detail.value
    : e.currentTarget.dataset.val;
  df[f] = v;
}
```

**规则**: `bindinput` 事件的 `e.detail.value` 是输入值；`bindtap` 事件的 `e.detail.value` 是 `undefined`。当一个方法同时服务于 input 和 chip 点击时，必须从 `dataset.val` 兜底读取。本次 devices.js 的设备类型选择因此失效。

### 陷阱 6：RecorderManager 监听器重复注册

```javascript
// ❌ 错误：每次 startRecording 都注册一次 onStop，导致回调叠加
function startRecording() {
  return new Promise((resolve, reject) => {
    recorderManager.onStop((res) => { ... }); // 每次调用都叠加！
    recorderManager.start({ ... });
  });
}

// ✅ 正确：模块顶层注册一次，用变量保存当前 promise 的回调
var _currentResolve = null;
recorderManager.onStop(function (res) {
  if (!_currentResolve) return;
  var resolve = _currentResolve;
  _currentResolve = null;
  resolve({ tempFilePath: res.tempFilePath });
});

function startRecording() {
  return new Promise(function (resolve, reject) {
    _currentResolve = resolve;
    recorderManager.start({ ... });
  });
}
```

**规则**: `wx.getRecorderManager()` 返回的是单例。`onStop`/`onError` 等监听器只需注册一次，重复注册会导致回调叠加。任何单例式的微信 API（RecorderManager、WebSocket 等）都适用此规则。

### 陷阱 7：Math.abs() 等 JS 方法不能在 WXML 中调用

```html
<!-- ❌ 错误：WXML 不支持调用 Math.abs() -->
<text class="{{Math.abs(settlement.diff_amount) < 0.01 ? 'ok' : 'warn'}}">

<!-- ✅ 正确：在 JS 中预计算布尔值 -->
// JS: data.isBalanced = Math.abs(diff_amount) < 0.01
<text class="{{settlement.isBalanced ? 'ok' : 'warn'}}">
```

**规则**: WXML 的 mustache 表达式只支持简单运算（三元、比较、逻辑），不支持调用任何 JS 方法（`Math.abs`、`Array.isArray`、`String.split` 等）。所有方法调用的结果必须在 JS 中算好后写入 data。

---

## 三、代码规范

### 3.1 错误处理规范

**所有 `app.request()` 必须有 `.catch()`**，且必须给用户反馈：

```javascript
// ✅ 正确：catch 中给用户反馈
app.request({ url: '/expenses' }).then(function (data) {
  self.setData({ expenses: data || [] });
}).catch(function () {
  self.setData({ expenses: [] });
  wx.showToast({ title: '加载失败', icon: 'none' });
});

// ❌ 错误：静默吞错（dashboard/report 的通病）
app.request({ url: '/twin/dashboard' }).catch(function () { return null; });
```

**例外**: 并行请求中的非关键 API 可以静默降级，但 `onShow` 中如果全部失败，必须显示错误状态。

### 3.2 Canvas 绘制时序

**禁止用固定 `setTimeout` 等 Canvas DOM 就绪**，改用 `wx.nextTick` 或 `SelectorQuery`：

```javascript
// ✅ 正确：用 nextTick
self.setData({ result: data }, function () {
  wx.nextTick(function () { self.renderChart(); });
});

// ✅ 更可靠：用 Chart.initCanvas 内部的 SelectorQuery（已封装）
Chart.initCanvas(this, '#myCanvas').then(function (canvas) {
  if (!canvas) return;
  Chart.drawBarChart(canvas.ctx, canvas.width, canvas.height, data, opts);
});

// ❌ 错误：固定延时，低端机竞态
setTimeout(function () { self.renderChart(); }, 280);
```

### 3.3 组件注册规范

**WXML 中使用的每个自定义组件，必须在页面的 `.json` 中声明**：

```json
{
  "usingComponents": {
    "icon": "/components/icon/icon",
    "mascot": "/components/mascot/mascot"
  }
}
```

**规则**:
- 声明了但未使用的组件应该移除（如 inventory 的 `stock-chart`）
- 使用了但未声明的组件会导致渲染空白（如 calendar 的 `mascot`）
- 新建页面时，从其他页面复制 `.json` 作为模板，确保 `navigationBarBackgroundColor` 和 `navigationBarTextStyle` 统一

### 3.4 onShow 缓存策略

**避免每次 `onShow` 都全量请求**，应加 TTL 缓存：

```javascript
var CACHE_TTL = 300000; // 5 分钟

onShow: function () {
  var cached = wx.getStorageSync('myPageCache');
  if (cached && cached.ts && (Date.now() - cached.ts < CACHE_TTL)) {
    this._applyCache(cached);  // 先用缓存渲染
    this._fetchRemote(false);  // 后台静默刷新
  } else {
    this._fetchRemote(true);   // 无缓存,显示骨架屏
  }
}
```

**index.js 已实现此模式**，其他页面（dashboard、inventory、advisor 等）应参照实现。

### 3.5 日期输入用 picker

**禁止用 `<input type="text">` 让用户手输日期**：

```html
<!-- ✅ 正确：用 picker -->
<picker mode="date" value="{{form.date}}" data-field="date" bindchange="onField">
  <view class="form-input">{{form.date || '选择日期'}}</view>
</picker>
<picker mode="month" value="{{month}}" bindchange="changeMonth">
  <view class="form-input">{{month}} ▾</view>
</picker>

<!-- ❌ 错误：手输 YYYY-MM-DD -->
<input type="text" placeholder="日期 YYYY-MM-DD" bindinput="onField" />
```

### 3.6 公开页面鉴权

**公开页面（如消费者扫码溯源）必须传 `auth: false`**：

```javascript
// ✅ 正确
app.request({
  url: '/food-safety/trace/' + code,
  auth: false,  // 跳过 JWT，app.js 判断 auth !== false
});

// ❌ 错误：noAuth 字段名不对，app.js 不认
app.request({ url: '/food-safety/trace/' + code, noAuth: true });
```

**规则**: `app.js` 的 `_requestOnce` 判断的是 `options.auth !== false`。只有 `auth: false` 才会跳过鉴权。

---

## 四、工具模块使用指南

### 4.1 utils/stream-text.js — 流式逐字打印

```javascript
var streamText = require('../../utils/stream-text').streamText;

// 使用
this._timerId = streamText(text, function (display) {
  this.setData({ streamingText: display });
}.bind(this), function () {
  // 打完回调
});

// 清理（重要！）
if (this._timerId && typeof this._timerId.cancel === 'function') {
  this._timerId.cancel();
}
this._timerId = null;
```

### 4.2 utils/chart.js — Canvas 图表

```javascript
var Chart = require('../../utils/chart');

// 绘制柱状图
Chart.initCanvas(this, '#myCanvas').then(function (canvas) {
  if (!canvas) return;
  Chart.drawBarChart(canvas.ctx, canvas.width, canvas.height, data, {
    valueKey: 'amount',
    labelKey: 'name',
    colors: ['#2BA24C', '#D9524A'],
  });
});

// 绘制折线图
Chart.drawLineChart(canvas.ctx, canvas.width, canvas.height, data, {
  series: [{ key: 'revenue', color: '#175C45', axis: 'left' }],
});
```

### 4.3 utils/theme.js — 时段皮肤

```javascript
var Theme = require('../../utils/theme');

// 一站式应用（推荐）
onShow: function () { Theme.apply(this); }

// 不要自己实现 getSkinByHour + setData，会忽略 skinManual 手动设置
```

### 4.4 utils/recorder.js — 录音

```javascript
var recorder = require('../../utils/recorder');

// 开始录音（返回 Promise）
recorder.startRecording().then(function (res) {
  console.log(res.tempFilePath, res.duration);
}).catch(function (err) {
  console.error(err);
});

// 停止
recorder.stopRecording();
```

**注意**: `onStop`/`onError` 已在模块顶层注册一次，不要在页面中直接调用 `wx.getRecorderManager().onStop()`。

---

## 五、后续技术债优先级

以下问题本次未修复，按优先级排序：

| 优先级 | 问题 | 位置 | 建议 |
|--------|------|------|------|
| P1 | POS 离线队列与 offline-sync.js 双队列 | pos.js / offline-sync.js | 统一到 SyncEngine 模式 |
| P1 | 请求中冗余 merchant_id | 所有页面 | 渐进式删除（后端从 JWT 提取） |
| P2 | 城市硬编码上海 | index.js / advisor.js | 加商户设置项，从 profile 读取 |
| P2 | var self = this → 箭头函数 | 所有页面 | 逐文件迁移（基础库 2.0+ 支持） |
| P2 | app.js reduceMotion 未持久化 | app.js:31 | 从 storage 读取，连接设置开关 |
| P2 | dashboard onShow 无缓存重复请求 | dashboard.js | 参照 index.js 的 TTL 缓存模式 |
| P3 | 无全局错误上报 | app.js onError | 接入微信后台或 Sentry |
| P3 | voice.js demo 模式散落 | voice.js:85-91 | 提取到独立 mock service 模块 |
| P3 | utils/storage.js 死代码 | utils/storage.js | 要么全量接入，要么删除 |

---

## 六、代码审查 Checklist

每次提交 PR 前，对照以下清单自查：

- [ ] WXML 中没有调用页面方法（`{{method()}}`）
- [ ] WXML 中没有调用 JS 内置方法（`Math.abs()`、`Array.isArray()` 等）
- [ ] 所有 `app.request()` 都有 `.catch()` 且给用户反馈
- [ ] 自定义组件在 `.json` 中已声明（用了的声明，没用的移除）
- [ ] `streamText` 的返回值用 `.cancel()` 清理，不是 `clearInterval`
- [ ] `onHide`/`onUnload` 中清理了所有定时器和监听器
- [ ] 日期输入用 `<picker>` 而非 `<input type="text">`
- [ ] 公开页面传 `auth: false`（不是 `noAuth: true`）
- [ ] URL 拼接不重复 `/api/v1`
- [ ] Canvas 绘制用 `wx.nextTick` 或 `SelectorQuery`，不用固定 `setTimeout`
- [ ] 没有 `console.log` 调试代码残留
- [ ] 没有"即将上线"占位 Toast

---

## 七、页面功能完成度对照表

本次修复后各页面功能完成度：

| 页面 | 修复前 | 修复后 | 关键修复 |
|------|--------|--------|----------|
| trace | 渲染崩溃 | ✅ 可用 | WXML 方法调用→data 字段；auth 字段修正 |
| voice | 定时器泄漏 | ✅ 稳定 | clearInterval→.cancel() |
| advisor | 定时器泄漏 | ✅ 稳定 | clearInterval→.cancel() |
| devices | 类型选择失效 | ✅ 可用 | onDevField 支持 chip 点击 |
| pos | 结算样式失效 | ✅ 可用 | Math.abs→JS 预计算 isBalanced |
| calendar | mascot 渲染失败 | ✅ 可用 | 注册 mascot 组件；加下拉刷新 |
| index | 调试 Toast 残留 | ✅ 干净 | 清理 9 处 console.log + 调试 Toast |
| ops | 导出死按钮+搜索空壳 | ✅ 完整 | 实现 accounts 导出；客户搜索过滤 |
| finance | 图表未实现 | ✅ 完整 | 实现月度柱状图；picker 日期；错误处理 |
| profile | 4 个占位 Toast | ✅ 完整 | 创建 doc 页面；修复 applySkin 和 sale_qty |
| stocktake | 损耗金额占位 | ✅ 完整 | 实时预估损耗金额 |
| report | 月报字段置零 | ✅ 完整 | 补全 health_score；诚实表述 AI 总结 |
| sandbox | 重复请求 | ✅ 优化 | 缓存 product_id；nextTick 替代 setTimeout |
| vision | 成本未入库 | ✅ 完整 | 拼接成本文本；传 unit_cost；retakePhoto 副作用 |
| inventory | 重复搜索框 | ✅ 干净 | 删除重复搜索框；移除未用组件 |
| offline-media | 上传 404 | ✅ 可用 | 修正字段名和 URL 拼接 |
| recorder | 监听器泄漏 | ✅ 稳定 | 顶层注册一次 |

---

**文档维护**: 本文档随代码审查持续更新。发现新的陷阱或规范时，请追加到对应章节并通知团队。
