/** 财务 v3.1 — 费用管理 + 月度报表(柱状图) + 发票 + 筛选 */
var app = getApp();
var Chart = require('../../utils/chart');

function localDate(d) {
  d = d || new Date();
  var pad = function (n) { return n < 10 ? '0' + n : String(n); };
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}
function localMonth(d) { return localDate(d).slice(0, 7); }

Page({
  data: {
    skin: '', loading: false, tab: 'expenses',
    // 费用
    expenses: [], showExpForm: false,
    expForm: { category: 'rent', amount: '', description: '', expense_date: '', payment_method: 'cash' },
    expFilterPeriod: 'current', expFilterCategory: 'all',
    // 月报
    month: '', monthlyReport: null, reportLoading: false,
    // 发票
    invoices: [], showInvForm: false, expenseSubmitting: false, invoiceSubmitting: false,
    invForm: { invoice_number: '', supplier_name: '', amount: '', invoice_date: '' },
  },

  onShow: function () {
    this.setData({ skin: 'skin-' + app.resolveSkin(), month: this.data.month || localMonth() });
    this.loadTab();
  },

  switchTab: function (e) {
    this.setData({ tab: e.currentTarget.dataset.tab });
    this.loadTab();
  },

  // 按当前 Tab 加载数据(原 loadAll,改名避免误解为"加载全部")
  loadTab: function () {
    if (this.data.tab === 'expenses') this.loadExpenses();
    else if (this.data.tab === 'report') this.loadReport();
    else if (this.data.tab === 'invoices') this.loadInvoices();
  },

  // ── 费用 ──
  loadExpenses: function () {
    var self = this, today = new Date();
    var start = new Date();
    var period = this.data.expFilterPeriod;
    if (period === 'current') start.setDate(1);
    else if (period === '3months') start.setMonth(start.getMonth() - 3);
    else start.setMonth(start.getMonth() - 1);

    app.request({
      url: '/expenses',
      data: { start: localDate(start), end: localDate(today) },
    }).then(function (data) {
      var list = data || [];
      var cat = self.data.expFilterCategory;
      if (cat !== 'all') list = list.filter(function (e) { return e.category === cat; });
      self.setData({ expenses: list });
    }).catch(function () {
      self.setData({ expenses: [] });
      wx.showToast({ title: '费用加载失败', icon: 'none' });
    });
  },

  changeExpPeriod: function (e) { this.setData({ expFilterPeriod: e.currentTarget.dataset.period }); this.loadExpenses(); },
  changeExpCat: function (e) { this.setData({ expFilterCategory: e.currentTarget.dataset.cat }); this.loadExpenses(); },

  openExpForm: function () {
    this.setData({ showExpForm: true, expForm: { category: 'rent', amount: '', description: '', expense_date: localDate(), payment_method: 'cash' } });
  },
  closeExpForm: function () { this.setData({ showExpForm: false }); },
  onExpField: function (e) {
    var f = e.currentTarget.dataset.field;
    // type-chip 点击时 e.detail.value 为 undefined,从 dataset.val 读取
    var v = (e.detail.value !== undefined && e.detail.value !== null) ? e.detail.value : e.currentTarget.dataset.val;
    var ef = this.data.expForm;
    ef[f] = v;
    this.setData({ expForm: ef });
  },

  saveExpense: function () {
    var self = this, ef = this.data.expForm;
    if (this.data.expenseSubmitting) return;
    var amount = Number(ef.amount);
    if (!amount || amount <= 0) { wx.showToast({ title: '请输入有效金额', icon: 'none' }); return; }
    this.setData({ expenseSubmitting: true });
    var payload = {}; Object.keys(ef).forEach(function (key) { payload[key] = ef[key]; }); payload.amount = amount;
    app.request({ url: '/expenses', method: 'POST', data: payload }).then(function () {
      self.setData({ showExpForm: false, expenseSubmitting: false });
      wx.showToast({ title: '已记录', icon: 'success' });
      self.loadExpenses();
    }).catch(function (err) {
      self.setData({ expenseSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '保存失败,请重试', icon: 'none' });
    });
  },

  deleteExpense: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({
      title: '删除费用', content: '确认删除？',
      success: function (r) {
        if (!r.confirm) return;
        app.request({ url: '/expenses/' + id, method: 'DELETE' }).then(function () {
          wx.showToast({ title: '已删除', icon: 'success' });
          self.loadExpenses();
        }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
      },
    });
  },

  // ── 月度报表 ──
  loadReport: function () {
    var self = this;
    this.setData({ reportLoading: true });
    app.request({ url: '/expenses/monthly-report', data: { month: this.data.month } }).then(function (data) {
      // 确保 expense_breakdown 是数组(WXML 直接访问 .length 需要)
      var report = data || {};
      if (!Array.isArray(report.expense_breakdown)) report.expense_breakdown = [];
      self.setData({ monthlyReport: report, reportLoading: false }, function () {
        // setData 回调中绘制图表,确保 Canvas DOM 已就绪
        self.drawReportChart(report);
      });
    }).catch(function () {
      self.setData({ monthlyReport: null, reportLoading: false });
      wx.showToast({ title: '报表加载失败', icon: 'none' });
    });
  },

  changeMonth: function (e) {
    this.setData({ month: e.detail.value });
    this.loadReport();
  },

  // 绘制月度收支柱状图(收入/采购/毛利/费用/净利润)
  drawReportChart: function (report) {
    var data = [
      { name: '收入', amount: Number(report.revenue) || 0 },
      { name: '采购', amount: -(Number(report.purchase_cost) || 0) },
      { name: '毛利', amount: Number(report.gross_profit) || 0 },
      { name: '费用', amount: -(Number(report.expenses) || 0) },
      { name: '净利', amount: Number(report.net_profit) || 0 },
    ];
    var self = this;
    Chart.initCanvas(this, '#reportChart').then(function (canvas) {
      if (!canvas) return;
      Chart.drawBarChart(canvas.ctx, canvas.width, canvas.height, data, {
        valueKey: 'amount',
        labelKey: 'name',
        colors: ['#2BA24C', '#D9524A', '#175C45', '#F3A83B', '#5A9B8E'],
        unitPrefix: '¥',
        recommendLabel: '最优',
      });
    });
  },

  // ── 发票 ──
  loadInvoices: function () {
    var self = this;
    app.request({ url: '/expenses/invoices' }).then(function (data) {
      self.setData({ invoices: data || [] });
    }).catch(function () {
      self.setData({ invoices: [] });
      wx.showToast({ title: '发票加载失败', icon: 'none' });
    });
  },

  openInvForm: function () {
    this.setData({ showInvForm: true, invForm: { invoice_number: '', supplier_name: '', amount: '', invoice_date: localDate() } });
  },
  closeInvForm: function () { this.setData({ showInvForm: false }); },
  onInvField: function (e) {
    var f = e.currentTarget.dataset.field;
    var v = e.detail.value;
    var inv = this.data.invForm;
    inv[f] = v;
    this.setData({ invForm: inv });
  },

  saveInvoice: function () {
    var self = this, inv = this.data.invForm;
    if (this.data.invoiceSubmitting) return;
    var amount = Number(inv.amount);
    if (!inv.invoice_number || !inv.supplier_name || !amount || amount <= 0 || !inv.invoice_date) { wx.showToast({ title: '请填写完整且有效的发票信息', icon: 'none' }); return; }
    this.setData({ invoiceSubmitting: true });
    var payload = {}; Object.keys(inv).forEach(function (key) { payload[key] = inv[key]; }); payload.amount = amount;
    app.request({ url: '/expenses/invoices', method: 'POST', data: payload }).then(function () {
      self.setData({ showInvForm: false, invoiceSubmitting: false });
      wx.showToast({ title: '已归档', icon: 'success' });
      self.loadInvoices();
    }).catch(function (err) {
      self.setData({ invoiceSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '归档失败,请重试', icon: 'none' });
    });
  },
});
