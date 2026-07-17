/** 租户中心 — 订阅套餐 · 用量配额 · 账单记录
 *
 *  调用 /api/tenant/* 系列接口，复用商户 JWT 鉴权。
 *  仅 owner / tenant_admin 角色可访问（后端校验）。
 */
var app = getApp();

/** 套餐功能中文映射 */
var FEATURE_LABELS = {
  ai_advisor: 'AI 参谋', voice_accounting: '语音记账', vision: '视觉识别',
  pos: '收银开单', purchase: '采购管理', stocktake: '库存盘点',
  reports: '经营报表', devices: '设备管理', export_data: '数据导出',
  offline_sync: '离线同步', experience_cloud: '经验云'
};

Page({
  data: {
    skinClass: '', loading: true, loadError: false,
    activeTab: 'subscription',  // subscription | usage | invoices

    // 订阅
    subscription: null, subscriptionError: false,

    // 用量
    quotas: [], usageTrend: [], usageTrendMetric: 'api_calls',
    quotasError: false,

    // 发票
    invoices: [], invoicesError: false,
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadCurrentTab();
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadCurrentTab(function () { wx.stopPullDownRefresh(); });
  },

  /** 切换 Tab */
  switchTab: function (e) {
    var tab = e.currentTarget.dataset.tab;
    if (tab === this.data.activeTab) return;
    this.setData({ activeTab: tab });
    this.loadCurrentTab();
  },

  /** 根据当前 tab 加载对应数据 */
  loadCurrentTab: function (callback) {
    var tab = this.data.activeTab;
    if (tab === 'subscription') this.loadSubscription(callback);
    else if (tab === 'usage') this.loadQuotas(callback);
    else if (tab === 'invoices') this.loadInvoices(callback);
    else if (callback) callback();
  },

  /** 加载订阅信息 */
  loadSubscription: function (callback) {
    var self = this;
    this.setData({ loading: true, subscriptionError: false });
    app.request({ url: '/tenant/subscription' })
      .then(function (data) {
        self.setData({ loading: false, subscription: data });
        if (callback) callback();
      })
      .catch(function () {
        self.setData({ loading: false, subscriptionError: true });
        if (callback) callback();
      });
  },

  /** 加载用量配额 */
  loadQuotas: function (callback) {
    var self = this;
    this.setData({ loading: true, quotasError: false });
    app.request({ url: '/tenant/usage/quotas' })
      .then(function (data) {
        var quotas = (data && data.quotas) || [];
        self.setData({ loading: false, quotas: quotas });
        // 加载第一个指标的 30 天趋势
        if (quotas.length > 0) {
          self.loadUsageTrend(quotas[0].metric);
        }
        if (callback) callback();
      })
      .catch(function () {
        self.setData({ loading: false, quotasError: true });
        if (callback) callback();
      });
  },

  /** 加载用量趋势 */
  loadUsageTrend: function (metric) {
    var self = this;
    this.setData({ usageTrendMetric: metric, usageTrend: [] });
    app.request({ url: '/tenant/usage/trend/' + metric })
      .then(function (data) {
        var trend = (data && data.trend) || [];
        self.setData({ usageTrend: trend });
      })
      .catch(function () { /* 趋势非关键，静默失败 */ });
  },

  /** 切换趋势指标 */
  onTrendMetricChange: function (e) {
    var metric = e.currentTarget.dataset.metric;
    if (metric === this.data.usageTrendMetric) return;
    this.loadUsageTrend(metric);
  },

  /** 加载发票列表 */
  loadInvoices: function (callback) {
    var self = this;
    this.setData({ loading: true, invoicesError: false });
    app.request({ url: '/tenant/invoices' })
      .then(function (data) {
        var invoices = Array.isArray(data) ? data : [];
        self.setData({ loading: false, invoices: invoices });
        if (callback) callback();
      })
      .catch(function () {
        self.setData({ loading: false, invoicesError: true });
        if (callback) callback();
      });
  },

  /** 格式话金额 */
  money: function (v) {
    return Number(v || 0).toFixed(2);
  },

  /** 用量百分比 */
  quotaPercent: function (current, limit) {
    if (!limit || limit <= 0) return 0;
    return Math.min(100, Math.round(current / limit * 100));
  },

  /** 用量状态 */
  quotaStatus: function (exceeded, current, limit) {
    if (exceeded) return 'danger';
    var pct = limit > 0 ? current / limit : 0;
    if (pct >= 0.9) return 'warn';
    if (pct >= 0.7) return 'caution';
    return 'ok';
  },

  /** 发票状态中文 */
  invoiceStatusLabel: function (status) {
    var map = { draft: '草稿', sent: '已发出', paid: '已付', overdue: '逾期', void: '作废' };
    return map[status] || status;
  },

  /** 发票状态颜色 */
  invoiceStatusColor: function (status) {
    var map = { draft: 'muted', sent: 'var(--corn)', paid: 'var(--green-600)', overdue: 'var(--tomato)', void: 'var(--muted)' };
    return map[status] || 'var(--muted)';
  },

  /** 计费周期中文 */
  billingCycleLabel: function (cycle) {
    return cycle === 'yearly' ? '年付' : '月付';
  },

  /** 套餐功能中文 */
  featureLabel: function (key) {
    return FEATURE_LABELS[key] || key;
  }
});
