/** 经营管理 — 报损/临期清货/客户赊账/数据导出 */
var app = getApp();

function money(v) { return Math.round(Number(v || 0) * 100) / 100; }

Page({
  data: {
    skin: '', tab: 'waste', loading: false,
    // 报损
    wasteReasons: [], wasteForm: { product_id: '', product_name: '', quantity: '', reason: '腐烂', notes: '' },
    products: [], showWasteForm: false, wasteRecords: [],
    // 临期
    clearanceItems: [], clearanceHours: 24,
    // 客户
    customers: [], showCustomerLedger: false, customerLedger: null, ledgerName: '',
    showRepayForm: false, repayForm: { customer_name: '', amount: '' },
    // 导出
    exportStart: '', exportEnd: '',
  },

  onShow: function () {
    this.setData({ skin: 'skin-' + app.resolveSkin() });
    this.loadAll();
  },

  switchTab: function (e) {
    var t = e.currentTarget.dataset.tab;
    this.setData({ tab: t });
    if (t === 'waste') { this.loadWasteReasons(); this.loadWasteRecords(); this.loadProducts(); }
    else if (t === 'clearance') this.loadClearance();
    else if (t === 'customers') this.loadCustomers();
  },

  loadAll: function () {
    this.loadWasteReasons();
    this.loadWasteRecords();
    this.loadProducts();
    this.loadClearance();
    this.loadCustomers();
  },

  // ── 报损 ──
  loadWasteReasons: function () { var self = this; app.request({ url: '/ops/waste-reasons' }).then(function (data) { self.setData({ wasteReasons: data || [] }); }); },
  loadProducts: function () { var self = this; app.request({ url: '/inventory/current' }).then(function (data) { self.setData({ products: (data || []).filter(function (x) { return Number(x.current_qty) > 0; }) }); }); },
  loadWasteRecords: function () { var self = this; app.request({ url: '/ops/waste?limit=20' }).then(function (data) { self.setData({ wasteRecords: data || [] }); }); },

  openWasteForm: function () { this.setData({ showWasteForm: true, wasteForm: { product_id: '', product_name: '', quantity: '', reason: '腐烂', notes: '' } }); },
  closeWasteForm: function () { this.setData({ showWasteForm: false }); },
  pickWasteProduct: function (e) {
    var idx = e.currentTarget.dataset.index;
    var p = this.data.products[idx];
    this.setData({ 'wasteForm.product_id': p.product_id, 'wasteForm.product_name': p.sku_name || p.product_name });
  },
  onWasteField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var wf = this.data.wasteForm; wf[f] = v; this.setData({ wasteForm: wf }); },
  pickWasteReason: function (e) { this.setData({ 'wasteForm.reason': e.currentTarget.dataset.reason }); },

  submitWaste: function () {
    var self = this, wf = this.data.wasteForm;
    if (!wf.product_id || !wf.quantity || Number(wf.quantity) <= 0) { wx.showToast({ title: '请选择商品和数量', icon: 'none' }); return; }
    var product = this.data.products.find(function (p) { return p.product_id === wf.product_id; });
    app.request({ url: '/ops/waste', method: 'POST', data: { product_id: wf.product_id, sku_id: product ? product.sku_id : null, quantity: Number(wf.quantity), unit: product ? product.unit : '斤', reason: wf.reason, notes: wf.notes } })
      .then(function () { self.setData({ showWasteForm: false }); wx.showToast({ title: '已记录报损', icon: 'success' }); self.loadWasteRecords(); self.loadProducts(); })
      .catch(function (err) { wx.showToast({ title: (err.body && err.body.detail) || '失败', icon: 'none' }); });
  },

  // ── 临期 ──
  loadClearance: function () { var self = this; app.request({ url: '/ops/expiry/clearance?within_hours=' + this.data.clearanceHours }).then(function (data) { self.setData({ clearanceItems: (data && data.items) || [] }); }); },
  changeClearanceHours: function (e) { this.setData({ clearanceHours: Number(e.currentTarget.dataset.h) }); this.loadClearance(); },

  quickDiscount: function (e) {
    var item = this.data.clearanceItems[e.currentTarget.dataset.index];
    if (!item || !item.suggested_price) { wx.showToast({ title: '无建议售价', icon: 'none' }); return; }
    // Navigate to catalog to change price (simplified: just show the suggestion)
    wx.showModal({ title: '临期清货建议', content: item.product_name + '\n剩余' + item.remaining_qty + '\n剩余' + item.hours_left + '小时\n建议售价 ¥' + item.suggested_price.toFixed(2), showCancel: false });
  },

  // ── 客户 ──
  loadCustomers: function () { var self = this; app.request({ url: '/ops/customers' }).then(function (data) { self.setData({ customers: data || [] }); }); },

  viewCustomerLedger: function (e) {
    var name = e.currentTarget.dataset.name, self = this;
    app.request({ url: '/ops/customers/' + encodeURIComponent(name) + '/ledger' }).then(function (data) {
      self.setData({ showCustomerLedger: true, customerLedger: data, ledgerName: name });
    });
  },
  closeLedger: function () { this.setData({ showCustomerLedger: false }); },

  openRepay: function (e) {
    this.setData({ showRepayForm: true, repayForm: { customer_name: e.currentTarget.dataset.name, amount: '' } });
  },
  closeRepay: function () { this.setData({ showRepayForm: false }); },
  onRepayField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var rf = this.data.repayForm; rf[f] = v; this.setData({ repayForm: rf }); },
  submitRepay: function () {
    var self = this, rf = this.data.repayForm;
    if (!rf.amount || Number(rf.amount) <= 0) { wx.showToast({ title: '请输入金额', icon: 'none' }); return; }
    app.request({ url: '/ops/customers/repay', method: 'POST', data: { customer_name: rf.customer_name, amount: Number(rf.amount) } })
      .then(function () { self.setData({ showRepayForm: false }); wx.showToast({ title: '回款已记录', icon: 'success' }); self.loadCustomers(); });
  },

  // ── 导出 ──
  onExportDate: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var d = {}; d[f] = v; this.setData(d); },
  doExport: function (e) {
    var type = e.currentTarget.dataset.type;
    var url = '/ops/export/' + type;
    if (type === 'sales' || type === 'waste') url += '?start_date=' + (this.data.exportStart || new Date().toISOString().slice(0,10)) + '&end_date=' + (this.data.exportEnd || new Date().toISOString().slice(0,10));
    wx.showToast({ title: '导出功能需在服务端下载，请联系管理员', icon: 'none' });
    // In production: use wx.downloadFile with auth header
  },
});
