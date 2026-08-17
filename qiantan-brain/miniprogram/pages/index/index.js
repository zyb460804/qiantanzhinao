/** 经营台 v3.2 */
var app = getApp();
var Theme = require('../../utils/theme');
var invStatus = require('../../utils/inventory-status');
var PushRules = require('../../utils/push-rules');
var CACHE_KEY = 'homeCache';
var CACHE_TTL = 300000; // 缓存有效期 5 分钟

Page({
  data: {
    merchantName: '', skin: 'noon', greeting: '你好',
    showSkeleton: false, loadError: false, staleData: false,
    todayRevenue: 0, todayCost: 0, todayProfit: 0, riskScore: 0,
    riskLevel: '低风险', riskColor: '#357d48',
    expiringCount: 0, inventoryCategoryCount: 0, inStockCount: 0, lowStockCount: 0,
    weather: null, recentRecords: [],
    todayTasks: [{ id: 'steady', tone: 'good', glyph: '稳',
      title: '当前没有紧急待办', desc: '经营状态平稳，可以查看今日建议安排下一轮进货。',
      action: '看建议', route: 'advisor' }],
  },

  _loadTimer: null,

  onLoad: function () {
    // 首次加载,数据获取在 onShow 中处理
  },

  onShow: function () {
    try {
      // Theme.apply 只设置 skin/skinClass, 不设置 greeting;
      // 这里一并补上时段问候语, 让"早上好/下午好/晚上好"在页面进入时即生效
      this.setData({
        merchantName: app.globalData.merchantName || '老板',
        greeting: Theme.getGreeting(),
      });
      Theme.apply(this);
    } catch (e) {
      app.logError('index/onShow', e, { silent: true });
    }

    // 策略1: 有新鲜缓存? 直接渲染, 后台静默刷新
    var cached = wx.getStorageSync(CACHE_KEY);
    if (cached && cached.ts && (Date.now() - cached.ts < CACHE_TTL)) {
      this._applyCache(cached);
      this._fetchRemote(false);
    } else {
      // 策略2: 无缓存或过期 → 先用默认内容渲染, 后台静默拉取
      this._fetchRemote(false);
    }

    this.loadWeather();
  },

  onReady: function () {
    // 绝对保险: 最多 4 秒后强制隐藏骨架屏, 保证内容一定出现
    var self = this;
    setTimeout(function () {
      if (self.data.showSkeleton) {
        self.setData({ showSkeleton: false });
      }
    }, 4000);
  },

  onHide: function () {
    // 页面隐藏时清除定时器, 防止内存泄漏
    if (this._loadTimer) { clearTimeout(this._loadTimer); this._loadTimer = null; }
  },

  onUnload: function () {
    if (this._loadTimer) { clearTimeout(this._loadTimer); this._loadTimer = null; }
  },

  onPullDownRefresh: function () {
    // 下拉刷新: 强制重新请求
    this.setData({ showSkeleton: true, staleData: false });
    this._fetchRemote(true, function () { wx.stopPullDownRefresh(); });
    this.loadWeather();
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    var greet = Theme.getGreeting();
    this.setData({ skin: skin, greeting: greet });
  },

  onSkinChange: function (e) {
    var skin = e.currentTarget.dataset.skin;
    app.globalData.skinManual = skin;
    this.applySkin(skin);
  },

  /* ── 远程数据获取 ── */

  /**
   * @param {boolean} showSkel - 是否在开始时显示骨架屏
   * @param {function} onDone  - 完成后的回调 (用于停止下拉刷新)
   */
  _fetchRemote: function (showSkel, onDone) {
    var self = this;
    var results = { dashboard: null, inventory: null, logs: null };
    var pending = 3;

    // 清除之前的定时器
    if (this._loadTimer) { clearTimeout(this._loadTimer); }

    // 超时保护 2.5 秒 (从 8s 大幅缩短)
    if (showSkel) {
      this._loadTimer = setTimeout(function () {
        self._loadTimer = null;
        if (pending > 0) {
          pending = 0;
          self._renderHomeData(results, true);
        }
        if (onDone) onDone();
      }, 2500);
    }

    function checkDone() {
      pending--;
      if (pending > 0) return;
      if (self._loadTimer) { clearTimeout(self._loadTimer); self._loadTimer = null; }
      self._renderHomeData(results, false);
      if (onDone) onDone();
    }

    function onFail(type, err) {
      app.logError('index/' + type, err, { silent: true });
      results[type] = null;
      checkDone();
    }

    // 3 个 API 并行请求
    app.request({ url: '/twin/dashboard' }).then(function (d) {
      results.dashboard = d; checkDone();
    }).catch(function (e) { onFail('dashboard', e); });

    app.request({ url: '/inventory/current' }).then(function (items) {
      results.inventory = Array.isArray(items) ? items : []; checkDone();
    }).catch(function (e) { onFail('inventory', e); });

    app.request({ url: '/voice/logs', data: { page: 1, limit: 3 } }).then(function (data) {
      results.logs = data; checkDone();
    }).catch(function (e) { onFail('logs', e); });
  },

  /* ── 渲染逻辑 ── */

  _renderHomeData: function (results, timedOut) {
    var self = this;
    var db = results.dashboard;
    var items = results.inventory || [];
    var logs = results.logs;

    // 全部失败 → 尝试缓存降级, 或显示错误状态
    var allFailed = !db && !items.length && !logs;
    if (allFailed || timedOut) {
      var cached = wx.getStorageSync(CACHE_KEY);
      if (cached && cached.ts) {
        this._applyCache(cached); // 用缓存填充, 标记可能过期
        return;
      }
      // 彻底没数据 → 显式标记加载失败，不再用零值伪装"一切正常"
      // (菜市场弱网环境下三接口全挂并不罕见，零值伪装会让摊主误判经营状态)
      this.setData({
        showSkeleton: false, loadError: true, staleData: false,
      });
      return;
    }

    // 至少有一项数据成功 → 正常渲染 (清掉之前的错误标记)
    if (this.data.loadError) {
      this.setData({ loadError: false });
    }

    // 正常渲染
    var inStock = 0, low = 0;
    items.forEach(function (item) {
      var qty = invStatus.resolveQty(item);
      if (invStatus.isInStock(qty)) inStock += 1;
      if (invStatus.isLowStock(qty, invStatus.resolveThreshold(item))) low += 1;
    });

    var recent = [];
    if (logs) {
      var list = Array.isArray(logs) ? logs : ((logs && logs.items) || []);
      recent = list.slice(0, 3).map(function (item) {
        var parsed = item.parsed_event || {};
        var fallback = [parsed.event_type, parsed.product, parsed.quantity && (parsed.quantity + (parsed.unit || ''))].filter(Boolean).join(' · ');
        var copy = {};
        Object.keys(item).forEach(function (key) { copy[key] = item[key]; });
        copy.display_text = item.asr_text || fallback || '一笔经营记录';
        return copy;
      });
    }

    // 保存最近一次成功渲染的原始数据，供天气异步返回后重建待办（降雨规则需要 weather）
    this._lastData = { db: db, items: items, recent: recent };

    var tasks = this._rebuildTasks(db, items, recent);

    var patch = {
      showSkeleton: false, loadError: false, staleData: false,
      todayRevenue: db ? (Number(db.today_revenue) || 0) : 0,
      todayCost: db ? (Number(db.today_cost) || 0) : 0,
      todayProfit: db ? (Number(db.today_profit) || 0) : 0,
      riskScore: db ? (Number(db.risk_score) || 0) : 0,
      expiringCount: db ? (Number(db.expiring_count) || 0) : 0,
      inventoryCategoryCount: items.length,
      inStockCount: inStock, lowStockCount: low,
      recentRecords: recent, todayTasks: tasks,
    };

    // 写缓存
    wx.setStorageSync(CACHE_KEY, { ts: Date.now(),
      todayRevenue: patch.todayRevenue, todayCost: patch.todayCost,
      todayProfit: patch.todayProfit, riskScore: patch.riskScore,
      expiringCount: patch.expiringCount,
      inventoryCategoryCount: patch.inventoryCategoryCount,
      inStockCount: patch.inStockCount, lowStockCount: patch.lowStockCount,
      recentRecords: recent, todayTasks: tasks,
    });

    this.setData(patch, function () { self._updateRiskLevel(); });
  },

  /** 从缓存快速恢复页面内容 */
  _applyCache: function (cached) {
    var self = this;
    this.setData({
      showSkeleton: false, loadError: false, staleData: true,
      todayRevenue: cached.todayRevenue || 0,
      todayCost: cached.todayCost || 0,
      todayProfit: cached.todayProfit || 0,
      riskScore: cached.riskScore || 0,
      expiringCount: cached.expiringCount || 0,
      inventoryCategoryCount: cached.inventoryCategoryCount || 0,
      inStockCount: cached.inStockCount || 0,
      lowStockCount: cached.lowStockCount || 0,
      recentRecords: cached.recentRecords || [],
      todayTasks: cached.todayTasks || [],
    }, function () { self._updateRiskLevel(); });
  },

  _updateRiskLevel: function () {
    var s = this.data.riskScore;
    var level = '', color = '';
    if (s <= 30) { level = '低风险'; color = '#357d48'; }
    else if (s <= 60) { level = '中等风险'; color = '#c8902a'; }
    else { level = '高风险'; color = '#c8392b'; }
    this.setData({ riskLevel: level, riskColor: color });
  },

  /** 待办卡片：消费统一规则引擎 utils/push-rules（与参谋页同源），仅做 index 版样式映射 */
  _rebuildTasks: function (db, items, recent, weather) {
    if (!db) {
      if (!recent) return [];
      return [{ id: 'partial', tone: 'normal', glyph: '记', title: '部分数据加载失败', desc: '请下拉刷新获取最新经营数据。', action: '下拉刷新', route: '' }];
    }
    var cards = PushRules.buildPushCards({
      dashboard: db,
      inventory: items,
      weather: weather || this.data.weather || null,
      recentCount: recent.length,
    });
    var tasks = cards.map(function (c) {
      return {
        id: c.id,
        tone: c.severity === 'info' ? 'normal' : c.severity,
        glyph: c.glyph,
        title: c.title,
        desc: c.desc,
        action: c.action,
        route: c.route,
      };
    });
    if (tasks.length === 0) {
      tasks.push({ id: 'steady', tone: 'good', glyph: '稳', title: '当前没有紧急待办', desc: '经营状态平稳，可以查看今日建议安排下一轮进货。', action: '看建议', route: 'advisor' });
    }
    return tasks.slice(0, PushRules.MAX_CARDS);
  },

  handleTask: function (e) {
    var route = e.currentTarget.dataset.route;
    if (!route) { wx.startPullDownRefresh(); return; }
    if (route === 'voice' || route === 'inventory' || route === 'advisor') return wx.switchTab({ url: '/pages/' + route + '/' + route });
    if (route) wx.navigateTo({ url: '/pages/' + route + '/' + route });
  },

  loadWeather: function () {
    var self = this;
    var city = app.getCity();
    app.request({ url: '/env/today', data: { city: city } }).then(function (d) {
      self.setData({ weather: d });
      // 天气晚于经营数据返回时，用最新天气重建待办（降雨预警规则依赖 weather）
      var last = self._lastData;
      if (last && last.db) {
        self.setData({ todayTasks: self._rebuildTasks(last.db, last.items, last.recent, d) });
      }
    }).catch(function () {});
  },

  // ── 页面导航（v3.3 收敛：删除 8+ 项工具跳转，只保留两大高频动作与页面内既有入口）──
  navigateToVoice: function () { wx.switchTab({ url: '/pages/voice/voice' }); },
  navigateToWaste: function () { wx.navigateTo({ url: '/pages/ops/ops?tab=waste' }); },
  navigateToInventory: function () { wx.switchTab({ url: '/pages/inventory/inventory' }); },
  navigateToAdvisor: function () { wx.switchTab({ url: '/pages/advisor/advisor' }); },
});
