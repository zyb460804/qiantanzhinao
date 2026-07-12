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
      auth: false,  // 公开端点,无需 JWT(app.js 判断 auth !== false)
    }).then(function (data) {
      // app.request 已解包 body.data,这里直接用 data;兼容旧式双重解包
      var trace = (data && data.trace) ? data.trace : data;
      // 在 JS 中算好派生字段,WXML 不支持调用页面方法
      var status = trace && trace.status;
      var statusLabels = self.data.statusLabels;
      var statusColors = self.data.statusColors;
      var statusLabel = statusLabels[status] || status || '未知';
      var statusColor = statusColors[status] || 'muted';
      var isSafe = status === 'sellable' || status === 'near_expiry' || status === 'sold_out';
      var bannerIcon = isSafe ? '✓' : '⚠';
      // 证照列表预处理为数组
      var certs = trace && trace.certificates;
      var certList = [];
      if (certs) {
        if (typeof certs === 'string') {
          try { certs = JSON.parse(certs); } catch (e) { certs = null; }
        }
        if (Array.isArray(certs)) {
          certList = certs.map(function (c) { return typeof c === 'string' ? { name: c } : c; });
        } else if (certs && typeof certs === 'object') {
          certList = Object.keys(certs).map(function (k) { return { name: k, value: certs[k] }; });
        }
      }
      self.setData({
        loading: false,
        trace: trace,
        error: null,
        statusLabel: statusLabel,
        statusColor: statusColor,
        isSafe: isSafe,
        bannerIcon: bannerIcon,
        certList: certList,
        hasCerts: certList.length > 0,
      });
    }).catch(function (err) {
      self.setData({
        loading: false,
        error: (err && err.body && (err.body.message || err.body.detail)) || (err && err.message) || '未找到该批次的追溯信息',
      });
    });
  },

  // "重新查询"按钮:从 dataset 读 code,避免 bindtap 传 event 给 loadTrace
  retryQuery: function (e) {
    var code = e.currentTarget.dataset.code || this.data.traceCode;
    if (code) this.loadTrace(code);
  },

  // 派生字段(statusLabel/statusColor/isSafe/certList)已在 loadTrace 中
  // 计算并写入 data,WXML 不支持调用页面方法,必须走 data 绑定。

  // Copy trace code
  copyCode: function () {
    wx.setClipboardData({
      data: this.data.traceCode,
      success: function () {
        wx.showToast({ title: '追溯码已复制', icon: 'success' });
      },
    });
  },

  reportIssue: function () {
    var self = this;
    wx.showModal({
      title: '反馈问题', editable: true,
      placeholderText: '描述商品或溯源信息的问题...', content: '',
      success: function (res) {
        if (res.confirm && res.content && res.content.trim()) {
          app.request({
            url: '/feedback', method: 'POST',
            data: { content: '追溯码 ' + self.data.traceCode + ': ' + res.content.trim(), page: 'pages/trace/trace' },
          }).then(function () { wx.showToast({ title: '感谢反馈！', icon: 'success' }); })
            .catch(function () { wx.showToast({ title: '提交失败', icon: 'none' }); });
        }
      },
    });
  },
});
