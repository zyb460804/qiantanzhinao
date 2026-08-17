/**
 * 智能分析页 — 聚合异常检测 / 动态定价 / 报童进货建议
 * 三个接口并行请求；单接口失败不影响其他区块。
 */
var app = getApp();

var ANOMALY_TYPE_LABELS = {
  zero_sales: '连续零销',
  data_error: '数据异常',
  spike: '销量突增',
  drop: '销量骤降',
  outlier: '离群值',
};

var STRATEGY_LABELS = {
  age_based: '货龄',
  inventory_based: '库存',
  combined: '综合',
  clearance: '出清',
};

var URGENCY_LABELS = {
  urgent: '紧急',
  high: '高',
  medium: '中',
  low: '低',
  normal: '一般',
};

function typeLabel(key) {
  return ANOMALY_TYPE_LABELS[key] || key || '未知';
}

function strategyLabel(key) {
  return STRATEGY_LABELS[key] || key || '综合';
}

function urgencyLabel(key) {
  return URGENCY_LABELS[key] || key || '一般';
}

function toNumber(value) {
  var n = Number(value);
  return isFinite(n) ? n : 0;
}

function textNumber(value) {
  var n = Number(value);
  if (!isFinite(n)) return '--';
  return String(Math.round(n * 100) / 100);
}

function percentNumber(value) {
  var n = Number(value);
  if (!isFinite(n)) return 0;
  // 兼容 0.15 与 15 两种百分比表达
  if (n > 0 && n <= 1) return Math.round(n * 100);
  return Math.round(n);
}

function objectKeys(obj) {
  if (!obj) return [];
  if (Array.isArray(obj)) return obj.map(String);
  return Object.keys(obj);
}

function normalizeAnomaly(data) {
  var list = Array.isArray(data && data.anomalies) ? data.anomalies : [];
  var count = data && data.anomaly_count != null ? toNumber(data.anomaly_count) : list.length;

  return {
    count: count,
    list: list.map(function (item, index) {
      var typeKeys = objectKeys(item && item.by_type);
      return {
        key: 'anomaly-' + index,
        product_name: (item && item.product_name) || '未知商品',
        current_value: item && item.current_value != null ? item.current_value : '--',
        typeLabels: typeKeys.map(typeLabel),
        summary: (item && item.summary) || '',
      };
    }),
  };
}

function normalizePricing(data) {
  var list = Array.isArray(data && data.suggestions) ? data.suggestions : [];
  var count = data && data.count != null ? toNumber(data.count) : list.length;

  return {
    count: count,
    list: list.map(function (item, index) {
      return {
        key: 'pricing-' + index,
        product_name: (item && item.product_name) || '未知商品',
        current_qty: item && item.current_qty != null ? item.current_qty : '--',
        daily_forecast: item && item.daily_forecast != null ? item.daily_forecast : '--',
        original_price_text: textNumber(item && item.original_price),
        recommended_price_text: textNumber(item && item.recommended_price),
        discount_pct: percentNumber(item && item.discount_pct),
        strategy_label: strategyLabel(item && item.strategy),
        urgency_label: urgencyLabel(item && item.urgency),
        reason: (item && item.reason) || '',
        expected_revenue: item && item.expected_revenue != null ? item.expected_revenue : '--',
        expected_waste_pct: percentNumber(item && item.expected_waste_pct),
      };
    }),
  };
}

function normalizeNewsvendor(data) {
  var list = Array.isArray(data && data.suggestions) ? data.suggestions : [];
  var count = data && data.count != null ? toNumber(data.count) : list.length;

  return {
    count: count,
    list: list.map(function (item, index) {
      return {
        key: 'newsvendor-' + index,
        product_name: (item && item.product_name) || '未知商品',
        selling_price_text: textNumber(item && item.selling_price),
        unit_cost_text: textNumber(item && item.unit_cost),
        mean_demand: item && item.mean_demand != null ? item.mean_demand : '--',
        optimal_quantity: item && item.optimal_quantity != null ? item.optimal_quantity : '--',
        suggestion: (item && item.suggestion) || '',
        waste_rate_pct: percentNumber(item && item.waste_rate_pct),
      };
    }),
  };
}

Page({
  data: {
    skinClass: '',
    loading: true,
    allFailed: false,
    anomaly: null,
    anomalyError: false,
    pricing: null,
    pricingError: false,
    newsvendor: null,
    newsvendorError: false,
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this._loadAll();
  },

  onPullDownRefresh: function () {
    this._loadAll(function () {
      wx.stopPullDownRefresh();
    });
  },

  onRetry: function () {
    this._loadAll();
  },

  goInventory: function () {
    wx.switchTab({ url: '/pages/inventory/inventory' });
  },

  goPurchase: function () {
    wx.navigateTo({ url: '/pages/purchase/purchase' });
  },

  _loadAll: function (onDone) {
    var self = this;
    this.setData({ loading: true, allFailed: false });

    // 三个接口并行请求；各自 catch 返回 null，保证单个失败不阻塞其他区块。
    Promise.all([
      app.request({ url: '/anomalies/scan', data: { days: 30 } })
        .then(normalizeAnomaly)
        .catch(function (err) {
          app.logError('insight/anomalies', err, { silent: true });
          return null;
        }),
      app.request({ url: '/insights/pricing-suggestions', data: { days: 7, price_tier: 'balanced', limit: 20 } })
        .then(normalizePricing)
        .catch(function (err) {
          app.logError('insight/pricing', err, { silent: true });
          return null;
        }),
      app.request({ url: '/insights/newsvendor-suggestions', data: { days: 14, limit: 20 } })
        .then(normalizeNewsvendor)
        .catch(function (err) {
          app.logError('insight/newsvendor', err, { silent: true });
          return null;
        }),
    ]).then(function (results) {
      var anomaly = results[0];
      var pricing = results[1];
      var newsvendor = results[2];

      self.setData({
        loading: false,
        allFailed: !anomaly && !pricing && !newsvendor,
        anomaly: anomaly,
        anomalyError: !anomaly,
        pricing: pricing,
        pricingError: !pricing,
        newsvendor: newsvendor,
        newsvendorError: !newsvendor,
      });

      if (onDone) onDone();
    });
  },
});
