/** 经营管理 — 报损/临期清货/客户赊账/数据导出 */
var app = getApp();

Page({
  data: {
    skin: '', tab: 'waste', loading: false, submitting: false,
    // 报损
    wasteReasons: [], wasteForm: { product_id: '', product_name: '', quantity: '', reason: '腐烂', notes: '' },
    products: [], showWasteForm: false, wasteRecords: [], wasteSubmitting: false,
    // 临期
    clearanceItems: [], clearanceHours: 24,
    // 客户
    customers: [], filteredCustomers: [], showCustomerLedger: false, customerLedger: null, ledgerName: '',
    customerSearch: '',
    showRepayForm: false, repayForm: { customer_name: '', amount: '' }, repaySubmitting: false,
    // 导出
    exportStart: '', exportEnd: '', exporting: false,
  },

  onLoad: function (options) {
    // 允许外部页面带 ?tab=clearance 直接跳到指定模块
    var valid = { waste: 1, clearance: 1, customers: 1, export: 1 };
    if (options && options.tab && valid[options.tab]) {
      this.setData({ tab: options.tab });
    }
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
  loadWasteReasons: function () {
    var self = this;
    app.request({ url: '/ops/waste-reasons' }).then(function (data) {
      self.setData({ wasteReasons: data || [] });
    }).catch(function () { self.setData({ wasteReasons: ['腐烂', '过期', '破损', '其他'] }); });
  },
  loadProducts: function () {
    var self = this;
    app.request({ url: '/inventory/current' }).then(function (data) {
      var products = (data || []).map(function (x) {
        var qty = Number(x.current_qty != null ? x.current_qty : x.total_qty) || 0;
        var normalized = {};
        Object.keys(x).forEach(function (key) { normalized[key] = x[key]; });
        normalized.current_qty = qty;
        return normalized;
      }).filter(function (x) { return x.current_qty > 0; });
      self.setData({ products: products });
    }).catch(function () { self.setData({ products: [] }); wx.showToast({ title: '库存数据加载失败', icon: 'none' }); });
  },
  loadWasteRecords: function () {
    var self = this;
    app.request({ url: '/ops/waste?limit=20' }).then(function (data) {
      self.setData({ wasteRecords: data || [] });
    }).catch(function () { self.setData({ wasteRecords: [] }); wx.showToast({ title: '报损记录加载失败', icon: 'none' }); });
  },

  openWasteForm: function () { this.setData({ showWasteForm: true, wasteForm: { product_id: '', product_name: '', quantity: '', reason: '腐烂', notes: '' } }); },
  closeWasteForm: function () { if (!this.data.wasteSubmitting) this.setData({ showWasteForm: false }); },
  // 弹窗内容区域阻止 tap 冒泡，避免点击输入框时触发遮罩关闭。
  stopMaskTap: function () {},
  pickWasteProduct: function (e) {
    var idx = e.currentTarget.dataset.index;
    var p = this.data.products[idx];
    this.setData({ 'wasteForm.product_id': p.product_id, 'wasteForm.product_name': p.sku_name || p.product_name });
  },
  onWasteField: function (e) { var f = e.currentTarget.dataset.field; var d = {}; d['wasteForm.' + f] = e.detail.value; this.setData(d); },
  pickWasteReason: function (e) { this.setData({ 'wasteForm.reason': e.currentTarget.dataset.reason }); },

  submitWaste: function () {
    var self = this, wf = this.data.wasteForm;
    if (this.data.wasteSubmitting) return;
    var quantity = Number(wf.quantity);
    if (!wf.product_id || !quantity || quantity <= 0) { wx.showToast({ title: '请选择商品和数量', icon: 'none' }); return; }
    var product = this.data.products.find(function (p) { return String(p.product_id) === String(wf.product_id); });
    if (!product) { wx.showToast({ title: '商品库存已变化，请重新选择', icon: 'none' }); this.loadProducts(); return; }
    if (quantity > Number(product.current_qty || 0)) { wx.showToast({ title: '报损数量不能超过当前库存', icon: 'none' }); return; }
    this.setData({ wasteSubmitting: true });
    app.request({ url: '/ops/waste', method: 'POST', data: { product_id: wf.product_id, sku_id: product.sku_id || null, quantity: quantity, unit: product.unit || '斤', reason: wf.reason, notes: wf.notes } })
      .then(function () {
        self.setData({ showWasteForm: false, wasteSubmitting: false });
        wx.showToast({ title: '已记录报损', icon: 'success' });
        self.loadWasteRecords(); self.loadProducts();
      })
      .catch(function (err) {
        self.setData({ wasteSubmitting: false });
        wx.showToast({ title: (err.body && err.body.detail) || '记录失败', icon: 'none' });
      });
  },

  // ── 临期 ──
  loadClearance: function () {
    var self = this;
    app.request({ url: '/ops/expiry/clearance?within_hours=' + this.data.clearanceHours }).then(function (data) {
      self.setData({ clearanceItems: (data && data.items) || [] });
    }).catch(function () { self.setData({ clearanceItems: [] }); wx.showToast({ title: '临期商品加载失败', icon: 'none' }); });
  },
  changeClearanceHours: function (e) { this.setData({ clearanceHours: Number(e.currentTarget.dataset.h) }); this.loadClearance(); },

  quickDiscount: function (e) {
    var item = this.data.clearanceItems[e.currentTarget.dataset.index];
    if (!item || this.data.submitting) return;
    var self = this;
    wx.showModal({
      title: '修改售价', editable: true,
      placeholderText: '输入新售价',
      content: item.suggested_price ? Number(item.suggested_price).toFixed(2) : '',
      success: function (res) {
        if (res.confirm && res.content) {
          var newPrice = parseFloat(res.content);
          if (isNaN(newPrice) || newPrice <= 0) { wx.showToast({ title: '请输入有效价格', icon: 'none' }); return; }
          self.setData({ submitting: true });
          app.request({
            url: '/ops/expiry/clearance/' + item.batch_id + '/promotion',
            method: 'POST',
            data: {
              promotion_price: newPrice,
              start_at: new Date().toISOString(),
              end_at: item.expiry_date,
            },
          }).then(function () {
            self.setData({ submitting: false });
            wx.showToast({ title: '已设置该批次促销 ¥' + newPrice.toFixed(2), icon: 'success' });
            self.loadClearance();
          }).catch(function () {
            self.setData({ submitting: false });
            wx.showToast({ title: '改价失败，请重试', icon: 'none' });
          });
        }
      },
    });
  },

  // ── 客户 ──
  loadCustomers: function () {
    var self = this;
    app.request({ url: '/ops/customers' }).then(function (data) {
      var list = data || [];
      self.setData({ customers: list, filteredCustomers: list });
    }).catch(function () { self.setData({ customers: [], filteredCustomers: [] }); wx.showToast({ title: '客户账款加载失败', icon: 'none' }); });
  },
  onCustomerSearch: function (e) {
    var kw = (e.detail.value || '').trim().toLowerCase();
    var filtered = kw ? this.data.customers.filter(function (c) {
      return (c.customer_name || '').toLowerCase().indexOf(kw) >= 0;
    }) : this.data.customers;
    this.setData({ customerSearch: e.detail.value, filteredCustomers: filtered });
  },

  viewCustomerLedger: function (e) {
    var name = e.currentTarget.dataset.name, self = this;
    wx.showLoading({ title: '加载中...' });
    app.request({ url: '/ops/customers/' + encodeURIComponent(name) + '/ledger' }).then(function (data) {
      wx.hideLoading();
      self.setData({ showCustomerLedger: true, customerLedger: data, ledgerName: name });
    }).catch(function (err) {
      wx.hideLoading();
      wx.showToast({ title: (err.body && err.body.detail) || '明细加载失败', icon: 'none' });
    });
  },
  closeLedger: function () { this.setData({ showCustomerLedger: false }); },

  openRepay: function (e) {
    this.setData({ showRepayForm: true, repayForm: { customer_name: e.currentTarget.dataset.name, amount: '' } });
  },
  closeRepay: function () { if (!this.data.repaySubmitting) this.setData({ showRepayForm: false }); },
  onRepayField: function (e) { var f = e.currentTarget.dataset.field; var d = {}; d['repayForm.' + f] = e.detail.value; this.setData(d); },
  submitRepay: function () {
    var self = this, rf = this.data.repayForm;
    if (this.data.repaySubmitting) return;
    var amount = Number(rf.amount);
    if (!isFinite(amount) || amount <= 0) { wx.showToast({ title: '请输入有效金额', icon: 'none' }); return; }
    var customer = (this.data.customers || []).find(function (c) { return c.customer_name === rf.customer_name; });
    if (customer && amount > Number(customer.balance || 0)) { wx.showToast({ title: '回款金额不能超过当前欠款', icon: 'none' }); return; }
    this.setData({ repaySubmitting: true });
    app.request({ url: '/ops/customers/repay', method: 'POST', data: { customer_name: rf.customer_name, amount: amount } })
      .then(function () {
        self.setData({ showRepayForm: false, repaySubmitting: false });
        wx.showToast({ title: '回款已记录', icon: 'success' }); self.loadCustomers();
      }).catch(function (err) {
        self.setData({ repaySubmitting: false });
        wx.showToast({ title: (err.body && err.body.detail) || '回款记录失败', icon: 'none' });
      });
  },

  // ── 导出 ──
  onExportDate: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var d = {}; d[f] = v; this.setData(d); },

  // 统一 CSV 生成与分享
  _exportCSV: function (type, rows) {
    if (!rows || rows.length === 0) { wx.showToast({ title: '暂无数据可导出', icon: 'none' }); return; }
    var headers = Object.keys(rows[0]);
    var csv = '\uFEFF' + headers.join(',') + '\n';
    rows.forEach(function (row) {
      csv += headers.map(function (h) {
        var v = row[h];
        return v != null ? '"' + String(v).replace(/"/g, '""') + '"' : '';
      }).join(',') + '\n';
    });
    var fs = wx.getFileSystemManager();
    var filePath = wx.env.USER_DATA_PATH + '/export_' + type + '_' + Date.now() + '.csv';
    fs.writeFile({
      filePath: filePath, data: csv, encoding: 'utf8',
      success: function () {
        wx.shareFileMessage({
          filePath: filePath, fileName: type + '.csv',
          success: function () { wx.showToast({ title: '已导出 ' + rows.length + ' 行', icon: 'success' }); },
          fail: function (err) { if (!(err && err.errMsg && err.errMsg.indexOf('cancel') >= 0)) wx.showToast({ title: '文件分享失败', icon: 'none' }); },
        });
      },
      fail: function () { wx.showToast({ title: '文件写入失败', icon: 'none' }); },
    });
  },

  doExport: function (e) {
    var type = e.currentTarget.dataset.type;
    var self = this;
    if (this.data.exporting) return;
    var now = new Date();
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    var today = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    var start = this.data.exportStart || today;
    var end = this.data.exportEnd || today;
    var datePattern = /^\d{4}-\d{2}-\d{2}$/;
    if (!datePattern.test(start) || !datePattern.test(end)) { wx.showToast({ title: '日期格式应为 YYYY-MM-DD', icon: 'none' }); return; }
    if (start > end) { wx.showToast({ title: '开始日期不能晚于结束日期', icon: 'none' }); return; }
    var url;

    // 往来账导出:用本地已加载的客户赊账数据生成 CSV(后端无独立端点)
    if (type === 'accounts') {
      var customers = this.data.customers;
      if (!customers.length) { wx.showToast({ title: '暂无客户数据', icon: 'none' }); return; }
      var rows = customers.map(function (c) {
        return {
          customer_name: c.customer_name || '',
          balance: c.balance || 0,
          is_overdue: c.is_overdue ? '是' : '否',
          overdue_days: c.overdue_days || 0,
          last_transaction: c.last_transaction || '',
        };
      });
      self._exportCSV(type, rows);
      return;
    }

    if (type === 'sales') {
      url = '/ops/export/sales?start_date=' + start + '&end_date=' + end;
    } else if (type === 'waste') {
      url = '/ops/export/waste?start_date=' + start + '&end_date=' + end;
    } else if (type === 'inventory') {
      url = '/ops/export/inventory';
    } else {
      wx.showToast({ title: '不支持的导出类型', icon: 'none' }); return;
    }

    this.setData({ exporting: true });
    wx.showLoading({ title: '生成中...' });
    app.request({ url: url }).then(function (data) {
      wx.hideLoading();
      self.setData({ exporting: false });
      var rows = Array.isArray(data) ? data : (data && data.rows ? data.rows : []);
      self._exportCSV(type, rows);
    }).catch(function (err) {
      wx.hideLoading();
      self.setData({ exporting: false });
      wx.showToast({ title: (err && err.body && (err.body.detail || err.body.message)) || '导出失败，请稍后重试', icon: 'none' });
    });
  },
});
