/** 库存盘点页面 — 核对实际库存，校准数字孪生 */
var app = getApp();

Page({
  data: {
    activeTab: 'stocktake',
    skinClass: '',
    loading: false,
    restoring: false,
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
    } else if (!this.data.sessionId && !this.data.completed) {
      this.loadCurrentStocktake();
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
      this.loadCurrentStocktake().then(function () {
        wx.stopPullDownRefresh();
      }).catch(function () {
        wx.stopPullDownRefresh();
      });
    }
  },

  switchTab: function (event) {
    var self = this;
    var tab = event.currentTarget.dataset.tab;
    // 盘点进行中切 Tab → 确认放弃
    if (this.data.sessionId && !this.data.completed && tab !== 'stocktake') {
      wx.showModal({
        title: '放弃盘点？', content: '当前盘点尚未完成，切换后将丢失已核对的数据。',
        confirmText: '放弃盘点', cancelText: '继续盘点', confirmColor: '#d9524a',
        success: function (res) {
          if (res.confirm) {
            self._cancelCurrentStocktake(tab);
          }
        },
      });
      return;
    }
    this.setData({ activeTab: tab });
    if (tab === 'history') {
      this.loadHistory();
    }
  },

  _cancelCurrentStocktake: function (nextTab) {
    var self = this;
    var sessionId = this.data.sessionId;
    if (!sessionId || this.data.submitting) return;
    this.setData({ submitting: true });
    wx.showLoading({ title: '正在取消' });
    app.request({
      url: '/inventory/stocktake/' + sessionId + '/cancel',
      method: 'POST'
    }).then(function () {
      self.setData({
        activeTab: nextTab || 'stocktake',
        submitting: false,
        sessionId: null,
        stocktakeItems: [],
        submittedMap: {},
        completed: false,
        result: null,
        progressCount: 0,
        totalVariance: 0,
        lossAmount: 0,
        notes: '',
        progressPercent: 0
      });
      wx.showToast({ title: '盘点已取消', icon: 'success' });
      if (nextTab === 'history') self.loadHistory();
    }).catch(function (err) {
      self.setData({ submitting: false });
      wx.showToast({ title: self._errorText(err, '取消盘点失败'), icon: 'none' });
    }).then(function () {
      wx.hideLoading();
    });
  },

  // ── 开始盘点 ─────────────────────────────────────────

  _applySessionData: function (data) {
    var self = this;
    var submittedMap = {};
    var items = ((data && data.items) || []).map(function (item) {
      var submitted = item.submitted === true || item.actual_qty !== null && item.actual_qty !== undefined;
      var variance = item.variance === null || item.variance === undefined ? null : Number(item.variance);
      var reason = item.variance_reason || '';
      if (submitted) {
        submittedMap[item.product_id] = {
          actual_qty: Number(item.actual_qty),
          variance: variance,
          reason: reason,
          item_id: item.item_id,
        };
      }
      return {
        item_id: item.item_id,
        product_id: item.product_id,
        product_name: item.product_name,
        unit: item.unit || '斤',
        book_qty: Number(item.book_qty) || 0,
        avg_cost: Number(item.avg_cost || item.unit_cost) || 0,
        actual_qty: submitted ? String(item.actual_qty) : '',
        variance: variance,
        reason: reason,
        submitted: submitted,
      };
    });
    this.setData({
      loading: false,
      restoring: false,
      sessionId: data ? data.session_id : null,
      stocktakeItems: items,
      completed: false,
      result: null,
      submittedMap: submittedMap,
      notes: data && data.notes ? data.notes : '',
      progressCount: 0,
      totalVariance: 0,
      lossAmount: 0,
      progressPercent: 0,
    }, function () {
      self._recalcSummary();
    });
  },

  loadCurrentStocktake: function () {
    var self = this;
    if (this.data.restoring) return Promise.resolve();
    this.setData({ restoring: true });
    return app.request({
      url: '/inventory/stocktake/current'
    }).then(function (data) {
      if (data && data.session_id) {
        self._applySessionData(data);
      } else {
        self.setData({ restoring: false });
      }
    }).catch(function (err) {
      console.error('Restore stocktake fail:', err);
      self.setData({ restoring: false });
      throw err;
    });
  },

  startStocktake: function () {
    var self = this;
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    app.request({
      url: '/inventory/stocktake/start',
      method: 'POST'
    }).then(function (data) {
      self.setData({ submitting: false });
      self._applySessionData(data);
    }).catch(function (err) {
      console.error('Start stocktake fail:', err);
      self.setData({ submitting: false });
      wx.showToast({ title: self._errorText(err, '开始盘点失败'), icon: 'none' });
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
    var submittedMap = Object.assign({}, this.data.submittedMap);
    delete submittedMap[item.product_id];
    this.setData({ stocktakeItems: items, submittedMap: submittedMap });
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
    var submittedMap = Object.assign({}, this.data.submittedMap);
    delete submittedMap[item.product_id];
    this.setData({ stocktakeItems: items, submittedMap: submittedMap });
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
    if (isNaN(actualNum) || item.actual_qty === '' || actualNum < 0) {
      wx.showToast({ title: '实盘数量必须是非负数', icon: 'none' });
      return;
    }
    var reason = (item.variance === 0 || item.variance === null) ? 'unknown' : (item.reason || 'unknown');
    this.setData({ submitting: true });
    // 乐观更新：立即标记 UI 为提交中
    var items = self.data.stocktakeItems.slice();
    items[index]._submitting = true;
    self.setData({ stocktakeItems: items });

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
      items[index]._submitting = false;
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
      var failedItems = self.data.stocktakeItems.slice();
      if (failedItems[index]) failedItems[index]._submitting = false;
      self.setData({ stocktakeItems: failedItems, submitting: false });
      wx.showToast({ title: self._errorText(err, '保存盘点数量失败'), icon: 'none' });
    });
  },

  // ── 重新计算汇总 ─────────────────────────────────────────

  _recalcSummary: function () {
    var items = this.data.stocktakeItems;
    var progressCount = 0;
    var totalVariance = 0;
    var lossAmount = 0;
    items.forEach(function (item) {
      if (item.submitted && item.variance !== null) {
        progressCount++;
        totalVariance += item.variance;
        // 预估损耗金额:盘亏部分 × 单位成本
        if (item.variance < 0 && item.avg_cost) {
          lossAmount += Math.abs(item.variance) * Number(item.avg_cost);
        }
      }
    });
    totalVariance = Math.round(totalVariance * 100) / 100;
    lossAmount = Math.round(lossAmount * 100) / 100;
    var total = items.length;
    var percent = total > 0 ? Math.round(progressCount / total * 100) : 0;
    this.setData({
      progressCount: progressCount,
      totalVariance: totalVariance,
      lossAmount: lossAmount,
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
      wx.showToast({ title: self._errorText(err, '完成盘点失败'), icon: 'none' });
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
      wx.showToast({ title: self._errorText(err, '盘点记录加载失败'), icon: 'none' });
      return Promise.reject(err);
    });
  },

  _errorText: function (err, fallback) {
    return (err && err.body && err.body.detail) || (err && err.message) || fallback;
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
