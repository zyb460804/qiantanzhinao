/** 批次追溯页 — 消费者扫码查看商品来源、检疫、召回状态 */
var app = getApp();

Page({
  data: {
    traceCode: '',
    loading: true,
    error: null,
    trace: null,
    skinClass: '',
    statusLabels: {
      'sellable': '可售',
      'near_expiry': '临期',
      'locked': '已锁定',
      'sold_out': '售罄',
      'wasted': '已报损',
      'returned': '已退货',
      'recalled': '已召回',
      'destroyed': '已销毁',
      'removed': '已下架',
    },
    statusColors: {
      'sellable': 'green',
      'near_expiry': 'amber',
      'locked': 'red',
      'sold_out': 'muted',
      'wasted': 'muted',
      'returned': 'muted',
      'recalled': 'red',
      'destroyed': 'red',
      'removed': 'red',
    },
  },

  onLoad: function (options) {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    var code = options.code || options.scene || '';
    // Support WeChat scan code (scene parameter may be URL-encoded)
    if (!code && options.q) {
      // Parse query string from scanned QR
      var params = this._parseQuery(options.q);
      code = params.code || '';
    }
    if (code) {
      this.setData({ traceCode: code });
      this.loadTrace(code);
    } else {
      this.setData({ loading: false, error: '未提供追溯码' });
    }
  },

  _parseQuery: function (qs) {
    var result = {};
    if (!qs) return result;
    var pairs = qs.split('&');
    for (var i = 0; i < pairs.length; i++) {
      var parts = pairs[i].split('=');
      if (parts.length === 2) {
        result[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1]);
      }
    }
    return result;
  },

  loadTrace: function (code) {
    var self = this;
    this.setData({ loading: true, error: null });
    app.request({
      url: '/food-safety/trace/' + encodeURIComponent(code),
      method: 'GET',
      noAuth: true,  // Public endpoint, no JWT required
    }).then(function (data) {
      var trace = data.data || data;
      self.setData({
        loading: false,
        trace: trace,
        error: null,
      });
    }).catch(function (err) {
      self.setData({
        loading: false,
        error: (err && err.message) || '未找到该批次的追溯信息',
      });
    });
  },

  // ── Helpers ─────────────────────────────────────────

  certList: function () {
    var certs = this.data.trace && this.data.trace.certificates;
    if (!certs) return [];
    if (typeof certs === 'string') {
      try { certs = JSON.parse(certs); } catch (e) { return []; }
    }
    if (Array.isArray(certs)) return certs;
    return Object.keys(certs).map(function (k) {
      return { name: k, value: certs[k] };
    });
  },

  statusLabel: function () {
    var s = this.data.trace && this.data.trace.status;
    return this.data.statusLabels[s] || s || '未知';
  },

  statusColor: function () {
    var s = this.data.trace && this.data.trace.status;
    return this.data.statusColors[s] || 'muted';
  },

  isSafe: function () {
    var s = this.data.trace && this.data.trace.status;
    return s === 'sellable' || s === 'near_expiry' || s === 'sold_out';
  },

  // Copy trace code
  copyCode: function () {
    wx.setClipboardData({
      data: this.data.traceCode,
      success: function () {
        wx.showToast({ title: '追溯码已复制', icon: 'success' });
      },
    });
  },
});
