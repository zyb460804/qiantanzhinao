/** 经营台：首页只呈现真实经营数据、待办与高频入口。 */
var app = getApp();

Page({
  data: {
    merchantName: '', skin: 'noon', greeting: '你好', showSkeleton: true,
    todayRevenue: 0, todayCost: 0, todayProfit: 0, riskScore: 0,
    expiringCount: 0, inventoryCategoryCount: 0, inStockCount: 0, lowStockCount: 0,
    weather: null, recentRecords: [], todayTasks: [],
  },

  onShow: function () {
    this.setData({ merchantName: app.globalData.merchantName || '老板' });
    this.applySkin(app.resolveSkin());
    this.loadHomeData();
    this.loadWeather();
  },

  onReady: function () {
    var self = this;
    setTimeout(function () { self.setData({ showSkeleton: false }); }, 420);
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    var h = new Date().getHours();
    var greet = h < 5 || h >= 22 ? '夜深了' : (h < 11 ? '早上好' : (h < 18 ? '下午好' : '晚上好'));
    this.setData({ skin: skin, greeting: greet });
  },

  onSkinChange: function (e) {
    var skin = e.currentTarget.dataset.skin;
    app.globalData.skinManual = skin;
    this.applySkin(skin);
  },

  loadHomeData: function () {
    var self = this;
    var mid = app.getMerchantId();

    app.request({ url: '/twin/dashboard', data: { merchant_id: mid } }).then(function (d) {
      self.setData({
        todayRevenue: Number(d.today_revenue) || 0,
        todayCost: Number(d.today_cost) || 0,
        todayProfit: Number(d.today_profit) || 0,
        riskScore: Number(d.risk_score) || 0,
        expiringCount: Number(d.expiring_count) || 0,
      }, function () { self.rebuildTasks(); });
    }).catch(function () { self.rebuildTasks(); });

    app.request({ url: '/inventory/current', data: { merchant_id: mid } }).then(function (items) {
      items = Array.isArray(items) ? items : [];
      var inStock = 0;
      var low = 0;
      items.forEach(function (item) {
        var qty = Number(item.current_qty != null ? item.current_qty : item.total_qty) || 0;
        if (qty > 0) inStock += 1;
        if (qty > 0 && qty <= 10) low += 1;
      });
      self.setData({ inventoryCategoryCount: items.length, inStockCount: inStock, lowStockCount: low }, function () { self.rebuildTasks(); });
    }).catch(function () { self.rebuildTasks(); });

    app.request({ url: '/voice/logs', data: { merchant_id: mid, page: 1, limit: 3 } }).then(function (logs) {
      var list = Array.isArray(logs) ? logs : ((logs && logs.items) || []);
      list = list.slice(0, 3).map(function (item) {
        var parsed = item.parsed_event || {};
        var fallback = [parsed.event_type, parsed.product, parsed.quantity && (parsed.quantity + (parsed.unit || ''))].filter(Boolean).join(' · ');
        var copy = {};
        Object.keys(item).forEach(function (key) { copy[key] = item[key]; });
        copy.display_text = item.asr_text || fallback || '一笔经营记录';
        return copy;
      });
      self.setData({ recentRecords: list }, function () { self.rebuildTasks(); });
    }).catch(function () { self.setData({ recentRecords: [] }, function () { self.rebuildTasks(); }); });
  },

  rebuildTasks: function () {
    var tasks = [];
    if (this.data.expiringCount > 0) {
      tasks.push({ id: 'expiry', tone: 'danger', glyph: '临', title: this.data.expiringCount + ' 个临期批次待处理', desc: '先查看临期商品，再决定促销、退货或报损。', action: '查看库存', route: 'inventory' });
    }
    if (this.data.lowStockCount > 0) {
      tasks.push({ id: 'low', tone: 'warn', glyph: '补', title: this.data.lowStockCount + ' 个品类余量较少', desc: '数量提示不等于必须补货，建议结合销量和明日客流判断。', action: '查看建议', route: 'advisor' });
    }
    if (this.data.riskScore >= 60) {
      tasks.push({ id: 'risk', tone: 'warn', glyph: '险', title: '经营风险分偏高', desc: '打开经营镜像，查看风险来自库存、现金流还是客流波动。', action: '查看原因', route: 'dashboard' });
    }
    if (this.data.recentRecords.length === 0) {
      tasks.push({ id: 'record', tone: 'normal', glyph: '记', title: '今天还没有经营流水', desc: '先记进货、销售或损耗，后续利润和库存才会准确。', action: '记一笔', route: 'voice' });
    }
    if (tasks.length === 0) {
      tasks.push({ id: 'steady', tone: 'good', glyph: '稳', title: '当前没有紧急待办', desc: '经营状态平稳，可以查看今日建议安排下一轮进货。', action: '看建议', route: 'advisor' });
    }
    this.setData({ todayTasks: tasks.slice(0, 3) });
  },

  handleTask: function (e) {
    var route = e.currentTarget.dataset.route;
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
