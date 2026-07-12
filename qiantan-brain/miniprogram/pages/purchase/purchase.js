/** 采购清单 — AI建议→编辑→验收→入库→付款→对账 */
var app = getApp();

function money(v) { return Math.round(Number(v || 0) * 100) / 100; }

Page({
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
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadList();
    this.loadSuppliers();
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadList().then(function () { wx.stopPullDownRefresh(); })
      .catch(function () { wx.stopPullDownRefresh(); });
  },

  // ── 数据加载 ──────────────────────────────────────────

  loadSuppliers: function () {
    var self = this;
    app.request({ url: '/accounts/supplier-balance' }).then(function (data) {
      self.setData({ suppliers: (data && data.items) || [] });
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
    if (isNaN(actQty) || actQty === 0) actQty = recQty;
    var estCost = Number(item.estimated_unit_cost) || 0;
    var actCost = Number(item.actual_unit_cost);
    if (isNaN(actCost) || actCost === 0) actCost = estCost;
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

  // ── 编辑数量与成本 ────────────────────────────────────

  startEdit: function (e) { this.setData({ editingItemId: e.currentTarget.dataset.id }); },

  editQty: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var num = parseFloat(e.detail.value) || 0;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var item = list.items[idx];
    var s = money(num * item.actual_unit_cost);
    var dev = 0, tone = 'flat';
    if (item.recommended_qty > 0) { dev = Math.round((num - item.recommended_qty) / item.recommended_qty * 100); tone = dev > 0 ? 'up' : (dev < 0 ? 'down' : 'flat'); }
    this.setData({
      ['listData.items[' + idx + '].actual_subtotal']: s,
      ['listData.items[' + idx + '].deviation_percent']: dev,
      ['listData.items[' + idx + '].deviation_tone']: tone,
      'listData.total_actual_cost': this._recomputeTotal()
    });
  },

  editCost: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var num = parseFloat(e.detail.value) || 0;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var item = list.items[idx];
    this.setData({
      ['listData.items[' + idx + '].actual_subtotal']: money(item.actual_qty * num),
      'listData.total_actual_cost': this._recomputeTotal()
    });
  },

  saveItem: function (e) {
    var itemId = e.currentTarget.dataset.id;
    var field = e.currentTarget.dataset.field;
    var num = parseFloat(e.detail.value) || 0;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    var item = list.items[idx];
    var apiData = { actual_qty: item.actual_qty, actual_unit_cost: item.actual_unit_cost };
    if (field === 'qty') { apiData.actual_qty = num; this.setData({ ['listData.items[' + idx + '].actual_qty']: num }); }
    else if (field === 'cost') { apiData.actual_unit_cost = num; this.setData({ ['listData.items[' + idx + '].actual_unit_cost']: num }); }
    this.setData({ ['listData.items[' + idx + '].actual_subtotal']: money(item.actual_qty * (field === 'cost' ? num : item.actual_unit_cost)), 'listData.total_actual_cost': this._recomputeTotal(), editingItemId: '' });
    app.request({ url: '/purchase/item/' + itemId, method: 'PUT', data: apiData }).catch(function () {});
  },

  _recomputeTotal: function () {
    var items = this.data.listData.items, total = 0;
    for (var i = 0; i < items.length; i++) { if (items[i].status !== 'cancelled') total += items[i].actual_subtotal; }
    return money(total);
  },

  cancelItem: function (e) {
    var itemId = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '取消采购', content: '确认取消该采购项？', confirmColor: '#d9524a', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/purchase/item/' + itemId, method: 'DELETE' }).then(function () { wx.showToast({ title: '已取消', icon: 'none' }); self.loadList(); }).catch(function () {});
    }});
  },

  // ── 供应商选择 ────────────────────────────────────────

  pickSupplier: function (e) {
    this.setData({ showSupplierPicker: true, supplierPickItemId: e.currentTarget.dataset.id });
  },

  selectSupplier: function (e) {
    var supplierId = e.currentTarget.dataset.id;
    var supplierName = e.currentTarget.dataset.name;
    var itemId = this.data.supplierPickItemId;
    var list = this.data.listData; if (!list) return;
    var idx = this._findItemIndex(list.items, itemId); if (idx < 0) return;
    this.setData({ ['listData.items[' + idx + '].supplier_id']: supplierId, ['listData.items[' + idx + '].supplier_name']: supplierName, showSupplierPicker: false });
    app.request({ url: '/purchase/item/' + itemId, method: 'PUT', data: { supplier_id: supplierId } }).catch(function () {});
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
    if (!list) return;
    var items = [];
    var acc = this.data.acceptanceItems;
    list.items.forEach(function (item) {
      if (item.status === 'cancelled') return;
      var a = acc[item.item_id] || {};
      items.push({
        item_id: item.item_id, arrival_qty: a.arrival_qty || item.actual_qty,
        accepted_qty: a.accepted_qty || item.actual_qty,
        shortage_qty: a.shortage_qty || 0, damaged_qty: a.damaged_qty || 0,
        rejected_qty: a.rejected_qty || 0, returned_qty: a.returned_qty || 0,
        replenish_qty: a.replenish_qty || 0,
        package_count: a.package_count || null,
        gross_weight: a.gross_weight || null, tare_weight: a.tare_weight || null,
        net_weight: a.net_weight || null,
        actual_unit_cost: a.actual_unit_cost || item.actual_unit_cost,
        quality_ok: a.quality_ok !== false,
        acceptance_notes: a.acceptance_notes || ''
      });
    });
    this.setData({ submitting: true });
    app.request({ url: '/purchase/' + list.list_id + '/acceptance', method: 'POST', data: { items: items, notes: '' } })
      .then(function (data) {
        self.setData({ submitting: false });
        wx.showToast({ title: '验收完成', icon: 'success' });
        // Now confirm → batch + inventory + payable
        app.request({ url: '/purchase/' + list.list_id + '/acceptance/confirm', method: 'POST', data: {} })
          .then(function (result) {
            self.setData({ confirmed: true, confirmResult: result, acceptanceMode: false });
          }).catch(function (err) { wx.showToast({ title: (err.body && err.body.detail) || '入库失败', icon: 'none' }); });
      }).catch(function (err) {
        self.setData({ submitting: false });
        wx.showToast({ title: (err.body && err.body.detail) || '验收失败', icon: 'none' });
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
    // Find the first supplier from items or pick manually
    this.setData({ showPayment: true, paymentAmount: list.total_actual_cost, paymentNote: '' });
    this.loadSuppliers();
  },

  closePayment: function () { this.setData({ showPayment: false }); },

  pickPaymentSupplier: function (e) {
    this.setData({
      paymentSupplierId: e.currentTarget.dataset.id,
      paymentSupplierName: e.currentTarget.dataset.name
    });
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
      supplier_id: this.data.paymentSupplierId, amount: this.data.paymentAmount,
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

  openStatement: function (e) {
    var supplierId = e.currentTarget.dataset.id;
    var self = this;
    app.request({ url: '/accounts/supplier/' + supplierId + '/statement' }).then(function (data) {
      self.setData({ showStatement: true, statementData: data, statementSupplierId: supplierId });
    }).catch(function () {});
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
});
