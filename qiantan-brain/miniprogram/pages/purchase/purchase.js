/** 采购清单 — AI建议→编辑→验收→入库→付款→对账 */
var app = getApp();

function money(v) { return Math.round(Number(v || 0) * 100) / 100; }
function hasValue(v) { return v !== undefined && v !== null && v !== ''; }

Page({
  // 阻止弹窗/卡片内部 tap 冒泡到外层关闭区域。
  stopMaskTap: function () {},
  data: {
    skinClass: '', loading: true, submitting: false,
    listData: null, editingItemId: '',
    confirmed: false, confirmResult: null,
    // 验收模式
    acceptanceMode: false, acceptanceItems: {},
    // 付款弹窗
    showPayment: false, paymentSupplierId: '', paymentSupplierName: '',
    paymentAmount: 0, paymentMethod: 'cash', paymentNote: '',
    paymentSubmitting: false,
    // 供应商列表
    suppliers: [], showSupplierPicker: false, supplierPickItemId: '',
    // 对账单
    showStatement: false, statementData: null, statementSupplierId: '',
    // 退货
    showReturn: false, returnItemId: '', returnReason: '', returnQty: 0,
    returnSubmitting: false,
    // 采购历史
    showHistory: false, historyList: [], historyLoaded: false,
  },

  onLoad: function (options) {
    this._pendingStatementSupplierId = options && options.statement ? options.statement : '';
  },

  onShow: function () {
    var self = this;
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadSuppliers();
    this._importPurchaseDraft().then(function () {
      return self.loadList();
    }).then(function () {
      if (self._pendingStatementSupplierId) {
        var supplierId = self._pendingStatementSupplierId;
        self._pendingStatementSupplierId = '';
        self._loadStatement(supplierId);
      }
    });
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadList().then(function () { wx.stopPullDownRefresh(); })
      .catch(function () { wx.stopPullDownRefresh(); });
  },

  // ── 数据加载 ──────────────────────────────────────────

  _importPurchaseDraft: function () {
    var self = this;
    var draft = wx.getStorageSync('purchaseDraft');
    if (!Array.isArray(draft) || draft.length === 0 || this._draftImporting) return Promise.resolve(false);
    this._draftImporting = true;
    this.setData({ submitting: true });
    var items = draft.map(function (item) {
      return {
        product_id: item.product_id || null,
        name: (item.name || '').trim(),
        qty: Number(item.qty || item.quantity || 0),
        unit: item.unit || '斤',
        from: item.from || '经营日历',
      };
    }).filter(function (item) { return item.name && item.qty > 0; });
    if (!items.length) {
      wx.removeStorageSync('purchaseDraft');
      this._draftImporting = false;
      this.setData({ submitting: false });
      return Promise.resolve(false);
    }
    return app.request({ url: '/purchase/from-advice', method: 'POST', data: { items: items } }).then(function (data) {
      wx.removeStorageSync('purchaseDraft');
      self._draftImporting = false;
      self.setData({ submitting: false });
      var unmatched = (data && data.unmatched_items) || [];
      if (unmatched.length) {
        wx.showModal({ title: '已导入采购清单', content: '以下商品未在商品目录中找到：' + unmatched.join('、'), showCancel: false });
      } else {
        wx.showToast({ title: '时令商品已加入清单', icon: 'success' });
      }
      return true;
    }).catch(function (err) {
      self._draftImporting = false;
      self.setData({ submitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '采购草稿导入失败', icon: 'none' });
      return false;
    });
  },

  loadSuppliers: function () {
    var self = this;
    app.request({ url: '/catalog/suppliers', data: { limit: 200 } }).then(function (data) {
      var items = (data && data.items) || [];
      self.setData({ suppliers: items.map(function (s) {
        return { supplier_id: s.supplier_id, supplier_name: s.name, balance: s.current_balance || 0 };
      }) });
    }).catch(function () {});
  },

  loadList: function () {
    var self = this;
    this.setData({ loading: true });
    return app.request({ url: '/purchase/today' }).then(function (data) {
      var decorated = self._decorateList(data);
      self.setData({ loading: false, listData: decorated, confirmed: false, confirmResult: null, editingItemId: '', acceptanceMode: false, acceptanceItems: {} });
    }).catch(function () { self.setData({ loading: false }); });
  },

  // ── 装饰 ──────────────────────────────────────────────

  _findItemIndex: function (items, itemId) {
    for (var i = 0; i < items.length; i++) { if (items[i].item_id === itemId) return i; }
    return -1;
  },

  _decorateItem: function (item) {
    var recQty = Number(item.recommended_qty) || 0;
    var actQty = Number(item.actual_qty);
    if (!hasValue(item.actual_qty) || isNaN(actQty)) actQty = recQty;
    var estCost = Number(item.estimated_unit_cost) || 0;
    var actCost = Number(item.actual_unit_cost);
    if (!hasValue(item.actual_unit_cost) || isNaN(actCost)) actCost = estCost;
    var actualSubtotal = money(actQty * actCost);

    var deviation = 0, deviationTone = 'flat';
    if (recQty > 0) { deviation = Math.round((actQty - recQty) / recQty * 100); deviationTone = deviation > 0 ? 'up' : (deviation < 0 ? 'down' : 'flat'); }

    var rawStatus = item.status || 'pending';
    var statusText = '待采购', statusClass = 'pending', statusTag = 'tag-amber';
    if (rawStatus === 'purchased') { statusText = '已入库'; statusClass = 'purchased'; statusTag = 'tag-green'; }
    else if (rawStatus === 'returned') { statusText = '已退货'; statusClass = 'returned'; statusTag = 'tag-red'; }
    else if (rawStatus === 'cancelled') { statusText = '已取消'; statusClass = 'cancelled'; statusTag = 'tag-muted'; }

    var arrivalQty = Number(item.arrival_qty);
    var acceptedQty = Number(item.accepted_qty);
    var hasAcceptance = !isNaN(arrivalQty) && arrivalQty > 0;

    return {
      item_id: item.item_id, product_id: item.product_id,
      product_name: item.product_name || ('商品' + item.product_id),
      recommended_qty: recQty, actual_qty: actQty, unit: item.unit || '斤',
      estimated_unit_cost: estCost, actual_unit_cost: actCost,
      estimated_cost: Number(item.estimated_cost) || money(recQty * estCost),
      actual_subtotal: actualSubtotal,
      reason: item.reason || '', status: rawStatus,
      status_text: statusText, status_class: statusClass, status_tag: statusTag,
      deviation_percent: deviation, deviation_tone: deviationTone,
      editable: rawStatus !== 'purchased' && rawStatus !== 'cancelled' && rawStatus !== 'returned',
      // 验收
      has_acceptance: hasAcceptance,
      arrival_qty: arrivalQty, accepted_qty: acceptedQty,
      shortage_qty: Number(item.shortage_qty) || 0, damaged_qty: Number(item.damaged_qty) || 0,
      rejected_qty: Number(item.rejected_qty) || 0, returned_qty: Number(item.returned_qty) || 0,
      package_count: item.package_count, net_weight: Number(item.net_weight) || 0,
      quality_ok: item.quality_ok !== false,
      acceptance_notes: item.acceptance_notes || '',
      supplier_id: item.supplier_id || '',
      supplier_name: item.supplier_name || '',
    };
  },

  _decorateList: function (data) {
    if (!data) return null;
    var self = this;
    var items = (data.items || []).map(function (item) { return self._decorateItem(item); });
    var totalActual = 0, purchasedCount = 0;
    for (var i = 0; i < items.length; i++) {
      if (items[i].status !== 'cancelled') totalActual += items[i].actual_subtotal;
      if (items[i].status === 'purchased') purchasedCount++;
    }
    var rawStatus = data.status || 'draft';
    var statusTexts = { draft: '草稿', confirmed: '已下单', partial_arrival: '部分到货', accepted: '待入库', stored: '已入库', completed: '已完成' };
    var statusTags = { draft: 'tag-amber', confirmed: 'tag-amber', partial_arrival: 'tag-amber', accepted: 'tag-blue', stored: 'tag-green', completed: 'tag-green' };

    return {
      list_id: data.list_id, status: rawStatus,
      order_no: data.order_no || '',
      expected_arrival: data.expected_arrival_date || null,
      list_status_text: statusTexts[rawStatus] || rawStatus,
      list_status_tag: statusTags[rawStatus] || 'tag-amber',
      payment_status: data.payment_status || 'unpaid',
      paid_amount: Number(data.paid_amount) || 0,
      total_estimated_cost: money(Number(data.total_estimated_cost) || 0),
      total_actual_cost: money(totalActual),
      item_count: data.item_count || items.length,
      purchased_count: purchasedCount,
      created_at: data.created_at, confirmed_at: data.confirmed_at, accepted_at: data.accepted_at,
      items: items,
      can_confirm_order: rawStatus === 'draft',
    };
  },

  // ── 生成采购单 ────────────────────────────────────────

  generateFromAdvice: function () {
    var self = this;
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    app.request({ url: '/purchase/from-advice', method: 'POST', data: { recommendation_ids: [] } })
      .then(function () { self.setData({ submitting: false }); wx.showToast({ title: '已生成采购清单', icon: 'success' }); self.loadList(); })
      .catch(function () { self.setData({ submitting: false }); });
  },

  // ── 确认采购单 (draft → confirmed) ─────────────────────

  confirmOrder: function () {
    var self = this;
    if (this.data.submitting || !this.data.listData) return;
    wx.showModal({
      title: '确认采购单', content: '确认后将正式生成采购订单，确定吗？',
      confirmText: '确认下单', cancelText: '再改改',
      success: function (r) {
        if (!r.confirm) return;
        self.setData({ submitting: true });
        app.request({ url: '/purchase/' + self.data.listData.list_id + '/confirm-order', method: 'POST', data: {} })
          .then(function (data) {
            self.setData({ submitting: false });
            wx.showToast({ title: '采购单已确认', icon: 'success' });
            self.loadList();
          }).catch(function () { self.setData({ submitting: false }); });
      },
    });
  },

  // ── 采购历史 ──────────────────────────────────────────

  loadHistory: function () {
    var self = this;
    app.request({ url: '/purchase/history', data: { days: 30, limit: 10 } }).then(function (data) {
      var items = (data || []).map(function (h) {
        var statusMap = { stored: '已入库', completed: '已完成', cancelled: '已取消', returned: '已退货' };
        return {
          list_id: h.list_id,
          order_no: h.order_no || '',
          status_text: statusMap[h.status] || h.status,
          item_count: h.item_count,
          total_cost: h.total_actual_cost || h.total_estimated_cost || 0,
          payment_status: h.payment_status,
          created_at: h.created_at,
        };
      });
      self.setData({ historyList: items, historyLoaded: true });
    }).catch(function () {});
  },

  toggleHistory: function () {
    var show = !this.data.showHistory;
    this.setData({ showHistory: show });
    if (show && !this.data.historyLoaded) this.loadHistory();
  },

  // ── 编辑数量与成本 ────────────────────────────────────

  startEdit: function (e) { this.setData({ editingItemId: e.currentTarget.dataset.id }); },

  editQty: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var num = parseFloat(e.detail.value);
    if (!isFinite(num) || num < 0) num = 0;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var items = list.items.slice();
    var item = Object.assign({}, items[idx], { actual_qty: num });
    item.actual_subtotal = money(num * item.actual_unit_cost);
    item.deviation_percent = item.recommended_qty > 0 ? Math.round((num - item.recommended_qty) / item.recommended_qty * 100) : 0;
    item.deviation_tone = item.deviation_percent > 0 ? 'up' : (item.deviation_percent < 0 ? 'down' : 'flat');
    items[idx] = item;
    this.setData({ 'listData.items': items, 'listData.total_actual_cost': this._recomputeTotal(items) });
  },

  editCost: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var num = parseFloat(e.detail.value);
    if (!isFinite(num) || num < 0) num = 0;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var items = list.items.slice();
    var item = Object.assign({}, items[idx], { actual_unit_cost: num });
    item.actual_subtotal = money(item.actual_qty * num);
    items[idx] = item;
    this.setData({ 'listData.items': items, 'listData.total_actual_cost': this._recomputeTotal(items) });
  },

  saveItem: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var field = e.currentTarget.dataset.field;
    var num = parseFloat(e.detail.value);
    if (!isFinite(num) || num < 0) { wx.showToast({ title: '数量和单价不能为负数', icon: 'none' }); this.loadList(); return; }
    if (this.data.submitting) return;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var items = list.items.slice();
    var item = Object.assign({}, items[idx]);
    if (field === 'qty') item.actual_qty = num;
    else if (field === 'cost') item.actual_unit_cost = num;
    item.actual_subtotal = money(item.actual_qty * item.actual_unit_cost);
    items[idx] = item;
    var apiData = { actual_qty: item.actual_qty, actual_unit_cost: item.actual_unit_cost };
    this.setData({ 'listData.items': items, 'listData.total_actual_cost': this._recomputeTotal(items), editingItemId: '', submitting: true });
    var self = this;
    app.request({ url: '/purchase/item/' + itemId, method: 'PUT', data: apiData }).then(function () {
      self.setData({ submitting: false });
    }).catch(function (err) {
      self.setData({ submitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '保存失败，已恢复服务端数据', icon: 'none' });
      self.loadList();
    });
  },

  _recomputeTotal: function (items) {
    items = items || (this.data.listData && this.data.listData.items) || [];
    var total = 0;
    for (var i = 0; i < items.length; i++) { if (items[i].status !== 'cancelled') total += Number(items[i].actual_subtotal) || 0; }
    return money(total);
  },

  cancelItem: function (e) {
    var itemId = e.currentTarget.dataset.id, self = this;
    if (this.data.submitting) return;
    wx.showModal({ title: '取消采购', content: '确认取消该采购项？', confirmColor: '#d9524a', success: function (r) {
      if (!r.confirm) return;
      self.setData({ submitting: true });
      app.request({ url: '/purchase/item/' + itemId, method: 'DELETE' }).then(function () { self.setData({ submitting: false }); wx.showToast({ title: '已取消', icon: 'none' }); self.loadList(); }).catch(function (err) { self.setData({ submitting: false }); wx.showToast({ title: (err.body && err.body.detail) || '取消失败', icon: 'none' }); self.loadList(); });
    }});
  },

  // ── 供应商选择 ────────────────────────────────────────

  pickSupplier: function (e) {
    this.setData({ showSupplierPicker: true, supplierPickItemId: e.currentTarget.dataset.id });
  },

  selectSupplier: function (e) {
    if (this.data.submitting) return;
    var supplierId = e.currentTarget.dataset.id;
    var supplierName = e.currentTarget.dataset.name;
    var itemId = this.data.supplierPickItemId;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    this.setData({ ['listData.items[' + idx + '].supplier_id']: supplierId, ['listData.items[' + idx + '].supplier_name']: supplierName, showSupplierPicker: false, submitting: true });
    var self = this;
    app.request({ url: '/purchase/item/' + itemId, method: 'PUT', data: { supplier_id: supplierId } }).then(function () {
      self.setData({ submitting: false });
    }).catch(function (err) { self.setData({ submitting: false }); wx.showToast({ title: (err.body && err.body.detail) || '供应商保存失败', icon: 'none' }); self.loadList(); });
  },

  closeSupplierPicker: function () { this.setData({ showSupplierPicker: false }); },

  // ── 到货验收 ──────────────────────────────────────────

  enterAcceptance: function () {
    var list = this.data.listData;
    if (!list) return;
    var acc = {};
    list.items.forEach(function (item) {
      if (item.status === 'cancelled') return;
      acc[item.item_id] = {
        arrival_qty: item.actual_qty, accepted_qty: item.actual_qty,
        shortage_qty: 0, damaged_qty: 0, rejected_qty: 0, returned_qty: 0, replenish_qty: 0,
        package_count: item.package_count || 0, gross_weight: 0, tare_weight: 0,
        net_weight: item.net_weight || item.actual_qty, actual_unit_cost: item.actual_unit_cost,
        quality_ok: true, acceptance_notes: ''
      };
    });
    this.setData({ acceptanceMode: true, acceptanceItems: acc });
  },

  exitAcceptance: function () { this.setData({ acceptanceMode: false }); },

  editAcceptanceField: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var field = e.currentTarget.dataset.field;
    var val = parseFloat(e.detail.value) || 0;
    var acc = this.data.acceptanceItems;
    if (!acc[itemId]) acc[itemId] = {};
    acc[itemId][field] = val;
    // 净重自动计算: 毛重 - 皮重
    if (field === 'gross_weight' || field === 'tare_weight') {
      var g = Number(acc[itemId].gross_weight) || 0;
      var t = Number(acc[itemId].tare_weight) || 0;
      acc[itemId].net_weight = Math.max(0, g - t);
      acc[itemId].accepted_qty = acc[itemId].net_weight;
    }
    this.setData({ acceptanceItems: acc });
  },

  editAcceptanceStr: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var field = e.currentTarget.dataset.field;
    var acc = this.data.acceptanceItems;
    if (!acc[itemId]) acc[itemId] = {};
    acc[itemId][field] = e.detail.value || '';
    this.setData({ acceptanceItems: acc });
  },

  toggleQuality: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var acc = this.data.acceptanceItems;
    if (!acc[itemId]) acc[itemId] = { quality_ok: true };
    acc[itemId].quality_ok = !acc[itemId].quality_ok;
    this.setData({ acceptanceItems: acc });
  },

  submitAcceptance: function () {
    var self = this, list = this.data.listData;
    if (!list || this.data.submitting) return;
    var items = [];
    var acc = this.data.acceptanceItems;
    var invalidName = '';
    list.items.forEach(function (item) {
      if (item.status === 'cancelled') return;
      var a = acc[item.item_id] || {};
      var arrivalQty = hasValue(a.arrival_qty) ? Number(a.arrival_qty) : Number(item.actual_qty);
      var acceptedQty = hasValue(a.accepted_qty) ? Number(a.accepted_qty) : Number(item.actual_qty);
      if (!isFinite(arrivalQty) || !isFinite(acceptedQty) || arrivalQty < 0 || acceptedQty < 0 || acceptedQty > arrivalQty) invalidName = invalidName || item.product_name;
      items.push({
        item_id: item.item_id, arrival_qty: arrivalQty,
        accepted_qty: acceptedQty,
        shortage_qty: hasValue(a.shortage_qty) ? Number(a.shortage_qty) : 0,
        damaged_qty: hasValue(a.damaged_qty) ? Number(a.damaged_qty) : 0,
        rejected_qty: hasValue(a.rejected_qty) ? Number(a.rejected_qty) : 0,
        returned_qty: hasValue(a.returned_qty) ? Number(a.returned_qty) : 0,
        replenish_qty: hasValue(a.replenish_qty) ? Number(a.replenish_qty) : 0,
        package_count: hasValue(a.package_count) ? Number(a.package_count) : null,
        gross_weight: hasValue(a.gross_weight) ? Number(a.gross_weight) : null,
        tare_weight: hasValue(a.tare_weight) ? Number(a.tare_weight) : null,
        net_weight: hasValue(a.net_weight) ? Number(a.net_weight) : null,
        actual_unit_cost: hasValue(a.actual_unit_cost) ? Number(a.actual_unit_cost) : item.actual_unit_cost,
        quality_ok: a.quality_ok !== false,
        acceptance_notes: a.acceptance_notes || ''
      });
    });
    if (invalidName) { wx.showToast({ title: invalidName + '的合格数量不能大于到货数量', icon: 'none' }); return; }
    this.setData({ submitting: true });
    app.request({ url: '/purchase/' + list.list_id + '/acceptance', method: 'POST', data: { items: items, notes: '' } })
      .then(function () {
        return app.request({ url: '/purchase/' + list.list_id + '/acceptance/confirm', method: 'POST', data: {} });
      }).then(function (result) {
        self.setData({ submitting: false, confirmed: true, confirmResult: result, acceptanceMode: false });
        wx.showToast({ title: '验收并入库完成', icon: 'success' });
        self.loadList();
      }).catch(function (err) {
        self.setData({ submitting: false });
        wx.showToast({ title: (err.body && err.body.detail) || '验收或入库失败', icon: 'none' });
        self.loadList();
      });
  },

  // ── 确认入库（跳过验收的快速通道）─────────────────────

  confirmPurchase: function () {
    var self = this;
    if (this.data.submitting || !this.data.listData) return;
    // If in acceptance mode, use the acceptance flow
    if (this.data.acceptanceMode) { this.submitAcceptance(); return; }
    this.setData({ submitting: true });
    app.request({ url: '/purchase/' + this.data.listData.list_id + '/confirm', method: 'POST', data: {} })
      .then(function (data) { self.setData({ submitting: false, confirmed: true, confirmResult: data }); })
      .catch(function () { self.setData({ submitting: false }); });
  },

  // ── 供应商付款 ────────────────────────────────────────

  openPayment: function () {
    var list = this.data.listData; if (!list) return;
    // 多供应商清单不能把整单金额默认付给某一个供应商。
    this.setData({ showPayment: true, paymentSupplierId: '', paymentSupplierName: '', paymentAmount: 0, paymentNote: '', paymentPayableIds: [] });
    this.loadSuppliers();
  },

  closePayment: function () { this.setData({ showPayment: false }); },

  pickPaymentSupplier: function (e) {
    var supplierId = e.currentTarget.dataset.id;
    var amount = 0;
    var list = this.data.listData;
    if (list) {
      list.items.forEach(function (item) {
        if (item.supplier_id === supplierId && item.status !== 'cancelled') amount += Number(item.actual_subtotal) || 0;
      });
    }
    var self = this;
    this.setData({
      paymentSupplierId: supplierId,
      paymentSupplierName: e.currentTarget.dataset.name,
      paymentAmount: money(amount),
      paymentPayableIds: []
    });
    app.request({ url: '/accounts/supplier/' + supplierId + '/statement' }).then(function (data) {
      var openRows = (data.items || []).filter(function (row) {
        return row.direction === 'purchase' && Number(row.remaining_amount) > 0;
      });
      var ids = openRows.map(function (row) { return row.id; });
      var remaining = openRows.reduce(function (sum, row) { return sum + Number(row.remaining_amount || 0); }, 0);
      self.setData({ paymentPayableIds: ids, paymentAmount: money(remaining) });
    }).catch(function () {});
  },

  editPaymentAmount: function (e) { this.setData({ paymentAmount: parseFloat(e.detail.value) || 0 }); },
  editPaymentNote: function (e) { this.setData({ paymentNote: e.detail.value || '' }); },
  selectPaymentMethod: function (e) { this.setData({ paymentMethod: e.currentTarget.dataset.method }); },

  submitPayment: function () {
    var self = this;
    if (!this.data.paymentSupplierId) { wx.showToast({ title: '请选择供应商', icon: 'none' }); return; }
    if (!this.data.paymentAmount || this.data.paymentAmount <= 0) { wx.showToast({ title: '请输入金额', icon: 'none' }); return; }
    this.setData({ paymentSubmitting: true });
    app.request({ url: '/accounts/supplier-payment', method: 'POST', data: {
      supplier_id: this.data.paymentSupplierId, payable_ids: this.data.paymentPayableIds, amount: this.data.paymentAmount,
      method: this.data.paymentMethod, note: this.data.paymentNote
    }}).then(function () {
      self.setData({ paymentSubmitting: false, showPayment: false });
      wx.showToast({ title: '付款成功', icon: 'success' });
      self.loadSuppliers();
    }).catch(function (err) {
      self.setData({ paymentSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '付款失败', icon: 'none' });
    });
  },

  // ── 对账单 ────────────────────────────────────────────

  _loadStatement: function (supplierId) {
    var self = this;
    if (!supplierId) return Promise.resolve(false);
    wx.showLoading({ title: '加载对账单...' });
    return app.request({ url: '/accounts/supplier/' + supplierId + '/statement' }).then(function (data) {
      wx.hideLoading();
      self.setData({ showStatement: true, statementData: data, statementSupplierId: supplierId });
      return true;
    }).catch(function (err) {
      wx.hideLoading();
      wx.showToast({ title: (err.body && err.body.detail) || '对账单加载失败', icon: 'none' });
      return false;
    });
  },

  openStatement: function (e) {
    return this._loadStatement(e.currentTarget.dataset.id);
  },

  closeStatement: function () { this.setData({ showStatement: false, statementData: null }); },

  // ── 退货 ──────────────────────────────────────────────

  openReturn: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var item = list.items[idx];
    this.setData({ showReturn: true, returnItemId: itemId, returnReason: '', returnQty: item.accepted_qty || item.actual_qty });
  },

  closeReturn: function () { this.setData({ showReturn: false }); },
  editReturnReason: function (e) { this.setData({ returnReason: e.detail.value || '' }); },
  editReturnQty: function (e) { this.setData({ returnQty: parseFloat(e.detail.value) || 0 }); },

  submitReturn: function () {
    var self = this;
    if (!this.data.returnReason.trim()) { wx.showToast({ title: '请填写退货原因', icon: 'none' }); return; }
    if (!this.data.returnQty || this.data.returnQty <= 0) { wx.showToast({ title: '请输入退货数量', icon: 'none' }); return; }
    this.setData({ returnSubmitting: true });
    app.request({ url: '/purchase/items/' + this.data.returnItemId + '/return', method: 'POST', data: {
      item_id: this.data.returnItemId, return_qty: this.data.returnQty,
      reason: this.data.returnReason, offset_payable: true
    }}).then(function () {
      self.setData({ returnSubmitting: false, showReturn: false });
      wx.showToast({ title: '退货完成', icon: 'success' }); self.loadList();
    }).catch(function (err) {
      self.setData({ returnSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '退货失败', icon: 'none' });
    });
  },

  // ── 导航 ──────────────────────────────────────────────

  viewInventory: function () { wx.switchTab({ url: '/pages/inventory/inventory' }); },
  goHome: function () { wx.switchTab({ url: '/pages/index/index' }); },
  goSupplier: function () { wx.navigateTo({ url: '/pages/supplier/supplier' }); },
});
