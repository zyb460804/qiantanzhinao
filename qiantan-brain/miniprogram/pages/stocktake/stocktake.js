/** 库存盘点页面 — 核对实际库存，校准库存账本 */
var app = getApp();

/** 离线盘点提交队列的本地存储键。 */
var OFFLINE_QUEUE_KEY = 'qt_stocktake_queue';
/** 历史列表每页条数（与后端默认 limit 对齐）。 */
var HISTORY_PAGE_SIZE = 10;

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
    // 历史分页与详情相关
    historyPage: 1,
    historyHasMore: true,
    historyLoadingMore: false,
    expandedHistoryId: null,
    historyDetailCache: {},
    historyDetailLoading: false,
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
    var self = this;
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    if (this.data.activeTab === 'history') {
      this.loadHistory(false);
    } else if (!this.data.sessionId && !this.data.completed) {
      this.loadCurrentStocktake().then(function () {
        // 恢复会话后，尝试把离线缓存的未同步项推到服务端。
        self._flushPendingSubmits();
      }).catch(function () {});
    } else if (this.data.sessionId) {
      // 已有会话，直接重试离线队列。
      this._flushPendingSubmits();
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    if (this.data.activeTab === 'history') {
      this.loadHistory(false).then(function () {
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

  // 触底加载更多历史
  onReachBottom: function () {
    if (this.data.activeTab !== 'history') return;
    this.loadHistory(true);
  },

  // WXML "加载更多" 链接的 tap handler（bindtap 只能传 event，需包装）
  loadMoreHistory: function () {
    this.loadHistory(true);
  },

  switchTab: function (event) {
    var self = this;
    var tab = event.currentTarget.dataset.tab;
    // P1 修复：盘点进行中切 Tab 时不再强制取消。后端 in_progress 会话长期保留，
    // 已提交项已持久化；下次切回时 onShow 自动恢复。仅提示用户先保存未提交数据。
    if (this.data.sessionId && !this.data.completed && tab !== 'stocktake') {
      wx.showModal({
        title: '暂停盘点？',
        content: '当前盘点进度会保留，可随时切回继续。未提交的数据建议先保存。',
        confirmText: '暂停并切换',
        cancelText: '留在这里',
        confirmColor: '#c8392b',
        success: function (res) {
          if (res.confirm) {
            self.setData({ activeTab: tab });
            if (tab === 'history') self.loadHistory(false);
          }
        },
      });
      return;
    }
    this.setData({ activeTab: tab });
    if (tab === 'history') {
      this.loadHistory(false);
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
      // 清理本会话的离线缓存
      self._clearPendingSubmits(sessionId);
      wx.showToast({ title: '盘点已取消', icon: 'success' });
      if (nextTab === 'history') self.loadHistory(false);
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
        // P1 修复：后端已下发 avg_cost，离线时回退 unit_cost 字段。
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
    var self = this;
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
    // P0 修复：实盘=账面（variance=0）时差异原因按钮被 wx:if 隐藏，
    // 原代码没有任何触发 _submitItem 的路径，导致整次盘点无法完成。
    // 这里在 variance=0 时自动提交，与 quickAdjust 行为一致，打破死锁。
    if (item.variance === 0 && !isNaN(actualNum) && value !== '') {
      this._submitItem(index);
    }
  },

  // ── 快捷调整数量 ─────────────────────────────────────────

  quickAdjust: function (event) {
    var index = event.currentTarget.dataset.index;
    var offset = Number(event.currentTarget.dataset.offset);
    var items = this.data.stocktakeItems.slice();
    var item = items[index];
    // P1 修复：以当前实盘数量为基准累加；未输入则回退账面值。
    // 原代码 item.book_qty + offset 会覆盖用户键盘输入且多击不累加。
    var currentActual = parseFloat(item.actual_qty);
    var base = !isNaN(currentActual) && item.actual_qty !== '' ? currentActual : item.book_qty;
    var newVal = base + offset;
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
      // 提交成功后清除该商品对应的离线缓存（如有）。
      self._removePendingSubmit(self.data.sessionId, item.product_id);
      self._recalcSummary();
    }).catch(function (err) {
      console.error('Submit item fail:', err);
      var failedItems = self.data.stocktakeItems.slice();
      if (failedItems[index]) failedItems[index]._submitting = false;
      self.setData({ stocktakeItems: failedItems, submitting: false });
      // P1 修复：网络失败时缓存到本地，下次有网自动重试，避免弱网下数据丢失。
      self._savePendingSubmit(self.data.sessionId, {
        product_id: item.product_id,
        actual_qty: actualNum,
        variance_reason: reason,
      });
      wx.showToast({ title: '已离线保存，联网后自动同步', icon: 'none' });
    });
  },

  // ── 批量保存全部未提交项（P2 修复：弱网下减少请求数） ──────────────

  submitAllItems: function () {
    var self = this;
    if (this.data.submitting || this.data.completing) return;
    var items = this.data.stocktakeItems;
    var pending = items.filter(function (item) {
      return !item.submitted && item.actual_qty !== '' && !isNaN(parseFloat(item.actual_qty));
    });
    if (pending.length === 0) {
      wx.showToast({ title: '没有可保存的项', icon: 'none' });
      return;
    }
    // 本地先校验，避免无效请求
    for (var i = 0; i < pending.length; i++) {
      if (parseFloat(pending[i].actual_qty) < 0) {
        wx.showToast({ title: '实盘数量必须是非负数', icon: 'none' });
        return;
      }
    }
    this.setData({ submitting: true });
    var payload = {
      items: pending.map(function (item) {
        return {
          product_id: item.product_id,
          actual_qty: parseFloat(item.actual_qty),
          variance_reason: (item.variance === 0 || item.variance === null)
            ? 'unknown'
            : (item.reason || 'unknown'),
        };
      }),
    };
    app.request({
      url: '/inventory/stocktake/' + this.data.sessionId + '/submit-batch',
      method: 'POST',
      data: payload,
    }).then(function (data) {
      var results = (data && data.results) || [];
      var okByPid = {};
      var failCount = 0;
      results.forEach(function (r) {
        if (r.status === 'ok') okByPid[r.product_id] = r;
        else failCount += 1;
      });
      var newItems = self.data.stocktakeItems.map(function (item) {
        var r = okByPid[item.product_id];
        if (r) {
          return Object.assign({}, item, {
            submitted: true,
            _submitting: false,
            variance: r.variance,
          });
        }
        return item;
      });
      var submittedMap = Object.assign({}, self.data.submittedMap);
      Object.keys(okByPid).forEach(function (pid) {
        var r = okByPid[pid];
        submittedMap[pid] = {
          actual_qty: r.actual_qty,
          variance: r.variance,
          reason: '',
          item_id: r.item_id,
        };
      });
      self.setData({
        stocktakeItems: newItems,
        submittedMap: submittedMap,
        submitting: false,
      });
      // 成功项从离线队列清除
      Object.keys(okByPid).forEach(function (pid) {
        self._removePendingSubmit(self.data.sessionId, Number(pid));
      });
      self._recalcSummary();
      var okCount = Object.keys(okByPid).length;
      if (failCount > 0) {
        wx.showToast({ title: '成功 ' + okCount + ' 项，失败 ' + failCount + ' 项', icon: 'none' });
      } else {
        wx.showToast({ title: '已保存 ' + okCount + ' 项', icon: 'success' });
      }
    }).catch(function (err) {
      console.error('Batch submit fail:', err);
      self.setData({ submitting: false });
      wx.showToast({ title: self._errorText(err, '批量保存失败'), icon: 'none' });
    });
  },

  // ── 离线盘点缓存：本地持久化失败的提交，下次有网重试 ─────────

  _savePendingSubmit: function (sessionId, payload) {
    if (!sessionId) return;
    try {
      var queue = wx.getStorageSync(OFFLINE_QUEUE_KEY) || [];
      // 按 session_id + product_id 去重，保留最新
      queue = queue.filter(function (it) {
        return !(it.session_id === sessionId && it.product_id === payload.product_id);
      });
      queue.push(Object.assign({ session_id: sessionId }, payload, {
        queued_at: Date.now(),
      }));
      wx.setStorageSync(OFFLINE_QUEUE_KEY, queue);
    } catch (e) {
      console.warn('Save pending submit fail:', e);
    }
  },

  _removePendingSubmit: function (sessionId, productId) {
    try {
      var queue = wx.getStorageSync(OFFLINE_QUEUE_KEY) || [];
      var next = queue.filter(function (it) {
        return !(it.session_id === sessionId && it.product_id === productId);
      });
      if (next.length !== queue.length) wx.setStorageSync(OFFLINE_QUEUE_KEY, next);
    } catch (e) {
      console.warn('Remove pending submit fail:', e);
    }
  },

  _clearPendingSubmits: function (sessionId) {
    try {
      var queue = wx.getStorageSync(OFFLINE_QUEUE_KEY) || [];
      var next = queue.filter(function (it) { return it.session_id !== sessionId; });
      wx.setStorageSync(OFFLINE_QUEUE_KEY, next);
    } catch (e) {
      console.warn('Clear pending submits fail:', e);
    }
  },

  // 串行重试当前会话的所有离线缓存提交
  _flushPendingSubmits: function () {
    var self = this;
    var sessionId = this.data.sessionId;
    if (!sessionId || this.data.submitting) return Promise.resolve();
    var queue;
    try {
      queue = wx.getStorageSync(OFFLINE_QUEUE_KEY) || [];
    } catch (e) {
      return Promise.resolve();
    }
    var pending = queue.filter(function (it) { return it.session_id === sessionId; });
    if (pending.length === 0) return Promise.resolve();
    // 串行重试，避免并发覆盖
    return pending.reduce(function (chain, item) {
      return chain.then(function () {
        if (!self.data.sessionId || self.data.sessionId !== item.session_id) return;
        return app.request({
          url: '/inventory/stocktake/' + item.session_id + '/submit',
          method: 'POST',
          data: {
            product_id: item.product_id,
            actual_qty: item.actual_qty,
            variance_reason: item.variance_reason,
          }
        }).then(function (data) {
          self._removePendingSubmit(item.session_id, item.product_id);
          // 同步本地状态：标记该项为已提交
          var items = self.data.stocktakeItems.slice();
          for (var i = 0; i < items.length; i++) {
            if (items[i].product_id === item.product_id) {
              items[i].submitted = true;
              items[i]._submitting = false;
              items[i].variance = data.variance;
              items[i].actual_qty = String(data.actual_qty);
              break;
            }
          }
          var submittedMap = Object.assign({}, self.data.submittedMap);
          submittedMap[item.product_id] = {
            actual_qty: data.actual_qty,
            variance: data.variance,
            reason: item.variance_reason,
            item_id: data.item_id,
          };
          self.setData({ stocktakeItems: items, submittedMap: submittedMap });
          self._recalcSummary();
        }).catch(function () {
          // 仍失败，保留在队列里下次再试
        });
      });
    }, Promise.resolve());
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
      // 完成后清空本会话的离线缓存
      self._clearPendingSubmits(self.data.sessionId);
      wx.showToast({ title: '盘点完成', icon: 'success' });
    }).catch(function (err) {
      console.error('Complete stocktake fail:', err);
      self.setData({ completing: false });
      wx.showToast({ title: self._errorText(err, '完成盘点失败'), icon: 'none' });
    });
  },

  // P3 修复：本方法此前无对应 WXML 绑定（死代码），已在盘点进行区添加备注 textarea。
  inputNotes: function (event) {
    this.setData({ notes: event.detail.value });
  },

  // ── 加载盘点历史 ─────────────────────────────────────────

  // P2 修复：支持分页加载。append=true 时加载下一页，否则重置到第一页。
  loadHistory: function (append) {
    var self = this;
    var page = append ? (this.data.historyPage || 1) + 1 : 1;
    if (append && (this.data.historyLoadingMore || !this.data.historyHasMore)) {
      return Promise.resolve();
    }
    if (append) this.setData({ historyLoadingMore: true });
    return app.request({
      url: '/inventory/stocktake/history?page=' + page + '&limit=' + HISTORY_PAGE_SIZE
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
      var merged = append ? self.data.historyList.concat(list) : list;
      var hasMore = list.length >= HISTORY_PAGE_SIZE;
      self.setData({
        historyList: merged,
        historyPage: page,
        historyHasMore: hasMore,
        historyLoadingMore: false,
      });
    }).catch(function (err) {
      console.error('Load history fail:', err);
      if (!append) self.setData({ historyList: [] });
      self.setData({ historyLoadingMore: false });
      wx.showToast({ title: self._errorText(err, '盘点记录加载失败'), icon: 'none' });
      return Promise.reject(err);
    });
  },

  // P2 修复：点击历史卡片展开/折叠明细
  toggleHistoryDetail: function (event) {
    var self = this;
    var sessionId = event.currentTarget.dataset.id;
    if (this.data.expandedHistoryId === sessionId) {
      this.setData({ expandedHistoryId: null });
      return;
    }
    this.setData({ expandedHistoryId: sessionId });
    // 已缓存则直接展示
    if (this.data.historyDetailCache[sessionId]) return;
    // 拉取明细
    this.setData({ historyDetailLoading: true });
    app.request({
      url: '/inventory/stocktake/history/' + sessionId
    }).then(function (data) {
      var items = (data && data.items) || [];
      var cache = Object.assign({}, self.data.historyDetailCache);
      cache[sessionId] = items.map(function (item) {
        return Object.assign({}, item, {
          variance_text: self._formatVariance(item.variance),
        });
      });
      self.setData({ historyDetailCache: cache, historyDetailLoading: false });
    }).catch(function (err) {
      console.error('Load history detail fail:', err);
      self.setData({ historyDetailLoading: false });
      wx.showToast({ title: self._errorText(err, '明细加载失败'), icon: 'none' });
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
