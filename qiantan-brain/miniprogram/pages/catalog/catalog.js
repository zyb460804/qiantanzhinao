/** 商品管理 — 主从布局：SKU列表 → 详情面板（别名/规格/供应商/单位换算） */
var app = getApp();

/** 保质期：后端存小时，前端按天/小时展示 */
function hoursToDisplay(h) {
  if (!h || h <= 0) return { value: '', unit: '小时' };
  if (h >= 24 && h % 24 === 0) return { value: String(h / 24), unit: '天' };
  if (h >= 24) return { value: String((h / 24).toFixed(1)), unit: '天' };
  return { value: String(h), unit: '小时' };
}

function displayToHours(value, unit) {
  var v = parseFloat(value) || 0;
  if (v <= 0) return 0;
  return unit === '天' ? Math.round(v * 24) : Math.round(v);
}

/** 保质期友好展示 */
function shelfLifeLabel(h) {
  if (!h || h <= 0) return '';
  if (h < 24) return h + '小时';
  var d = h / 24;
  if (d === Math.floor(d)) return d + '天';
  return d.toFixed(1) + '天';
}

Page({
  stopMaskTap: function () {},

  data: {
    skin: '', loading: false, submitting: false,

    // ── SKU 列表 ──
    skus: [], searchKeyword: '', filteredSkus: [], pricedCount: 0, unpricedCount: 0,

    // ── 详情面板 ──
    detailSku: null,
    detailAliases: [],
    detailSpecs: [],
    detailSupProds: [],

    // ── SKU 表单 ──
    showSkuForm: false, editSkuId: '',
    skuForm: { name: '', category_group: '', canonical_unit: '斤', shelf_life_value: '', shelf_life_unit: '天', default_sale_price: '' },

    // ── 别名表单 ──
    showAliasForm: false, aliasForm: { alias: '' },

    // ── 规格表单 ──
    showSpecForm: false, specForm: { name: '', price_delta: '0' },

    // ── 供应商关联表单 ──
    showSupProdForm: false, supProdForm: { supplier_id: '', last_price: '', min_order_qty: '' },
    allSuppliers: [],

    // ── 全局：单位换算 ──
    showUnitPanel: false,
    units: [], conversions: [],
    showUnitForm: false, unitForm: { code: '', name: '', kind: 'weight' },
    showConvForm: false, convForm: { from_unit: '', to_unit: '斤', factor: '', sku_id: '', _skuName: '' },

    // ── 全局：供应商管理 ──
    allSuppliers: [],

    // ── 供应商比价面板 ──
    showComparePanel: false,
    compareLoading: false,
    compareError: '',
    compareSort: 'value',
    compareData: { suppliers: [] },
    recommendData: null,
  },

  onShow: function () {
    this.setData({ skin: 'skin-' + app.resolveSkin() });
    this._loadSkus();
  },

  // ═══════════════════════════════════════════════════════════
  // SKU 列表
  // ═══════════════════════════════════════════════════════════

  _loadSkus: function () {
    var self = this;
    this.setData({ loading: true });
    app.request({ url: '/catalog/skus' }).then(function (data) {
      var decorated = (data || []).map(function (s) {
        s._shelfLabel = shelfLifeLabel(s.shelf_life_hours);
        s._initial = (s.name || '商').slice(0, 1);
        return s;
      });
      var pricedCount = decorated.filter(function (s) { return Number(s.default_sale_price) > 0; }).length;
      self.setData({
        skus: decorated,
        pricedCount: pricedCount,
        unpricedCount: decorated.length - pricedCount,
        loading: false
      });
      self._filterSkus();
    }).catch(function (err) { self.setData({ loading: false }); wx.showToast({ title: (err.body && err.body.detail) || '商品列表加载失败', icon: 'none' }); });
  },

  _filterSkus: function () {
    var kw = (this.data.searchKeyword || '').trim().toLowerCase();
    if (!kw) { this.setData({ filteredSkus: this.data.skus }); return; }
    var filtered = (this.data.skus || []).filter(function (s) {
      return (s.name || '').toLowerCase().indexOf(kw) >= 0 ||
        (s.category_group || '').toLowerCase().indexOf(kw) >= 0;
    });
    this.setData({ filteredSkus: filtered });
  },

  onSearchInput: function (e) {
    this.setData({ searchKeyword: e.detail.value });
    this._filterSkus();
  },
  onSearchClear: function () {
    this.setData({ searchKeyword: '', filteredSkus: this.data.skus });
  },

  // ═══════════════════════════════════════════════════════════
  // 打开 SKU 详情面板
  // ═══════════════════════════════════════════════════════════

  openDetail: function (e) {
    var skuId = e.currentTarget.dataset.id;
    var sku = this.data.skus.find(function (s) { return s.sku_id === skuId; });
    if (!sku) return;
    this.setData({ detailSku: sku, detailAliases: [], detailSpecs: [], detailSupProds: [] });
    this._loadDetailAliases(skuId);
    this._loadDetailSpecs(skuId);
    this._loadDetailSuppliers(skuId);
  },

  closeDetail: function () {
    this.setData({ detailSku: null });
  },

  // ── 详情：别名 ──

  _loadDetailAliases: function (skuId) {
    var self = this;
    app.request({ url: '/catalog/skus/' + skuId + '/aliases' }).then(function (data) {
      self.setData({ detailAliases: data || [] });
    }).catch(function () { wx.showToast({ title: '别名加载失败', icon: 'none' }); });
  },

  openAliasForm: function () {
    this.setData({ showAliasForm: true, aliasForm: { alias: '' } });
  },
  closeAliasForm: function () { this.setData({ showAliasForm: false }); },
  onAliasField: function (e) {
    var f = this.data.aliasForm; f.alias = e.detail.value; this.setData({ aliasForm: f });
  },
  saveAlias: function () {
    var self = this, alias = this.data.aliasForm.alias.trim(), skuId = this.data.detailSku.sku_id;
    if (!alias) { wx.showToast({ title: '别名不能为空', icon: 'none' }); return; }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    app.request({ url: '/catalog/skus/' + skuId + '/aliases', method: 'POST', data: { alias: alias } }).then(function () {
      self.setData({ showAliasForm: false, submitting: false });
      wx.showToast({ title: '已添加', icon: 'success' });
      self._loadDetailAliases(skuId);
    }).catch(function () { self.setData({ submitting: false }); wx.showToast({ title: '别名添加失败', icon: 'none' }); });
  },
  removeAlias: function (e) {
    var id = e.currentTarget.dataset.id, self = this, skuId = this.data.detailSku.sku_id;
    app.request({ url: '/catalog/aliases/' + id, method: 'DELETE' }).then(function () {
      self._loadDetailAliases(skuId);
    }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
  },

  // ── 详情：规格 ──

  _loadDetailSpecs: function (skuId) {
    var self = this;
    app.request({ url: '/catalog/skus/' + skuId + '/specs' }).then(function (data) {
      self.setData({ detailSpecs: data || [] });
    }).catch(function () { wx.showToast({ title: '规格加载失败', icon: 'none' }); });
  },

  openSpecForm: function () {
    this.setData({ showSpecForm: true, specForm: { name: '', price_delta: '0' } });
  },
  closeSpecForm: function () { this.setData({ showSpecForm: false }); },
  onSpecField: function (e) {
    var f = e.currentTarget.dataset.field;
    var v = e.detail.value;
    var form = this.data.specForm; form[f] = v; this.setData({ specForm: form });
  },
  saveSpec: function () {
    var self = this, f = this.data.specForm, skuId = this.data.detailSku.sku_id;
    if (!f.name.trim()) { wx.showToast({ title: '规格名不能为空', icon: 'none' }); return; }
    var delta = Number(f.price_delta);
    if (!isFinite(delta)) { wx.showToast({ title: '价格增量必须是数字', icon: 'none' }); return; }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    app.request({ url: '/catalog/skus/' + skuId + '/specs', method: 'POST', data: { name: f.name.trim(), price_delta: delta } }).then(function () {
      self.setData({ showSpecForm: false, submitting: false });
      wx.showToast({ title: '已添加', icon: 'success' });
      self._loadDetailSpecs(skuId);
    }).catch(function () { self.setData({ submitting: false }); wx.showToast({ title: '规格添加失败', icon: 'none' }); });
  },
  removeSpec: function (e) {
    var id = e.currentTarget.dataset.id, self = this, skuId = this.data.detailSku.sku_id;
    app.request({ url: '/catalog/specs/' + id, method: 'DELETE' }).then(function () {
      self._loadDetailSpecs(skuId);
    }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
  },

  // ── 详情：供应商关联 ──

  _loadDetailSuppliers: function (skuId) {
    var self = this;
    app.request({ url: '/catalog/skus/' + skuId + '/suppliers' }).then(function (data) {
      self.setData({ detailSupProds: data || [] });
    }).catch(function () { wx.showToast({ title: '供应商关联加载失败', icon: 'none' }); });
  },

  openSupProdForm: function () {
    var self = this;
    this.setData({ showSupProdForm: true, supProdForm: { supplier_id: '', last_price: '', min_order_qty: '' } });
    app.request({ url: '/catalog/suppliers', data: { limit: 200 } }).then(function (data) {
      self.setData({ allSuppliers: (data && data.items) || [] });
    }).catch(function () { wx.showToast({ title: '供应商列表加载失败', icon: 'none' }); });
  },
  closeSupProdForm: function () { this.setData({ showSupProdForm: false }); },
  onSupProdField: function (e) {
    var f = e.currentTarget.dataset.field;
    var v = e.detail.value;
    var form = this.data.supProdForm; form[f] = v; this.setData({ supProdForm: form });
  },
  pickSupProdSupplier: function (e) {
    var form = this.data.supProdForm;
    form.supplier_id = e.currentTarget.dataset.id;
    this.setData({ supProdForm: form });
  },
  saveSupProd: function () {
    var self = this, f = this.data.supProdForm, skuId = this.data.detailSku.sku_id;
    if (!f.supplier_id) { wx.showToast({ title: '请选择供应商', icon: 'none' }); return; }
    var lastPrice = f.last_price === '' ? null : Number(f.last_price);
    var minQty = f.min_order_qty === '' ? null : Number(f.min_order_qty);
    if ((lastPrice !== null && (!isFinite(lastPrice) || lastPrice < 0)) || (minQty !== null && (!isFinite(minQty) || minQty <= 0))) { wx.showToast({ title: '价格不能为负，起订量必须大于0', icon: 'none' }); return; }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    var payload = {
      sku_id: skuId,
      last_price: lastPrice,
      min_order_qty: minQty,
    };
    app.request({ url: '/catalog/suppliers/' + f.supplier_id + '/products', method: 'POST', data: payload }).then(function () {
      self.setData({ showSupProdForm: false, submitting: false });
      wx.showToast({ title: '已关联', icon: 'success' });
      self._loadDetailSuppliers(skuId);
    }).catch(function () { self.setData({ submitting: false }); wx.showToast({ title: '关联失败', icon: 'none' }); });
  },
  removeSupProd: function (e) {
    var id = e.currentTarget.dataset.id, self = this, skuId = this.data.detailSku.sku_id;
    wx.showModal({ title: '解除关联', content: '确认解除该供应商与此商品的关联？', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/catalog/supplier-products/' + id, method: 'DELETE' }).then(function () {
        self._loadDetailSuppliers(skuId);
      }).catch(function () { wx.showToast({ title: '解除失败', icon: 'none' }); });
    }});
  },

  // ═══════════════════════════════════════════════════════════
  // SKU 增/改/停用
  // ═══════════════════════════════════════════════════════════

  openSkuForm: function (e) {
    var id = e ? e.currentTarget.dataset.id : '';
    var s = id ? this.data.skus.find(function (x) { return x.sku_id === id; }) : null;
    var lifeDisplay = s ? hoursToDisplay(s.shelf_life_hours) : { value: '3', unit: '天' };
    this.setData({
      showSkuForm: true, editSkuId: s ? s.sku_id : '',
      skuForm: s ? {
        name: s.name, category_group: s.category_group || '',
        canonical_unit: s.canonical_unit || '斤',
        shelf_life_value: lifeDisplay.value,
        shelf_life_unit: lifeDisplay.unit,
        default_sale_price: s.default_sale_price ? String(s.default_sale_price) : '',
      } : {
        name: '', category_group: '', canonical_unit: '斤',
        shelf_life_value: '3', shelf_life_unit: '天', default_sale_price: '',
      }
    });
  },
  closeSkuForm: function () { this.setData({ showSkuForm: false }); },
  onSkuField: function (e) {
    var f = e.currentTarget.dataset.field;
    var form = this.data.skuForm; form[f] = e.detail.value; this.setData({ skuForm: form });
  },
  /** 切换保质期单位 */
  switchLifeUnit: function (e) {
    var unit = e.currentTarget.dataset.unit;
    var form = this.data.skuForm; form.shelf_life_unit = unit; this.setData({ skuForm: form });
  },
  saveSku: function () {
    var self = this, f = this.data.skuForm;
    if (!f.name.trim()) { wx.showToast({ title: '名称不能为空', icon: 'none' }); return; }
    var salePrice = f.default_sale_price === '' ? null : Number(f.default_sale_price);
    var shelfValue = f.shelf_life_value === '' ? null : Number(f.shelf_life_value);
    if (shelfValue !== null && (!isFinite(shelfValue) || shelfValue <= 0)) { wx.showToast({ title: '保质期必须大于0', icon: 'none' }); return; }
    if (salePrice !== null && (!isFinite(salePrice) || salePrice < 0)) { wx.showToast({ title: '售价不能为负数', icon: 'none' }); return; }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    var payload = {
      name: f.name.trim(), category_group: f.category_group,
      canonical_unit: f.canonical_unit,
      shelf_life_hours: shelfValue === null ? 0 : displayToHours(shelfValue, f.shelf_life_unit),
      default_sale_price: salePrice,
    };
    var req = this.data.editSkuId
      ? app.request({ url: '/catalog/skus/' + this.data.editSkuId, method: 'PUT', data: payload })
      : app.request({ url: '/catalog/skus', method: 'POST', data: payload });
    req.then(function () {
      self.setData({ showSkuForm: false, submitting: false });
      wx.showToast({ title: '已保存', icon: 'success' });
      self._loadSkus();
      if (self.data.detailSku && self.data.editSkuId === self.data.detailSku.sku_id) {
        var updated = Object.assign({}, self.data.detailSku, payload, { name: f.name.trim(), _shelfLabel: shelfLifeLabel(payload.shelf_life_hours) });
        self.setData({ detailSku: updated });
      }
    }).catch(function (err) {
      self.setData({ submitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '保存失败', icon: 'none' });
    });
  },
  deleteSku: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '停用商品', content: '确认停用该SKU？停用后不会出现在销售和采购列表中。', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/catalog/skus/' + id, method: 'DELETE' }).then(function () {
        wx.showToast({ title: '已停用', icon: 'none' });
        if (self.data.detailSku && self.data.detailSku.sku_id === id) {
          self.setData({ detailSku: null });
        }
        self._loadSkus();
      }).catch(function () { wx.showToast({ title: '停用失败', icon: 'none' }); });
    }});
  },

  // ═══════════════════════════════════════════════════════════
  // 全局：单位管理
  // ═══════════════════════════════════════════════════════════

  openUnitPanel: function () {
    this.setData({ showUnitPanel: true });
    this._loadUnits();
    this._loadConversions();
  },
  closeUnitPanel: function () { this.setData({ showUnitPanel: false }); },

  _loadUnits: function () {
    var self = this;
    app.request({ url: '/catalog/units' }).then(function (data) {
      self.setData({ units: data || [] });
    }).catch(function () { wx.showToast({ title: '单位列表加载失败', icon: 'none' }); });
  },
  _loadConversions: function () {
    var self = this;
    app.request({ url: '/catalog/unit-conversions' }).then(function (data) {
      // Decorate conversions with SKU names
      var convs = (data || []).map(function (c) {
        if (c.sku_id) {
          var sku = (self.data.skus || []).find(function (s) { return s.sku_id === c.sku_id; });
          c._skuName = sku ? sku.name : c.sku_id;
        }
        return c;
      });
      self.setData({ conversions: convs });
    }).catch(function () { wx.showToast({ title: '单位换算加载失败', icon: 'none' }); });
  },

  openUnitForm: function () {
    this.setData({ showUnitForm: true, unitForm: { code: '', name: '', kind: 'weight' } });
  },
  closeUnitForm: function () { this.setData({ showUnitForm: false }); },
  onUnitField: function (e) {
    var f = e.currentTarget.dataset.field;
    var form = this.data.unitForm; form[f] = e.detail.value; this.setData({ unitForm: form });
  },
  pickUnitKind: function (e) {
    var form = this.data.unitForm;
    form.kind = e.currentTarget.dataset.kind;
    this.setData({ unitForm: form });
  },
  saveUnit: function () {
    var self = this, f = this.data.unitForm;
    if (!f.code.trim()) { wx.showToast({ title: '代码不能为空', icon: 'none' }); return; }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    app.request({ url: '/catalog/units', method: 'POST', data: f }).then(function () {
      self.setData({ showUnitForm: false, submitting: false });
      wx.showToast({ title: '已添加', icon: 'success' });
      self._loadUnits();
    }).catch(function () { self.setData({ submitting: false }); wx.showToast({ title: '单位添加失败', icon: 'none' }); });
  },

  openConvForm: function () {
    this.setData({
      showConvForm: true,
      convForm: { from_unit: '', to_unit: '斤', factor: '', sku_id: '', _skuName: '' }
    });
  },
  closeConvForm: function () { this.setData({ showConvForm: false }); },
  onConvField: function (e) {
    var f = e.currentTarget.dataset.field;
    var form = this.data.convForm; form[f] = e.detail.value; this.setData({ convForm: form });
  },
  pickConvSku: function (e) {
    var form = this.data.convForm;
    form.sku_id = e.currentTarget.dataset.id;
    // 同时存储名称用于显示，避免在表单中显示UUID
    var sku = (this.data.skus || []).find(function (s) { return s.sku_id === form.sku_id; });
    form._skuName = sku ? sku.name : form.sku_id;
    this.setData({ convForm: form });
  },
  clearConvSku: function () {
    var form = this.data.convForm;
    form.sku_id = '';
    form._skuName = '';
    this.setData({ convForm: form });
  },
  saveConv: function () {
    var self = this, f = this.data.convForm;
    var factor = Number(f.factor);
    if (!f.from_unit || !f.to_unit || !isFinite(factor) || factor <= 0) { wx.showToast({ title: '请填写单位，换算系数必须大于0', icon: 'none' }); return; }
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    var payload = { from_unit: f.from_unit, to_unit: f.to_unit, factor: factor };
    if (f.sku_id) payload.sku_id = f.sku_id;
    app.request({ url: '/catalog/unit-conversions', method: 'POST', data: payload }).then(function () {
      self.setData({ showConvForm: false, submitting: false });
      wx.showToast({ title: '已添加', icon: 'success' });
      self._loadConversions();
    }).catch(function () { self.setData({ submitting: false }); wx.showToast({ title: '换算添加失败', icon: 'none' }); });
  },
  deleteConv: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/catalog/unit-conversions/' + id, method: 'DELETE' }).then(function () {
      self._loadConversions();
    }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
  },

  // ═══════════════════════════════════════════════════════════
  // 供应商比价分析
  // ═══════════════════════════════════════════════════════════

  openComparePanel: function () {
    var self = this, skuId = this.data.detailSku.sku_id;
    this.setData({ showComparePanel: true, compareLoading: true, compareError: '' });

    // Load comparison data and recommendation in parallel
    var comparePromise = app.request({
      url: '/catalog/suppliers/compare?sku_id=' + skuId + '&sort_by=' + self.data.compareSort,
    });
    var recommendPromise = app.request({
      url: '/catalog/suppliers/recommend?sku_id=' + skuId,
      method: 'POST',
    });

    Promise.all([comparePromise, recommendPromise]).then(function (results) {
      self.setData({
        compareData: results[0] || { suppliers: [] },
        recommendData: results[1] || null,
        compareLoading: false,
      });
    }).catch(function (err) {
      self.setData({
        compareError: (err.body && err.body.detail) || '比价数据加载失败',
        compareLoading: false,
      });
    });
  },

  closeComparePanel: function () {
    this.setData({ showComparePanel: false });
  },

  switchCompareSort: function (e) {
    var sort = e.currentTarget.dataset.sort;
    var self = this;
    this.setData({ compareSort: sort, compareLoading: true });
    var skuId = this.data.detailSku.sku_id;
    app.request({
      url: '/catalog/suppliers/compare?sku_id=' + skuId + '&sort_by=' + sort,
    }).then(function (data) {
      self.setData({ compareData: data || { suppliers: [] }, compareLoading: false });
    }).catch(function (err) {
      self.setData({
        compareError: (err.body && err.body.detail) || '排序切换失败',
        compareLoading: false,
      });
    });
  },

  // ═══════════════════════════════════════════════════════════
  // 全局：供应商管理（已迁移至 pages/supplier/ 专用页面）
  // ═══════════════════════════════════════════════════════════

  goSupplier: function () {
    wx.navigateTo({ url: '/pages/supplier/supplier' });
  },

  viewStatement: function (e) {
    var id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/purchase/purchase?statement=' + id });
  },
});
