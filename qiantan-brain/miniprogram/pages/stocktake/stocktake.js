/** 库存盘点页面 — 核对实际库存，校准数字孪生 */
var app = getApp();

Page({
  data: {
    activeTab: 'stocktake',
    skinClass: '',
    loading: false,
    sessionId: null,
    stocktakeItems: [],
    submittedMap: {},
    completed: false,
    result: null,
    historyList: [],
    progressCount: 0,
    totalVariance: 0,
    lossAmount: 0,
    notes: '',
    reasons: [
      { key: 'natural_loss', label: '自然损耗' },
      { key: 'unrecorded_sale', label: '漏记销售' },
      { key: 'weighing_error', label: '称重误差' },
      { key: 'theft', label: '丢失' },
      { key: 'unknown', label: '未知' },
    ],
    submitting: false,
    completing: false,
    progressPercent: 0,
  },

  onLoad: function () {
    // 页面加载，不自动开始盘点
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    if (this.data.activeTab === 'history') {
      this.loadHistory();
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    if (this.data.activeTab === 'history') {
      this.loadHistory().then(function () {
        wx.stopPullDownRefresh();
      }).catch(function () {
        wx.stopPullDownRefresh();
      });
    } else {
      wx.stopPullDownRefresh();
    }
  },

  switchTab: function (event) {
    var tab = event.currentTarget.dataset.tab;
    this.setData({ activeTab: tab });
    if (tab === 'history') {
      this.loadHistory();
    }
  },

  // ── 开始盘点 ─────────────────────────────────────────

  startStocktake: function () {
    var self = this;
    if (this.data.loading) return;
    this.setData({ loading: true });
    app.request({
      url: '/inventory/stocktake/start',
      method: 'POST'
    }).then(function (data) {
      var items = (data.items || []).map(function (item) {
        return {
          product_id: item.product_id,
          product_name: item.product_name,
          unit: item.unit || '斤',
          book_qty: Number(item.book_qty) || 0,
          actual_qty: '',
          variance: null,
          reason: '',
          submitted: false,
        };
      });
      self.setData({
        loading: false,
        sessionId: data.session_id,
        stocktakeItems: items,
        completed: false,
        result: null,
        submittedMap: {},
        progressCount: 0,
        totalVariance: 0,
        lossAmount: 0,
        progressPercent: 0,
      });
    }).catch(function (err) {
      console.error('Start stocktake fail:', err);
      self.setData({ loading: false });
    });
  },

  // ── 输入实际数量 ─────────────────────────────────────────

  inputActualQty: function (event) {
    var index = event.currentTarget.dataset.index;
    var value = event.detail.value;
    var items = this.data.stocktakeItems.slice();
    var item = items[index];
    item.actual_qty = value;
    var actualNum = parseFloat(value);
    if (!isNaN(actualNum) && value !== '') {
      item.variance = Math.round((actualNum - item.book_qty) * 100) / 100;
    } else {
      item.variance = null;
    }
    item.submitted = false;
    this.setData({ stocktakeItems: items });
    this._recalcSummary();
  },

  // ── 快捷调整数量 ─────────────────────────────────────────

  quickAdjust: function (event) {
    var index = event.currentTarget.dataset.index;
    var offset = Number(event.currentTarget.dataset.offset);
    var items = this.data.stocktakeItems.slice();
    var item = items[index];
    var newVal = item.book_qty + offset;
    if (newVal < 0) newVal = 0;
    item.actual_qty = String(newVal);
    item.variance = Math.round((newVal - item.book_qty) * 100) / 100;
    item.submitted = false;
    this.setData({ stocktakeItems: items });
    this._recalcSummary();
    // 无差异时自动提交
    if (item.variance === 0) {
      this._submitItem(index);
    }
  },

  // ── 选择差异原因 ─────────────────────────────────────────

  selectReason: function (event) {
    var index = event.currentTarget.dataset.index;
    var reason = event.currentTarget.dataset.reason;
    var items = this.data.stocktakeItems.slice();
    items[index].reason = reason;
    this.setData({ stocktakeItems: items });
    this._submitItem(index);
  },

  // ── 提交单个盘点项 ─────────────────────────────────────────

  _submitItem: function (index) {
    var self = this;
    var item = this.data.stocktakeItems[index];
    if (this.data.submitting) return;
    var actualNum = parseFloat(item.actual_qty);
    if (isNaN(actualNum) || item.actual_qty === '') {
      wx.showToast({ title: '请输入有效数量', icon: 'none' });
      return;
    }
    var reason = (item.variance === 0 || item.variance === null) ? 'unknown' : (item.reason || 'unknown');
    this.setData({ submitting: true });
    app.request({
      url: '/inventory/stocktake/' + this.data.sessionId + '/submit',
      method: 'POST',
      data: {
        product_id: item.product_id,
        actual_qty: actualNum,
        variance_reason: reason,
      }
    }).then(function (data) {
      var items = self.data.stocktakeItems.slice();
      items[index].submitted = true;
      items[index].variance = data.variance;
      var submittedMap = self.data.submittedMap;
      submittedMap[item.product_id] = {
        actual_qty: data.actual_qty,
        variance: data.variance,
        reason: reason,
        item_id: data.item_id,
      };
      self.setData({ stocktakeItems: items, submittedMap: submittedMap, submitting: false });
      self._recalcSummary();
    }).catch(function (err) {
      console.error('Submit item fail:', err);
      self.setData({ submitting: false });
    });
  },

  // ── 重新计算汇总 ─────────────────────────────────────────

  _recalcSummary: function () {
    var items = this.data.stocktakeItems;
    var progressCount = 0;
    var totalVariance = 0;
    items.forEach(function (item) {
      if (item.submitted && item.variance !== null) {
        progressCount++;
        totalVariance += item.variance;
      }
    });
    totalVariance = Math.round(totalVariance * 100) / 100;
    var total = items.length;
    var percent = total > 0 ? Math.round(progressCount / total * 100) : 0;
    this.setData({
      progressCount: progressCount,
      totalVariance: totalVariance,
      progressPercent: percent,
    });
  },

  // ── 完成盘点 ─────────────────────────────────────────

  completeStocktake: function () {
    var self = this;
    if (this.data.completing) return;
    if (this.data.progressCount < this.data.stocktakeItems.length) {
      wx.showToast({ title: '还有商品未盘点', icon: 'none' });
      return;
    }
    this.setData({ completing: true });
    app.request({
      url: '/inventory/stocktake/' + this.data.sessionId + '/complete',
      method: 'POST',
      data: { notes: this.data.notes || '' }
    }).then(function (data) {
      self.setData({
        completed: true,
        result: data,
        completing: false,
        lossAmount: data.total_loss_amount || 0,
      });
      wx.showToast({ title: '盘点完成', icon: 'success' });
    }).catch(function (err) {
      console.error('Complete stocktake fail:', err);
      self.setData({ completing: false });
    });
  },

  inputNotes: function (event) {
    this.setData({ notes: event.detail.value });
  },

  // ── 加载盘点历史 ─────────────────────────────────────────

  loadHistory: function () {
    var self = this;
    return app.request({
      url: '/inventory/stocktake/history'
    }).then(function (data) {
      var list = (data || []).map(function (item) {
        var copy = {};
        Object.keys(item).forEach(function (key) { copy[key] = item[key]; });
        copy.status_text = item.status === 'completed' ? '已完成' : (item.status === 'in_progress' ? '进行中' : item.status);
        copy.variance_text = self._formatVariance(item.total_variance);
        copy.date_text = self._formatDate(item.started_at);
        copy.loss_text = item.total_loss_amount ? ('¥' + item.total_loss_amount) : '¥0';
        copy.is_loss = (Number(item.total_variance) || 0) < 0;
        copy.is_gain = (Number(item.total_variance) || 0) > 0;
        return copy;
      });
      self.setData({ historyList: list });
    }).catch(function (err) {
      console.error('Load history fail:', err);
      self.setData({ historyList: [] });
    });
  },

  _formatVariance: function (variance) {
    var v = Number(variance) || 0;
    v = Math.round(v * 100) / 100;
    if (v > 0) return '+' + v;
    return String(v);
  },

  _formatDate: function (dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    var month = d.getMonth() + 1;
    var day = d.getDate();
    var hour = d.getHours();
    var minute = d.getMinutes();
    var timeStr = (hour < 10 ? '0' : '') + hour + ':' + (minute < 10 ? '0' : '') + minute;
    return month + '月' + day + '日 ' + timeStr;
  },

  // ── 导航 ─────────────────────────────────────────

  viewInventory: function () {
    wx.switchTab({ url: '/pages/inventory/inventory' });
  },

  goHome: function () {
    wx.switchTab({ url: '/pages/index/index' });
  },

  startNewStocktake: function () {
    this.setData({
      sessionId: null,
      stocktakeItems: [],
      submittedMap: {},
      completed: false,
      result: null,
      progressCount: 0,
      totalVariance: 0,
      lossAmount: 0,
      notes: '',
      progressPercent: 0,
    });
  },
});
