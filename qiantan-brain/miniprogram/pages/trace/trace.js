/** 食品安全追溯 — 消费者扫码查看 + 摊主批次管理
 *
 *  双模式：
 *    - 有 traceCode（扫码进入）→ 消费者追溯视图
 *    - 无 traceCode（导航进入）→ 摊主批次管理视图
 */
var app = getApp();

var STATUS_LABELS = {
  sellable: '可售', near_expiry: '临期', locked: '已锁定',
  sold_out: '售罄', wasted: '已报损', returned: '已退货',
  recalled: '已召回', destroyed: '已销毁', removed: '已下架'
};
var STATUS_COLORS = {
  sellable: 'green', near_expiry: 'amber', locked: 'red',
  sold_out: 'muted', wasted: 'muted', returned: 'muted',
  recalled: 'red', destroyed: 'red', removed: 'red'
};

Page({
  stopMaskTap: function () {},

  data: {
    skinClass: '',
    mode: 'consumer',  // consumer | owner
    traceCode: '', loading: true, error: null, trace: null,

    // 批次管理
    batches: [], batchesLoading: true, batchesError: false,
    expandedBatchId: '',
    checklist: null, checklistLoading: false,

    // 二维码弹窗
    showQR: false, qrCode: '', qrBatchLabel: '', qrImageUrl: '',
  },

  onLoad: function (options) {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    var code = options.code || options.scene || '';
    if (!code && options.q) {
      var params = this._parseQuery(options.q);
      code = params.code || '';
    }
    if (code) {
      this.setData({ mode: 'consumer', traceCode: code });
      this.loadConsumerTrace(code);
    } else {
      this.setData({ mode: 'owner' });
    }
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    if (this.data.mode === 'owner' && this.data.batches.length === 0) {
      this.loadBatches();
      this.loadChecklist();
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    if (this.data.mode === 'owner') {
      this.loadBatches(function () { wx.stopPullDownRefresh(); });
      this.loadChecklist();
    } else {
      this.loadConsumerTrace(this.data.traceCode, function () { wx.stopPullDownRefresh(); });
    }
  },

  _parseQuery: function (qs) {
    var result = {};
    if (!qs) return result;
    var pairs = qs.split('&');
    for (var i = 0; i < pairs.length; i++) {
      var parts = pairs[i].split('=');
      if (parts.length === 2) result[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1]);
    }
    return result;
  },

  // ── 消费者模式 ──────────────────────────────────

  loadConsumerTrace: function (code, callback) {
    var self = this;
    this.setData({ loading: true, error: null });
    app.request({
      url: '/food-safety/trace/' + encodeURIComponent(code),
      method: 'GET', auth: false
    }).then(function (data) {
      var trace = (data && data.trace) ? data.trace : data;
      var status = trace && trace.status;
      var sl = STATUS_LABELS[status] || status || '未知';
      var sc = STATUS_COLORS[status] || 'muted';
      var isSafe = status === 'sellable' || status === 'near_expiry' || status === 'sold_out';
      var certs = trace && trace.certificates;
      var certList = [];
      if (certs) {
        if (typeof certs === 'string') { try { certs = JSON.parse(certs); } catch (e) {} }
        if (Array.isArray(certs)) certList = certs.map(function (c) { return typeof c === 'string' ? { name: c } : c; });
        else if (certs && typeof certs === 'object') certList = Object.keys(certs).map(function (k) { return { name: k, value: certs[k] }; });
      }
      self.setData({
        loading: false, trace: trace, error: null,
        statusLabel: sl, statusColor: sc, isSafe: isSafe,
        bannerIcon: isSafe ? '✓' : '⚠',
        certList: certList, hasCerts: certList.length > 0
      });
      if (callback) callback();
    }).catch(function (err) {
      self.setData({
        loading: false,
        error: (err && err.body && err.body.detail) || (err && err.message) || '未找到追溯信息'
      });
      if (callback) callback();
    });
  },

  copyCode: function () {
    wx.setClipboardData({ data: this.data.traceCode, success: function () { wx.showToast({ title: '已复制', icon: 'success' }); } });
  },

  retryQuery: function (e) {
    var code = e && e.currentTarget ? e.currentTarget.dataset.code : this.data.traceCode;
    if (code) this.loadConsumerTrace(code);
  },

  reportIssue: function () {
    var self = this;
    wx.showModal({
      title: '反馈问题', editable: true, placeholderText: '描述商品或溯源信息的问题...', content: '',
      success: function (res) {
        if (res.confirm && res.content && res.content.trim()) {
          app.request({
            url: '/feedback', method: 'POST',
            data: { content: '追溯码 ' + self.data.traceCode + ': ' + res.content.trim(), page: 'pages/trace/trace' }
          }).then(function () { wx.showToast({ title: '感谢反馈！', icon: 'success' }); })
            .catch(function () { wx.showToast({ title: '提交失败', icon: 'none' }); });
        }
      }
    });
  },

  // ── 摊主模式 ──────────────────────────────────

  loadBatches: function (callback) {
    var self = this;
    this.setData({ batchesLoading: true });
    app.request({ url: '/food-safety/batches?limit=30' })
      .then(function (data) {
        var batches = Array.isArray(data) ? data : [];
        self.setData({ batches: batches, batchesLoading: false, batchesError: false });
        if (callback) callback();
      })
      .catch(function () {
        self.setData({ batchesLoading: false, batchesError: true });
        if (callback) callback();
      });
  },

  loadChecklist: function () {
    var self = this;
    this.setData({ checklistLoading: true });
    app.request({ url: '/food-safety/daily-checklist' })
      .then(function (data) { self.setData({ checklist: data, checklistLoading: false }); })
      .catch(function () { self.setData({ checklistLoading: false }); });
  },

  toggleExpand: function (e) {
    var id = e.currentTarget.dataset.id;
    this.setData({ expandedBatchId: this.data.expandedBatchId === id ? '' : id });
  },

  generateQR: function (e) {
    var self = this;
    var batchId = e.currentTarget.dataset.id;
    wx.showLoading({ title: '生成中...' });
    app.request({ url: '/food-safety/batches/' + batchId + '/generate-qr', method: 'POST', data: {} })
      .then(function (res) {
        wx.hideLoading();
        var code = res && res.trace_code;
        if (code) {
          // 后端二维码图片地址
          var qrImgUrl = app.globalData.apiBase + '/food-safety/trace/' + encodeURIComponent(code) + '/qr-image';
          self.setData({
            showQR: true,
            qrCode: code,
            qrBatchLabel: res.batch_label || '',
            qrImageUrl: qrImgUrl
          });
        }
        self.loadBatches();
      })
      .catch(function (err) {
        wx.hideLoading();
        wx.showToast({ title: (err && err.body && err.body.detail) || '生成失败', icon: 'none' });
      });
  },

  /** 关闭二维码弹窗 */
  closeQR: function () {
    this.setData({ showQR: false });
  },

  /** 保存二维码图片到相册 */
  saveQR: function () {
    var self = this;
    wx.showLoading({ title: '保存中...' });
    wx.downloadFile({
      url: self.data.qrImageUrl,
      success: function (res) {
        wx.saveImageToPhotosAlbum({
          filePath: res.tempFilePath,
          success: function () {
            wx.hideLoading();
            wx.showToast({ title: '已保存到相册', icon: 'success' });
          },
          fail: function () {
            wx.hideLoading();
            wx.showModal({
              title: '需要授权', content: '请允许保存图片到相册，用于打印二维码标签。',
              success: function (m) {
                if (m.confirm) wx.openSetting();
              }
            });
          }
        });
      },
      fail: function () {
        wx.hideLoading();
        wx.showToast({ title: '下载失败', icon: 'none' });
      }
    });
  },

  /** 复制追溯码 */
  copyQRCode: function () {
    wx.setClipboardData({ data: this.data.qrCode, success: function () { wx.showToast({ title: '追溯码已复制', icon: 'success' }); } });
  },

  /** 锁定批次 */
  lockBatch: function (e) {
    var self = this;
    var batchId = e.currentTarget.dataset.id;
    wx.showModal({
      title: '确认锁定', content: '锁定后 POS 将立即停止销售此批次。', confirmColor: '#e2503e',
      success: function (res) {
        if (!res.confirm) return;
        app.request({ url: '/food-safety/batches/' + batchId + '/lock', method: 'POST', data: { reason: '食品安全检查' } })
          .then(function () { wx.showToast({ title: '已锁定', icon: 'success' }); self.loadBatches(); })
          .catch(function (err) { wx.showToast({ title: (err && err.body && err.body.detail) || '操作失败', icon: 'none' }); });
      }
    });
  },

  /** 解锁批次 */
  unlockBatch: function (e) {
    var self = this;
    var batchId = e.currentTarget.dataset.id;
    app.request({ url: '/food-safety/batches/' + batchId + '/unlock', method: 'POST', data: {} })
      .then(function () { wx.showToast({ title: '已解锁', icon: 'success' }); self.loadBatches(); })
      .catch(function (err) { wx.showToast({ title: (err && err.body && err.body.detail) || '操作失败', icon: 'none' }); });
  },

  getStatusLabel: function (s) { return STATUS_LABELS[s] || s; },
  getStatusColor: function (s) { return STATUS_COLORS[s] || 'muted'; }
});
