/** 供应商管理 — 档案·评分·关联商品·黑名单 */
var app = getApp();

function money(v) { return Math.round(Number(v || 0) * 100) / 100; }

/** 评分颜色: 80+ 绿, 60-80 黄, <60 红 */
function scoreColor(score) {
  if (score == null) return 'muted';
  if (score >= 80) return 'green';
  if (score >= 60) return 'corn';
  return 'tomato';
}

/** 评分标签 */
function scoreLabel(score) {
  if (score == null) return '暂无';
  if (score >= 90) return '优秀';
  if (score >= 80) return '良好';
  if (score >= 60) return '一般';
  return '较差';
}

Page({
  stopMaskTap: function () {},

  data: {
    skinClass: '', loading: true, loadingMore: false, submitting: false,

    // 供应商列表（分页）
    suppliers: [], searchKeyword: '',
    supplierOffset: 0, supplierLimit: 20, supplierTotal: 0,

    // 统计
    totalCount: 0, avgScore: 0, blacklistedCount: 0,

    // 展开的供应商详情
    expandedId: '', expandedDetail: null, expandedProducts: [],

    // 新增/编辑表单
    showForm: false, editId: '', form: {},
    formErrors: {},

    // 商品关联表单
    showProdForm: false, prodForm: { supplier_id: '', sku_id: '', last_price: '', min_order_qty: '' },
    prodFormSuppliers: [], allSkus: [], skuSearchKeyword: '', filteredSkus: [],

    // 评分中
    scoringId: '',

    // 黑名单确认
    showBlacklistConfirm: false, blacklistTarget: null,
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadSuppliers(false);
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadSuppliers(false).then(function () { wx.stopPullDownRefresh(); })
      .catch(function () { wx.stopPullDownRefresh(); });
  },

  onReachBottom: function () {
    if (this.data.suppliers.length < this.data.supplierTotal) {
      this.loadMore();
    }
  },

  // ── 数据加载（分页）──────────────────────────────────

  loadSuppliers: function (append) {
    var self = this;
    var offset = append ? this.data.supplierOffset : 0;
    if (!append) {
      this.setData({ loading: true, suppliers: [], supplierOffset: 0 });
    } else {
      this.setData({ loadingMore: true });
    }
    var params = { offset: offset, limit: this.data.supplierLimit };
    var kw = (this.data.searchKeyword || '').trim();
    if (kw) params.keyword = kw;
    return app.request({ url: '/catalog/suppliers', data: params }).then(function (data) {
      var d = data || {};
      var items = d.items || [];
      var list = items.map(function (s) {
        return {
          supplier_id: s.supplier_id,
          name: s.name,
          contact: s.contact || '',
          address: s.address || '',
          business_category: s.business_category || '',
          min_order_qty: s.min_order_qty,
          lead_time_hours: s.lead_time_hours,
          default_credit_days: s.default_credit_days,
          is_blacklisted: s.is_blacklisted,
          composite_score: s.composite_score,
          shortage_rate: s.shortage_rate,
          return_rate: s.return_rate,
          quality_issue_rate: s.quality_issue_rate,
          on_time_rate: s.on_time_rate,
          total_orders: s.total_orders || 0,
          current_balance: money(s.current_balance),
          _scoreColor: scoreColor(s.composite_score),
          _scoreLabel: scoreLabel(s.composite_score),
          _leadTimeDisplay: s.lead_time_hours ? (s.lead_time_hours >= 24 ? (s.lead_time_hours / 24).toFixed(1) + '天' : s.lead_time_hours + '小时') : '',
          _creditDisplay: s.default_credit_days ? s.default_credit_days + '天' : '现结',
        };
      });
      var merged = append ? self.data.suppliers.concat(list) : list;
      var totalCount = d.total || 0;
      // 黑名单数取后端全量统计，避免分页只数到部分
      var blacklistedCount = d.blacklisted_count || 0;
      var scoredList = merged.filter(function (s) { return s.composite_score != null; });
      var avgScore = scoredList.length > 0
        ? Math.round(scoredList.reduce(function (sum, s) { return sum + s.composite_score; }, 0) / scoredList.length)
        : 0;

      self.setData({
        suppliers: merged, supplierTotal: totalCount,
        supplierOffset: offset + items.length,
        totalCount: totalCount, avgScore: avgScore,
        blacklistedCount: blacklistedCount,
        loading: false, loadingMore: false
      });
    }).catch(function () {
      self.setData({ loading: false, loadingMore: false });
      if (!append) wx.showToast({ title: '供应商列表加载失败', icon: 'none' });
    });
  },

  loadMore: function () {
    if (this.data.loadingMore) return;
    this.loadSuppliers(true);
  },

  // ── 搜索（服务端搜索，重置分页）─────────────────────

  onSearchInput: function (e) {
    this.setData({ searchKeyword: e.detail.value });
    this.loadSuppliers(false);
  },

  onSearchClear: function () {
    this.setData({ searchKeyword: '' });
    this.loadSuppliers(false);
  },

  // ── 展开/折叠 ────────────────────────────────────────

  toggleExpand: function (e) {
    var id = e.currentTarget.dataset.id;
    var current = this.data.expandedId;
    if (current === id) {
      this.setData({ expandedId: '', expandedProducts: [], expandedDetail: null });
    } else {
      this.setData({ expandedId: id, expandedProducts: [], expandedDetail: null });
      this._loadSupplierDetail(id);
      this._loadSupplierProducts(id);
    }
  },

  _loadSupplierDetail: function (supplierId) {
    var self = this;
    app.request({ url: '/catalog/suppliers/' + supplierId }).then(function (data) {
      var d = data || {};
      self.setData({ expandedDetail: {
        composite_score: d.composite_score,
        shortage_rate: d.shortage_rate,
        return_rate: d.return_rate,
        quality_issue_rate: d.quality_issue_rate,
        on_time_rate: d.on_time_rate,
        total_orders: d.total_orders || 0,
        current_balance: money(d.current_balance),
      }});
    }).catch(function () {});
  },

  _loadSupplierProducts: function (supplierId) {
    var self = this;
    app.request({ url: '/catalog/suppliers/' + supplierId + '/products' }).then(function (data) {
      self.setData({ expandedProducts: data || [] });
    }).catch(function () {});
  },

  // ── 新增/编辑表单 ────────────────────────────────────

  openForm: function (e) {
    var id = e ? e.currentTarget.dataset.id : '';
    var s = id ? this.data.suppliers.find(function (x) { return x.supplier_id === id; }) : null;
    this.setData({
      showForm: true, editId: s ? s.supplier_id : '',
      form: s ? {
        name: s.name, contact: s.contact || '',
        address: s.address || '',
        business_category: s.business_category || '',
        min_order_qty: s.min_order_qty ? String(s.min_order_qty) : '',
        lead_time_hours: s.lead_time_hours ? String(s.lead_time_hours) : '',
        default_credit_days: s.default_credit_days ? String(s.default_credit_days) : '',
      } : {
        name: '', contact: '', address: '',
        business_category: '', min_order_qty: '',
        lead_time_hours: '', default_credit_days: '',
      },
      formErrors: {},
    });
  },

  closeForm: function () {
    this.setData({ showForm: false, formErrors: {} });
  },

  onFormField: function (e) {
    var f = e.currentTarget.dataset.field;
    var form = this.data.form;
    form[f] = e.detail.value;
    this.setData({ form: form });
    // 清除对应字段的错误
    if (this.data.formErrors[f]) {
      var errors = this.data.formErrors;
      delete errors[f];
      this.setData({ formErrors: errors });
    }
  },

  saveSupplier: function () {
    var self = this, f = this.data.form;
    var errors = {};

    if (!f.name || !f.name.trim()) {
      errors.name = '供应商名称不能为空';
    }

    var leadTime = f.lead_time_hours === '' ? null : Number(f.lead_time_hours);
    if (leadTime !== null && (!isFinite(leadTime) || leadTime < 0)) {
      errors.lead_time_hours = '供货周期不能为负数';
    }

    var creditDays = f.default_credit_days === '' ? null : Number(f.default_credit_days);
    if (creditDays !== null && (!isFinite(creditDays) || creditDays < 0)) {
      errors.default_credit_days = '账期不能为负数';
    }

    var minQty = f.min_order_qty === '' ? null : Number(f.min_order_qty);
    if (minQty !== null && (!isFinite(minQty) || minQty < 0)) {
      errors.min_order_qty = '起订量不能为负数';
    }

    if (Object.keys(errors).length > 0) {
      this.setData({ formErrors: errors });
      return;
    }

    var payload = {
      name: f.name.trim(),
      contact: (f.contact || '').trim() || null,
      address: (f.address || '').trim() || null,
      business_category: (f.business_category || '').trim() || null,
      min_order_qty: minQty,
      lead_time_hours: leadTime,
      default_credit_days: creditDays,
    };

    this.setData({ submitting: true });

    var req = this.data.editId
      ? app.request({ url: '/catalog/suppliers/' + this.data.editId, method: 'PUT', data: payload })
      : app.request({ url: '/catalog/suppliers', method: 'POST', data: payload });

    req.then(function () {
      self.setData({ submitting: false, showForm: false });
      wx.showToast({ title: self.data.editId ? '供应商已更新' : '供应商已创建', icon: 'success' });
      self.loadSuppliers();
    }).catch(function (err) {
      self.setData({ submitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '保存失败', icon: 'none' });
    });
  },

  // ── 停用/删除 ────────────────────────────────────────

  deactivateSupplier: function (e) {
    var id = e.currentTarget.dataset.id;
    var name = e.currentTarget.dataset.name;
    var self = this;
    wx.showModal({
      title: '停用供应商',
      content: '确认停用「' + name + '」？停用后不会出现在采购等选择列表中。',
      confirmText: '确认停用',
      confirmColor: '#d9524a',
      success: function (r) {
        if (!r.confirm) return;
        app.request({ url: '/catalog/suppliers/' + id, method: 'DELETE' }).then(function () {
          wx.showToast({ title: '已停用', icon: 'none' });
          self.setData({ expandedId: '' });
          self.loadSuppliers();
        }).catch(function () {
          wx.showToast({ title: '停用失败', icon: 'none' });
        });
      }
    });
  },

  // ── 黑名单 ────────────────────────────────────────────

  toggleBlacklist: function (e) {
    var id = e.currentTarget.dataset.id;
    var s = this.data.suppliers.find(function (x) { return x.supplier_id === id; });
    if (!s) return;
    this.setData({
      showBlacklistConfirm: true,
      blacklistTarget: { id: id, name: s.name, is_blacklisted: s.is_blacklisted }
    });
  },

  confirmBlacklist: function () {
    var t = this.data.blacklistTarget;
    if (!t) return;
    var self = this;
    var newVal = !t.is_blacklisted;
    app.request({
      url: '/catalog/suppliers/' + t.id, method: 'PUT',
      data: { is_blacklisted: newVal }
    }).then(function () {
      self.setData({ showBlacklistConfirm: false, blacklistTarget: null });
      wx.showToast({ title: newVal ? '已加入黑名单' : '已移出黑名单', icon: 'success' });
      self.loadSuppliers();
    }).catch(function () {
      self.setData({ showBlacklistConfirm: false, blacklistTarget: null });
      wx.showToast({ title: '操作失败', icon: 'none' });
    });
  },

  cancelBlacklist: function () {
    this.setData({ showBlacklistConfirm: false, blacklistTarget: null });
  },

  // ── 评分 ──────────────────────────────────────────────

  recalculateScore: function (e) {
    var id = e.currentTarget.dataset.id;
    var self = this;
    this.setData({ scoringId: id });
    app.request({ url: '/catalog/suppliers/' + id + '/recalculate-score', method: 'POST', data: {} }).then(function (data) {
      self.setData({ scoringId: '' });
      var score = data && data.composite_score;
      wx.showToast({
        title: score != null ? '综合评分 ' + score.toFixed(1) : '暂无采购记录',
        icon: score != null ? 'success' : 'none'
      });
      self.loadSuppliers();
      // refresh expanded detail
      if (self.data.expandedId === id) {
        self._loadSupplierDetail(id);
      }
    }).catch(function (err) {
      self.setData({ scoringId: '' });
      wx.showToast({ title: (err.body && err.body.detail) || '评分失败', icon: 'none' });
    });
  },

  // ── 商品关联 ──────────────────────────────────────────

  openProdForm: function (e) {
    var supplierId = e ? e.currentTarget.dataset.id : this.data.expandedId;
    var self = this;
    this.setData({
      showProdForm: true,
      prodForm: { supplier_id: supplierId, sku_id: '', last_price: '', min_order_qty: '' },
      skuSearchKeyword: '', filteredSkus: [],
    });
    // 加载全部SKU
    app.request({ url: '/catalog/skus' }).then(function (data) {
      var skus = (data || []).map(function (s) {
        return { sku_id: s.sku_id, name: s.name, _initial: (s.name || '商').slice(0, 1) };
      });
      self.setData({ allSkus: skus, filteredSkus: skus });
    }).catch(function () {});
  },

  closeProdForm: function () {
    this.setData({ showProdForm: false });
  },

  onProdField: function (e) {
    var f = e.currentTarget.dataset.field;
    var form = this.data.prodForm;
    form[f] = e.detail.value;
    this.setData({ prodForm: form });
  },

  onSkuSearch: function (e) {
    var kw = (e.detail.value || '').trim().toLowerCase();
    this.setData({ skuSearchKeyword: kw });
    if (!kw) {
      this.setData({ filteredSkus: this.data.allSkus });
      return;
    }
    var filtered = (this.data.allSkus || []).filter(function (s) {
      return (s.name || '').toLowerCase().indexOf(kw) >= 0;
    });
    this.setData({ filteredSkus: filtered });
  },

  pickSku: function (e) {
    var form = this.data.prodForm;
    form.sku_id = e.currentTarget.dataset.id;
    form._skuName = e.currentTarget.dataset.name;
    this.setData({ prodForm: form });
  },

  saveProd: function () {
    var self = this, f = this.data.prodForm;
    if (!f.supplier_id) { wx.showToast({ title: '供应商ID缺失', icon: 'none' }); return; }
    if (!f.sku_id) { wx.showToast({ title: '请选择商品', icon: 'none' }); return; }
    var lastPrice = f.last_price === '' ? null : Number(f.last_price);
    var minQty = f.min_order_qty === '' ? null : Number(f.min_order_qty);
    if ((lastPrice !== null && (!isFinite(lastPrice) || lastPrice < 0)) ||
        (minQty !== null && (!isFinite(minQty) || minQty <= 0))) {
      wx.showToast({ title: '价格不能为负，起订量必须大于0', icon: 'none' }); return;
    }
    var payload = {
      sku_id: f.sku_id,
      last_price: lastPrice,
      min_order_qty: minQty,
    };
    app.request({
      url: '/catalog/suppliers/' + f.supplier_id + '/products', method: 'POST', data: payload
    }).then(function () {
      self.setData({ showProdForm: false });
      wx.showToast({ title: '已关联', icon: 'success' });
      self._loadSupplierProducts(f.supplier_id);
    }).catch(function () {
      wx.showToast({ title: '关联失败', icon: 'none' });
    });
  },

  removeProd: function (e) {
    var id = e.currentTarget.dataset.id;
    var supplierId = this.data.expandedId;
    var self = this;
    wx.showModal({
      title: '解除关联',
      content: '确认解除该商品与供应商的关联？',
      success: function (r) {
        if (!r.confirm) return;
        app.request({ url: '/catalog/supplier-products/' + id, method: 'DELETE' }).then(function () {
          self._loadSupplierProducts(supplierId);
        }).catch(function () {
          wx.showToast({ title: '解除失败', icon: 'none' });
        });
      }
    });
  },

  // ── 导航 ──────────────────────────────────────────────

  viewStatement: function (e) {
    var id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/purchase/purchase?statement=' + id });
  },

  goPurchase: function () {
    wx.navigateTo({ url: '/pages/purchase/purchase' });
  },

  goHome: function () {
    wx.switchTab({ url: '/pages/index/index' });
  },
});