/** 财务 v3.2 — 费用、月报、发票与支付渠道对账 */
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
    skin: '', loading: false, submitting: false, invoiceLoading: false, tab: 'expenses',
    expensesLoaded: false, invoicesLoaded: false, reportLoaded: false,
    // 费用
    expenses: [], showExpForm: false,
    expForm: { category: 'rent', amount: '', description: '', expense_date: '', payment_method: 'cash' },
    expFilterPeriod: 'current', expFilterCategory: 'all',
    // 月报
    month: localMonth(), monthlyReport: null, reportLoading: false,
    // 发票
    invoices: [], showInvForm: false, expenseSubmitting: false, invoiceSubmitting: false,
    invForm: { invoice_number: '', supplier_name: '', amount: '', invoice_date: '' },
    // 支付对账
    reconDate: localDate(), reconChannel: 'wechat', reconLoading: false, reconUploading: false,
    reconTasks: [], reconResult: null, reconDifferences: [], selectedReconTaskId: '',
  },

  onShow: function () {
    this.setData({ skin: 'skin-' + app.resolveSkin(), month: this.data.month || localMonth() });
    this.loadTab();
  },

  switchTab: function (e) {
    this.setData({ tab: e.currentTarget.dataset.tab });
    this.loadTab();
  },

  // 数据导出入口：跳到经营管理页执行（避免重复实现）
  goExport: function () {
    wx.navigateTo({ url: '/pages/ops/ops?tab=export' });
  },

  // 按当前 Tab 加载数据(原 loadAll,改名避免误解为"加载全部")
  loadTab: function () {
    if (this.data.tab === 'expenses') this.loadExpenses();
    else if (this.data.tab === 'report') this.loadReport();
    else if (this.data.tab === 'invoices') this.loadInvoices();
    else if (this.data.tab === 'recon') this.loadReconciliation();
  },

  // ── 费用 ──
  loadExpenses: function () {
    var self = this, today = new Date();
    var year = today.getFullYear();
    var month = today.getMonth();
    var start;
    var end;
    var period = this.data.expFilterPeriod;
    if (period === 'last') {
      start = new Date(year, month - 1, 1);
      end = new Date(year, month, 0);
    } else if (period === '3months') {
      start = new Date(year, month - 2, 1);
      end = today;
    } else {
      start = new Date(year, month, 1);
      end = today;
    }

    this.setData({ loading: true, expensesLoaded: false });
    app.request({
      url: '/expenses',
      data: { start: localDate(start), end: localDate(end) },
    }).then(function (data) {
      var list = data || [];
      var cat = self.data.expFilterCategory;
      if (cat !== 'all') list = list.filter(function (e) { return e.category === cat; });
      self.setData({ expenses: list, loading: false, expensesLoaded: true });
    }).catch(function () {
      self.setData({ expenses: [], loading: false, expensesLoaded: false });
      wx.showToast({ title: '费用加载失败', icon: 'none' });
    });
  },

  changeExpPeriod: function (e) { this.setData({ expFilterPeriod: e.currentTarget.dataset.period }); this.loadExpenses(); },
  changeExpCat: function (e) { this.setData({ expFilterCategory: e.currentTarget.dataset.cat }); this.loadExpenses(); },

  openExpForm: function () {
    this.setData({ showExpForm: true, expForm: { category: 'rent', amount: '', description: '', expense_date: localDate(), payment_method: 'cash' } });
  },
  closeExpForm: function () { if (!this.data.expenseSubmitting) this.setData({ showExpForm: false }); },
  // 阻止弹窗内部点击冒泡到遮罩层，保证金额等输入框保持焦点。
  stopMaskTap: function () {},
  onExpField: function (e) {
    var f = e.currentTarget.dataset.field;
    // type-chip 点击时 e.detail.value 为 undefined,从 dataset.val 读取
    var v = (e.detail.value !== undefined && e.detail.value !== null) ? e.detail.value : e.currentTarget.dataset.val;
    var update = {};
    update['expForm.' + f] = v;
    this.setData(update);
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
    if (this.data.submitting) return;
    wx.showModal({
      title: '删除费用', content: '确认删除？',
      success: function (r) {
        if (!r.confirm) return;
        self.setData({ submitting: true });
        app.request({ url: '/expenses/' + id, method: 'DELETE' }).then(function () {
          self.setData({ submitting: false });
          wx.showToast({ title: '已删除', icon: 'success' });
          self.loadExpenses();
        }).catch(function () { self.setData({ submitting: false }); wx.showToast({ title: '删除失败', icon: 'none' }); });
      },
    });
  },

  // ── 月度报表 ──
  loadReport: function () {
    var self = this;
    this.setData({ reportLoading: true, reportLoaded: false });
    app.request({ url: '/expenses/monthly-report', data: { month: this.data.month } }).then(function (data) {
      // 确保 expense_breakdown 是数组(WXML 直接访问 .length 需要)
      var report = data || {};
      if (!Array.isArray(report.expense_breakdown)) report.expense_breakdown = [];
      self.setData({ monthlyReport: report, reportLoading: false, reportLoaded: true }, function () {
        // setData 回调中绘制图表,确保 Canvas DOM 已就绪
        self.drawReportChart(report);
      });
    }).catch(function () {
      self.setData({ monthlyReport: null, reportLoading: false, reportLoaded: false });
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
    this.setData({ invoiceLoading: true, invoicesLoaded: false });
    app.request({ url: '/expenses/invoices' }).then(function (data) {
      self.setData({ invoices: data || [], invoiceLoading: false, invoicesLoaded: true });
    }).catch(function () {
      self.setData({ invoices: [], invoiceLoading: false, invoicesLoaded: false });
      wx.showToast({ title: '发票加载失败', icon: 'none' });
    });
  },

  openInvForm: function () {
    this.setData({ showInvForm: true, invForm: { invoice_number: '', supplier_name: '', amount: '', invoice_date: localDate() } });
  },
  closeInvForm: function () { if (!this.data.invoiceSubmitting) this.setData({ showInvForm: false }); },
  onInvField: function (e) {
    var f = e.currentTarget.dataset.field;
    var v = e.detail.value;
    var update = {};
    update['invForm.' + f] = v;
    this.setData(update);
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

  // ── 支付渠道对账 ──
  loadReconciliation: function () {
    var self = this;
    this.setData({ reconLoading: true });
    app.request({ url: '/reconciliation/tasks', data: { limit: 30 } }).then(function (data) {
      var tasks = (data || []).map(function (item) {
        var statusText = item.status === 'balanced' ? '已平账'
          : item.status === 'resolved' ? '已处理'
            : item.status === 'exception' ? '有差异' : '待账单';
        return Object.assign({}, item, { statusText: statusText });
      });
      self.setData({ reconTasks: tasks, reconLoading: false });
    }).catch(function () {
      self.setData({ reconTasks: [], reconLoading: false });
      wx.showToast({ title: '对账任务加载失败', icon: 'none' });
    });
  },

  changeReconDate: function (e) { this.setData({ reconDate: e.detail.value }); },
  changeReconChannel: function (e) { this.setData({ reconChannel: e.currentTarget.dataset.channel }); },

  chooseReconBill: function () {
    var self = this;
    if (this.data.reconUploading) return;
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['csv', 'txt'],
      success: function (res) {
        var file = res.tempFiles && res.tempFiles[0];
        if (!file) return;
        if (file.size > 10 * 1024 * 1024) {
          wx.showToast({ title: '账单文件不能超过10MB', icon: 'none' });
          return;
        }
        self.uploadReconBill(file);
      },
    });
  },

  uploadReconBill: function (file) {
    var self = this;
    var url = '/reconciliation/import/' + this.data.reconDate
      + '?channel=' + this.data.reconChannel;
    this.setData({ reconUploading: true });
    app.uploadFile({
      url: url,
      filePath: file.path,
      name: 'file',
      timeout: 60000,
    }).then(function (data) {
      self.setData({
        reconUploading: false,
        reconResult: data,
        selectedReconTaskId: data.task_id,
      });
      wx.showToast({ title: data.duplicate ? '账单已导入' : '对账完成', icon: 'success' });
      self.loadReconciliation();
      self.loadReconDifferences(data.task_id);
    }).catch(function (err) {
      self.setData({ reconUploading: false });
      wx.showToast({
        title: (err.body && err.body.detail) || '账单导入失败',
        icon: 'none',
      });
    });
  },

  openReconTask: function (e) {
    var taskId = e.currentTarget.dataset.id;
    this.setData({ selectedReconTaskId: taskId, reconDifferences: [] });
    this.loadReconDifferences(taskId);
  },

  loadReconDifferences: function (taskId) {
    var self = this;
    app.request({ url: '/reconciliation/tasks/' + taskId + '/differences' }).then(function (data) {
      self.setData({ reconDifferences: data || [] });
    }).catch(function () {
      self.setData({ reconDifferences: [] });
      wx.showToast({ title: '差异明细加载失败', icon: 'none' });
    });
  },

  rerunReconciliation: function (e) {
    var reconDate = e.currentTarget.dataset.date;
    var channel = e.currentTarget.dataset.channel;
    var self = this;
    app.request({
      url: '/reconciliation/run/' + reconDate + '?channel=' + channel,
      method: 'POST',
    }).then(function (data) {
      self.setData({ reconResult: data, selectedReconTaskId: data.task_id });
      self.loadReconciliation();
      self.loadReconDifferences(data.task_id);
    }).catch(function () {
      wx.showToast({ title: '重新对账失败', icon: 'none' });
    });
  },

  resolveReconDifference: function (e) {
    var id = e.currentTarget.dataset.id;
    var self = this;
    wx.showModal({
      title: '处理对账差异',
      editable: true,
      placeholderText: '填写核验结果或调整说明',
      success: function (res) {
        var reason = (res.content || '').trim();
        if (!res.confirm) return;
        if (reason.length < 2) {
          wx.showToast({ title: '请填写处理说明', icon: 'none' });
          return;
        }
        app.request({
          url: '/reconciliation/differences/' + id + '/resolve',
          method: 'POST',
          data: { status: 'resolved', resolution: reason },
        }).then(function () {
          wx.showToast({ title: '已处理', icon: 'success' });
          self.loadReconDifferences(self.data.selectedReconTaskId);
          self.loadReconciliation();
        }).catch(function () {
          wx.showToast({ title: '处理失败', icon: 'none' });
        });
      },
    });
  },
});
