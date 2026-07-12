/** 经营台 v3.2 */
var app = getApp();
var Theme = require('../../utils/theme');
var CACHE_KEY = 'homeCache';
var CACHE_TTL = 300000; // 缓存有效期 5 分钟

Page({
  data: {
    merchantName: '', skin: 'noon', greeting: '你好',
    showSkeleton: false, loadError: false, staleData: false,
    todayRevenue: 0, todayCost: 0, todayProfit: 0, riskScore: 0,
    riskLevel: '低风险', riskColor: '#2BA24C',
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
      this.setData({ merchantName: app.globalData.merchantName || '老板' });
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

    // 全部失败 → 尝试缓存降级, 或显示空状态
    var allFailed = !db && !items.length && !logs;
    if (allFailed || timedOut) {
      var cached = wx.getStorageSync(CACHE_KEY);
      if (cached && cached.ts) {
        this._applyCache(cached); // 用缓存填充, 标记可能过期
        return;
      }
      // 彻底没数据 → 显示零值空状态 (不再卡骨架屏)
      this.setData({
        showSkeleton: false, loadError: false, staleData: false,
        todayRevenue: 0, todayCost: 0, todayProfit: 0,
        riskScore: 0, expiringCount: 0,
        inventoryCategoryCount: 0, inStockCount: 0, lowStockCount: 0,
        recentRecords: [], todayTasks: [{ id: 'steady', tone: 'good', glyph: '稳',
          title: '当前没有紧急待办', desc: '经营状态平稳，可以查看今日建议安排下一轮进货。',
          action: '看建议', route: 'advisor' }],
      }, function () { self._updateRiskLevel(); });
      return;
    }

    // 正常渲染
    var inStock = 0, low = 0;
    items.forEach(function (item) {
      var qty = Number(item.current_qty != null ? item.current_qty : item.total_qty) || 0;
      if (qty > 0) inStock += 1;
      if (qty > 0 && qty <= 10) low += 1;
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
    if (s <= 30) { level = '低风险'; color = '#2BA24C'; }
    else if (s <= 60) { level = '中等风险'; color = '#F3A83B'; }
    else { level = '高风险'; color = '#E5484D'; }
    this.setData({ riskLevel: level, riskColor: color });
  },

  _rebuildTasks: function (db, items, recent) {
    if (!db) {
      if (!recent) return [];
      return [{ id: 'partial', tone: 'normal', glyph: '记', title: '部分数据加载失败', desc: '请下拉刷新获取最新经营数据。', action: '下拉刷新', route: '' }];
    }
    var tasks = [];
    var exp = Number(db.expiring_count) || 0;
    if (exp > 0) {
      tasks.push({ id: 'expiry', tone: 'danger', glyph: '临', title: exp + ' 个临期批次待处理', desc: '先查看临期商品，再决定促销、退货或报损。', action: '查看库存', route: 'inventory' });
    }
    var lowCount = 0;
    (items || []).forEach(function (item) {
      var qty = Number(item.current_qty != null ? item.current_qty : item.total_qty) || 0;
      if (qty > 0 && qty <= 10) lowCount++;
    });
    if (lowCount > 0) {
      tasks.push({ id: 'low', tone: 'warn', glyph: '补', title: lowCount + ' 个品类余量较少', desc: '数量提示不等于必须补货，建议结合销量和明日客流判断。', action: '查看建议', route: 'advisor' });
    }
    var risk = Number(db.risk_score) || 0;
    if (risk >= 60) {
      tasks.push({ id: 'risk', tone: 'warn', glyph: '险', title: '经营风险分偏高', desc: '打开经营镜像，查看风险来自库存、现金流还是客流波动。', action: '查看原因', route: 'dashboard' });
    }
    if (recent.length === 0) {
      tasks.push({ id: 'record', tone: 'normal', glyph: '记', title: '今天还没有经营流水', desc: '先记进货、销售或损耗，后续利润和库存才会准确。', action: '记一笔', route: 'voice' });
    }
    if (tasks.length === 0) {
      tasks.push({ id: 'steady', tone: 'good', glyph: '稳', title: '当前没有紧急待办', desc: '经营状态平稳，可以查看今日建议安排下一轮进货。', action: '看建议', route: 'advisor' });
    }
    return tasks.slice(0, 3);
  },

  handleTask: function (e) {
    var route = e.currentTarget.dataset.route;
    if (!route) { wx.startPullDownRefresh(); return; }
    if (route === 'voice' || route === 'inventory' || route === 'advisor') return wx.switchTab({ url: '/pages/' + route + '/' + route });
    if (route) wx.navigateTo({ url: '/pages/' + route + '/' + route });
  },

  loadWeather: function () {
    var self = this;
    app.request({ url: '/env/today?city=%E4%B8%8A%E6%B5%B7' }).then(function (d) { self.setData({ weather: d }); }).catch(function () {});
  },

  navigateToVoice: function () { wx.switchTab({ url: '/pages/voice/voice' }); },
  navigateToInventory: function () { wx.switchTab({ url: '/pages/inventory/inventory' }); },
  navigateToVision: function () { wx.navigateTo({ url: '/pages/vision/vision' }); },
  navigateToStocktake: function () { wx.navigateTo({ url: '/pages/stocktake/stocktake' }); },
  navigateToReport: function () { wx.navigateTo({ url: '/pages/report/report' }); },
  navigateToPurchase: function () { wx.navigateTo({ url: '/pages/purchase/purchase' }); },
  navigateToAdvisor: function () { wx.switchTab({ url: '/pages/advisor/advisor' }); },
  navigateToDashboard: function () { wx.navigateTo({ url: '/pages/dashboard/dashboard' }); },
  navigateToPos: function () { wx.navigateTo({ url: '/pages/pos/pos' }); },
});
