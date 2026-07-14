/**
 * 「我的」页面 v3.0 — 经营快照 + 快捷操作 + 设备同步 + 工具网格 + 设置 + 帮助
 */
var app = getApp();
var Theme = require('../../utils/theme');

Page({
  data: {
    skinClass: '',
    // ① 经营快照
    snapshotLoading: true,
    snapshotError: false,
    todayRevenue: 0, todayRevenueDisplay: '0.00',
    todayOrders: 0, todayAov: '0.00', trendLabel: '—',
    weekTotal: 0, dayChangePct: null, dayChangeDir: '',

    // ② 快捷操作
    voiceLabel: '今天还没记',
    purchasePending: 0,

    // ③ 设备与同步
    devices: [], offlineQueueCount: 0, deviceError: false,

    // ④ 经营工具 (9个, 去重3个被提权 + 语音记账)
    tools: [
      { page: 'report', name: '经营报告', glyph: '报', tone: 'blue' },
      { page: 'sandbox', name: '决策沙盘', glyph: '算', tone: 'corn' },
      { page: 'stocktake', name: '库存盘点', glyph: '盘', tone: 'corn' },
      { page: 'calendar', name: '经营日历', glyph: '历', tone: 'blue' },
      { page: 'vision', name: '拍照识货', glyph: '识', tone: 'blue' },
      { page: 'catalog', name: '商品目录', glyph: '录', tone: 'green' },
      { page: 'ops', name: '经营管理', glyph: '管', tone: 'corn' },
      { page: 'devices', name: '设备管理', glyph: '设', tone: 'blue' },
      { page: 'finance', name: '财务管理', glyph: '财', tone: 'green' },
      { page: 'supplier', name: '供应商档案', glyph: '供', tone: 'corn' },
    ],

    // ⑤ 摊位设置
    merchantName: '', riskProfile: 'neutral',
    voiceDialect: 'mandarin', businessHours: 'morning',
    notificationEnabled: true,
    dialects: ['普通话', '四川话', '粤语', '上海话'],
    dialectValues: ['mandarin', 'sichuanese', 'cantonese', 'shanghainese'],
    dialectIndex: 0,
    hoursOptions: ['早市 (6:00-12:00)', '午市 (12:00-18:00)', '晚市 (18:00-24:00)', '全天'],
    hoursValues: ['morning', 'noon', 'evening', 'all'],
    hoursIndex: 0,
    cityOptions: ['上海', '北京', '广州', '深圳', '杭州', '南京', '成都', '武汉', '重庆', '西安'],
    cityIndex: 0,
    merchantCity: '上海',

    // ⑦ 关于
    appVersion: '1.0.0',
  },

  onShow: function () {
    this.applySkin();
    this.loadSnapshot();
    this.loadDevices();
    this.loadSettings();
    this.refreshVoiceLabel();
    this.refreshPurchasePending();
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadSnapshot(function () { wx.stopPullDownRefresh(); },
                      function () { wx.stopPullDownRefresh(); });
    this.loadDevices();
  },

  applySkin: function () {
    // 用 Theme.apply 尊重手动皮肤设置(skinManual),而非强制按小时
    Theme.apply(this);
  },

  // ── ① 经营快照 ──────────────────────────────
  loadSnapshot: function (onSuccess, onError) {
    var self = this;
    this.setData({ snapshotLoading: true });

    Promise.all([
      app.request({ url: '/twin/dashboard' }).catch(function () { return null; }),
      app.request({ url: '/reports/daily' }).catch(function () { return null; }),
    ]).then(function (results) {
      var dash = results[0];
      var daily = results[1];

      if (!dash && !daily) {
        self.setData({ snapshotLoading: false, snapshotError: true });
        if (onError) onError();
        return;
      }

      var rev = dash ? (Number(dash.today_revenue) || 0) : 0;
      // sale_qty 是销售件数而非订单笔数;优先用 order_count 算客单价
      var saleQty = daily ? (Number(daily.sale_qty) || 0) : 0;
      var orderCount = daily ? (Number(daily.order_count) || 0) : 0;
      var txnCount = orderCount > 0 ? orderCount : saleQty;
      var aov = txnCount > 0 ? (rev / txnCount) : 0;
      var yesterdayRev = daily ? (Number(daily.yesterday_revenue) || 0) : 0;
      var changePct = yesterdayRev > 0 ? ((rev - yesterdayRev) / yesterdayRev * 100) : null;
      var trendLabel = '— 待观察';
      if (daily && daily.revenue_change_pct !== undefined && daily.revenue_change_pct !== null) {
        var pct = Number(daily.revenue_change_pct);
        trendLabel = pct > 0 ? '↗ 向好' : (pct < 0 ? '↘ 走弱' : '▸ 持平');
      }
      var weekRev = daily ? (Number(daily.week_total_revenue || 0)) : 0;

      self.setData({
        snapshotLoading: false, snapshotError: false,
        todayRevenue: rev, todayRevenueDisplay: rev.toFixed(2),
        todayOrders: txnCount, todayAov: aov.toFixed(2),
        trendLabel: trendLabel,
        dayChangePct: changePct,
        dayChangeDir: changePct === null ? '' : (changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'flat')),
        weekTotal: weekRev,
      });
      if (onSuccess) onSuccess();
    }).catch(function () {
      self.setData({ snapshotLoading: false, snapshotError: true });
      if (onError) onError();
    });
  },

  // ── ② 快捷操作 ──────────────────────────────
  refreshVoiceLabel: function () {
    var self = this;
    app.request({ url: '/voice/today-count' }).then(function (res) {
      var count = (res && res.today_count) || 0;
      self.setData({ voiceLabel: count > 0 ? '再记一笔' : '今天还没记' });
    }).catch(function () {});
  },

  refreshPurchasePending: function () {
    var draft = wx.getStorageSync('purchaseDraft') || [];
    this.setData({ purchasePending: draft.length });
  },

  // ── ③ 设备与同步 ────────────────────────────
  loadDevices: function () {
    var self = this;
    // 离线队列
    try {
      var queue = JSON.parse(wx.getStorageSync('qt_offline_queue') || '[]');
      var pending = 0;
      queue.forEach(function (item) { if (!item.synced) pending++; });
      this.setData({ offlineQueueCount: pending });
    } catch (e) {}

    // 设备状态
    app.request({ url: '/devices' }).catch(function () { return null; }).then(function (data) {
      if (!data || !Array.isArray(data)) { self.setData({ deviceError: true }); return; }
      var devices = data.slice(0, 2).map(function (d) {
        var lastBeat = d.last_heartbeat ? new Date(d.last_heartbeat) : null;
        var minsAgo = lastBeat ? Math.floor((Date.now() - lastBeat.getTime()) / 60000) : null;
        var status = !lastBeat ? 'offline' : (minsAgo < 5 ? 'online' : (minsAgo < 30 ? 'unstable' : 'offline'));
        return {
          name: d.name || d.type || '设备',
          type: d.type || 'device',
          status: status,
          heartbeat: minsAgo !== null ? (minsAgo < 1 ? '刚刚' : minsAgo + ' 分钟前') : '—',
        };
      });
      self.setData({ devices: devices, deviceError: false });
    });
  },

  triggerSync: function () {
    try { require('../../utils/offline-sync').getQueue().sync().then(function () { wx.showToast({ title: '同步成功', icon: 'success' }); }); } catch (e) {}
  },

  // ── ⑤ 摊位设置 ──────────────────────────────
  loadSettings: function () {
    var storedDialect = wx.getStorageSync('voiceDialect') || 'mandarin';
    var storedRisk = wx.getStorageSync('riskProfile') || 'neutral';
    var storedHours = wx.getStorageSync('businessHours') || 'morning';
    var storedNotify = wx.getStorageSync('notificationEnabled');
    if (storedNotify === '') storedNotify = true;
    else storedNotify = storedNotify !== false;
    var di = this.data.dialectValues.indexOf(storedDialect);
    var hi = this.data.hoursValues.indexOf(storedHours);
    this.setData({
      merchantName: app.globalData.merchantName || '',
      voiceDialect: storedDialect, riskProfile: storedRisk,
      businessHours: storedHours, notificationEnabled: storedNotify,
      dialectIndex: di >= 0 ? di : 0, hoursIndex: hi >= 0 ? hi : 0,
      merchantCity: app.getCity(),
      cityIndex: Math.max(0, this.data.cityOptions.indexOf(app.getCity())),
    });
    // 从后端同步偏好设置（跨设备同步）
    var self = this;
    app.request({ url: '/auth/me/preferences', auth: true }).then(function (prefs) {
      if (!prefs) return;
      var dialect = prefs.voice_dialect || storedDialect;
      var risk = prefs.risk_profile || storedRisk;
      var hours = prefs.business_hours || storedHours;
      var notify = prefs.notification_enabled !== undefined ? prefs.notification_enabled : storedNotify;
      var city = prefs.merchant_city || app.getCity();
      var di2 = self.data.dialectValues.indexOf(dialect);
      var hi2 = self.data.hoursValues.indexOf(hours);
      var ci2 = Math.max(0, self.data.cityOptions.indexOf(city));
      self.setData({
        voiceDialect: dialect, riskProfile: risk,
        businessHours: hours, notificationEnabled: notify,
        dialectIndex: di2 >= 0 ? di2 : 0, hoursIndex: hi2 >= 0 ? hi2 : 0,
        merchantCity: city, cityIndex: ci2,
      });
      // 同步到本地缓存
      wx.setStorageSync('voiceDialect', dialect);
      wx.setStorageSync('riskProfile', risk);
      wx.setStorageSync('businessHours', hours);
      wx.setStorageSync('notificationEnabled', notify);
      app.setCity(city);
    }).catch(function () { /* 后端同步失败时使用本地设置，静默处理 */ });
  },

  onNameChange: function (e) { this.setData({ merchantName: e.detail.value }); },
  onRiskChange: function (e) { this.setData({ riskProfile: e.detail.value }); },
  onDialectChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ dialectIndex: index, voiceDialect: this.data.dialectValues[index] });
  },
  onBusinessHoursChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ hoursIndex: index, businessHours: this.data.hoursValues[index] });
  },
  onCityChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ cityIndex: index, merchantCity: this.data.cityOptions[index] });
  },
  onNotificationToggle: function () {
    this.setData({ notificationEnabled: !this.data.notificationEnabled });
  },

  saveProfile: function () {
    wx.setStorageSync('merchantName', this.data.merchantName);
    wx.setStorageSync('voiceDialect', this.data.voiceDialect);
    wx.setStorageSync('riskProfile', this.data.riskProfile);
    wx.setStorageSync('businessHours', this.data.businessHours);
    wx.setStorageSync('notificationEnabled', this.data.notificationEnabled);
    app.setCity(this.data.merchantCity);
    app.globalData.merchantName = this.data.merchantName;
    // 推送偏好到后端（跨设备同步）
    app.request({
      url: '/auth/me/preferences', method: 'PUT',
      data: {
        voice_dialect: this.data.voiceDialect,
        risk_profile: this.data.riskProfile,
        business_hours: this.data.businessHours,
        notification_enabled: this.data.notificationEnabled,
        merchant_city: this.data.merchantCity,
      },
    }).then(function () {}).catch(function () {});
    wx.showToast({ title: '偏好已保存', icon: 'success' });
  },

  // ── 导航 ─────────────────────────────────────
  goQuick: function (e) {
    var page = e.currentTarget.dataset.page;
    if (!page) return;
    if (page === 'voice' || page === 'inventory' || page === 'advisor') wx.switchTab({ url: '/pages/' + page + '/' + page });
    else wx.navigateTo({ url: '/pages/' + page + '/' + page });
  },

  goDeep: function (e) {
    var page = e.currentTarget.dataset.page;
    if (!page) return;
    wx.navigateTo({ url: '/pages/' + page + '/' + page });
  },

  goDevices: function () { wx.navigateTo({ url: '/pages/devices/devices' }); },
  goDashboard: function () { wx.navigateTo({ url: '/pages/dashboard/dashboard' }); },

  // ── ⑥ 帮助与反馈 ─────────────────────────────
  showFeedback: function () {
    var self = this;
    wx.showModal({
      title: '意见反馈', editable: true, placeholderText: '描述你的建议或遇到的问题...', content: '',
      success: function (res) {
        if (res.confirm && res.content && res.content.trim()) {
          app.request({
            url: '/feedback', method: 'POST',
            data: { content: res.content.trim(), page: 'pages/profile/profile', app_version: self.data.appVersion },
          }).then(function () { wx.showToast({ title: '感谢反馈！', icon: 'success' }); })
            .catch(function () { wx.showToast({ title: '提交失败，请稍后重试', icon: 'none' }); });
        }
      },
    });
  },

  showGuide: function () { wx.navigateTo({ url: '/pages/doc/doc?type=guide' }); },
  showFAQ: function () { wx.navigateTo({ url: '/pages/doc/doc?type=faq' }); },
  contactService: function () {
    wx.showModal({ title: '联系客服', content: '请拨打客服电话或在微信群反馈', confirmText: '知道了', showCancel: false });
  },
  showPrivacy: function () { wx.navigateTo({ url: '/pages/doc/doc?type=privacy' }); },
  showTerms: function () { wx.navigateTo({ url: '/pages/doc/doc?type=terms' }); },
});
