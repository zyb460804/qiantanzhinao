/** 财务 — 费用管理 + 月度报表 + 发票 */
var app = getApp();
function money(v) { return Math.round(Number(v || 0) * 100) / 100; }

Page({
  data: {
    skin: '', loading: false, tab: 'expenses',
    expenses: [], showExpForm: false, expForm: { category: 'rent', amount: '', description: '', expense_date: '', payment_method: 'cash' },
    month: '', monthlyReport: null,
    invoices: [], showInvForm: false, invForm: { invoice_number: '', supplier_name: '', amount: '', invoice_date: '' },
  },
  onShow: function () {
    this.setData({ skin: 'skin-' + app.resolveSkin(), month: new Date().toISOString().slice(0, 7) });
    this.loadAll();
  },
  switchTab: function (e) { this.setData({ tab: e.currentTarget.dataset.tab }); this.loadAll(); },
  loadAll: function () {
    if (this.data.tab === 'expenses') this.loadExpenses();
    else if (this.data.tab === 'report') this.loadReport();
    else if (this.data.tab === 'invoices') this.loadInvoices();
  },

  // Expenses
  loadExpenses: function () {
    var self = this, today = new Date().toISOString().slice(0, 10);
    var start = new Date(); start.setMonth(start.getMonth() - 1);
    app.request({ url: '/expenses?start=' + start.toISOString().slice(0, 10) + '&end=' + today }).then(function (data) { self.setData({ expenses: data || [] }); });
  },
  openExpForm: function () { this.setData({ showExpForm: true, expForm: { category: 'rent', amount: '', description: '', expense_date: new Date().toISOString().slice(0, 10), payment_method: 'cash' } }); },
  closeExpForm: function () { this.setData({ showExpForm: false }); },
  onExpField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var ef = this.data.expForm; ef[f] = v; this.setData({ expForm: ef }); },
  saveExpense: function () {
    var self = this, ef = this.data.expForm;
    if (!ef.amount || Number(ef.amount) <= 0) { wx.showToast({ title: '请输入金额', icon: 'none' }); return; }
    app.request({ url: '/expenses', method: 'POST', data: ef }).then(function () { self.setData({ showExpForm: false }); wx.showToast({ title: '已记录', icon: 'success' }); self.loadExpenses(); });
  },
  deleteExpense: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '删除费用', content: '确认删除？', success: function (r) { if (!r.confirm) return; app.request({ url: '/expenses/' + id, method: 'DELETE' }).then(function () { self.loadExpenses(); }); }});
  },

  // Monthly Report
  loadReport: function () {
    var self = this;
    app.request({ url: '/expenses/monthly-report?month=' + this.data.month }).then(function (data) { self.setData({ monthlyReport: data }); });
  },
  changeMonth: function (e) { var d = e.detail.value; this.setData({ month: d }); this.loadReport(); },

  // Invoices
  loadInvoices: function () { var self = this; app.request({ url: '/expenses/invoices' }).then(function (data) { self.setData({ invoices: data || [] }); }); },
  openInvForm: function () { this.setData({ showInvForm: true, invForm: { invoice_number: '', supplier_name: '', amount: '', invoice_date: new Date().toISOString().slice(0, 10) } }); },
  closeInvForm: function () { this.setData({ showInvForm: false }); },
  onInvField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var inv = this.data.invForm; inv[f] = v; this.setData({ invForm: inv }); },
  saveInvoice: function () {
    var self = this, inv = this.data.invForm;
    if (!inv.invoice_number || !inv.amount) { wx.showToast({ title: '请填写完整', icon: 'none' }); return; }
    app.request({ url: '/expenses/invoices', method: 'POST', data: inv }).then(function () { self.setData({ showInvForm: false }); wx.showToast({ title: '已归档', icon: 'success' }); self.loadInvoices(); });
  },
});
