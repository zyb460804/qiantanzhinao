/** 商品管理 — SKU/别名/规格/单位/供应商 */
var app = getApp();

Page({
  data: {
    skin: '', tab: 'skus', loading: false,
    searchKeyword: '', filteredSkus: [],
    // SKUs
    skus: [], showSkuForm: false, skuForm: { name: '', category_group: '', canonical_unit: '斤', shelf_life_hours: 72, default_sale_price: '' }, editSkuId: '',
    // Aliases
    aliases: [], showAliasForm: false, aliasForm: { alias: '' }, aliasSkuId: '',
    // Specs
    specs: [], showSpecForm: false, specForm: { name: '', price_delta: '0' }, specSkuId: '',
    // Units
    units: [], conversions: [], showUnitForm: false, unitForm: { code: '', name: '', kind: 'weight' },
    showConvForm: false, convForm: { from_unit: '', to_unit: '斤', factor: '', sku_id: '' },
    // Suppliers
    suppliers: [], showSupForm: false, supForm: { name: '', contact: '', lead_time_hours: '' }, editSupId: '',
    supProducts: [], showSupProdForm: false, supProdForm: { sku_id: '', last_price: '' }, supProdSupId: '',
  },

  onShow: function () { this.setData({ skin: 'skin-' + app.resolveSkin() }); this.loadAll(); },

  // ── Tab ──
  switchTab: function (e) { this.setData({ tab: e.currentTarget.dataset.tab }); this.loadAll(); },

  loadAll: function () {
    var t = this.data.tab;
    if (t === 'skus') this.loadSkus();
    else if (t === 'aliases') this.loadSkus();
    else if (t === 'specs') this.loadSkus();
    else if (t === 'units') { this.loadUnits(); this.loadConversions(); }
    else if (t === 'suppliers') this.loadSuppliers();
  },

  // ── SKU ──
  loadSkus: function () {
    var self = this;
    this.setData({ loading: true });
    app.request({ url: '/catalog/skus' }).then(function (data) { self.setData({ skus: data || [], loading: false }); self._filterSkus(); }).catch(function () { self.setData({ loading: false }); });
  },

  _filterSkus: function () {
    var kw = (this.data.searchKeyword || '').trim().toLowerCase();
    if (!kw) { this.setData({ filteredSkus: this.data.skus }); return; }
    var filtered = (this.data.skus || []).filter(function (s) { return (s.name || '').toLowerCase().indexOf(kw) >= 0; });
    this.setData({ filteredSkus: filtered });
  },

  onSearchInput: function (e) { this.setData({ searchKeyword: e.detail.value }, this._filterSkus); },

  openSkuForm: function (e) { var s = e ? this.data.skus.find(function (x) { return x.sku_id === e.currentTarget.dataset.id; }) : null; this.setData({ showSkuForm: true, editSkuId: s ? s.sku_id : '', skuForm: s ? { name: s.name, category_group: s.category_group || '', canonical_unit: s.canonical_unit || '斤', shelf_life_hours: s.shelf_life_hours || 72, default_sale_price: s.default_sale_price ? String(s.default_sale_price) : '' } : { name: '', category_group: '', canonical_unit: '斤', shelf_life_hours: 72, default_sale_price: '' } }); },
  closeSkuForm: function () { this.setData({ showSkuForm: false }); },
  onSkuField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var form = this.data.skuForm; form[f] = v; this.setData({ skuForm: form }); },
  saveSku: function () {
    var self = this, f = this.data.skuForm;
    if (!f.name.trim()) { wx.showToast({ title: '名称不能为空', icon: 'none' }); return; }
    var payload = { name: f.name.trim(), category_group: f.category_group, canonical_unit: f.canonical_unit, shelf_life_hours: Number(f.shelf_life_hours), default_sale_price: f.default_sale_price ? Number(f.default_sale_price) : null };
    var req = this.data.editSkuId ? app.request({ url: '/catalog/skus/' + this.data.editSkuId, method: 'PUT', data: payload }) : app.request({ url: '/catalog/skus', method: 'POST', data: payload });
    req.then(function () { self.setData({ showSkuForm: false }); wx.showToast({ title: '已保存', icon: 'success' }); self.loadSkus(); }).catch(function (err) { wx.showToast({ title: (err.body && err.body.detail) || '保存失败', icon: 'none' }); });
  },
  deleteSku: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '停用商品', content: '确认停用该SKU？', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/catalog/skus/' + id, method: 'DELETE' }).then(function () {
        wx.showToast({ title: '已停用', icon: 'none' }); self.loadSkus();
      }).catch(function () { wx.showToast({ title: '停用失败', icon: 'none' }); });
    }});
  },

  // ── 别名 ──
  loadAliases: function (e) {
    var skuId = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/catalog/skus/' + skuId + '/aliases' }).then(function (data) {
      self.setData({ aliases: data || [], aliasSkuId: skuId, tab: 'aliases' });
    }).catch(function () { wx.showToast({ title: '别名加载失败', icon: 'none' }); });
  },
  openAliasForm: function () { this.setData({ showAliasForm: true, aliasForm: { alias: '' } }); },
  closeAliasForm: function () { this.setData({ showAliasForm: false }); },
  onAliasField: function (e) { var f = this.data.aliasForm; f.alias = e.detail.value; this.setData({ aliasForm: f }); },
  saveAlias: function () {
    var self = this, alias = this.data.aliasForm.alias.trim();
    if (!alias) { wx.showToast({ title: '别名不能为空', icon: 'none' }); return; }
    app.request({ url: '/catalog/skus/' + this.data.aliasSkuId + '/aliases', method: 'POST', data: { alias: alias } }).then(function () {
      self.setData({ showAliasForm: false }); wx.showToast({ title: '已添加', icon: 'success' });
      self.loadAliases({ currentTarget: { dataset: { id: self.data.aliasSkuId } } });
    }).catch(function () { wx.showToast({ title: '别名添加失败', icon: 'none' }); });
  },
  removeAlias: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/catalog/aliases/' + id, method: 'DELETE' }).then(function () {
      self.loadAliases({ currentTarget: { dataset: { id: self.data.aliasSkuId } } });
    }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
  },

  // ── 规格 ──
  loadSpecs: function (e) {
    var skuId = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/catalog/skus/' + skuId + '/specs' }).then(function (data) {
      self.setData({ specs: data || [], specSkuId: skuId, tab: 'specs' });
    }).catch(function () { wx.showToast({ title: '规格加载失败', icon: 'none' }); });
  },
  openSpecForm: function () { this.setData({ showSpecForm: true, specForm: { name: '', price_delta: '0' } }); },
  closeSpecForm: function () { this.setData({ showSpecForm: false }); },
  onSpecField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var form = this.data.specForm; form[f] = v; this.setData({ specForm: form }); },
  saveSpec: function () {
    var self = this, f = this.data.specForm;
    if (!f.name.trim()) { wx.showToast({ title: '规格名不能为空', icon: 'none' }); return; }
    app.request({ url: '/catalog/skus/' + this.data.specSkuId + '/specs', method: 'POST', data: { name: f.name.trim(), price_delta: Number(f.price_delta) } }).then(function () {
      self.setData({ showSpecForm: false }); wx.showToast({ title: '已添加', icon: 'success' });
      self.loadSpecs({ currentTarget: { dataset: { id: self.data.specSkuId } } });
    }).catch(function () { wx.showToast({ title: '规格添加失败', icon: 'none' }); });
  },
  removeSpec: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/catalog/specs/' + id, method: 'DELETE' }).then(function () {
      self.loadSpecs({ currentTarget: { dataset: { id: self.data.specSkuId } } });
    }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
  },

  // ── 单位 ──
  loadUnits: function () {
    var self = this;
    app.request({ url: '/catalog/units' }).then(function (data) {
      self.setData({ units: data || [] });
    }).catch(function () { wx.showToast({ title: '单位加载失败', icon: 'none' }); });
  },
  loadConversions: function () {
    var self = this;
    app.request({ url: '/catalog/unit-conversions' }).then(function (data) {
      self.setData({ conversions: data || [] });
    }).catch(function () { wx.showToast({ title: '换算加载失败', icon: 'none' }); });
  },
  openUnitForm: function () { this.setData({ showUnitForm: true, unitForm: { code: '', name: '', kind: 'weight' } }); },
  closeUnitForm: function () { this.setData({ showUnitForm: false }); },
  onUnitField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var form = this.data.unitForm; form[f] = v; this.setData({ unitForm: form }); },
  saveUnit: function () {
    var self = this, f = this.data.unitForm;
    if (!f.code.trim()) { wx.showToast({ title: '代码不能为空', icon: 'none' }); return; }
    app.request({ url: '/catalog/units', method: 'POST', data: f }).then(function () {
      self.setData({ showUnitForm: false }); wx.showToast({ title: '已添加', icon: 'success' }); self.loadUnits();
    }).catch(function () { wx.showToast({ title: '单位添加失败', icon: 'none' }); });
  },
  openConvForm: function () { this.setData({ showConvForm: true, convForm: { from_unit: '', to_unit: '斤', factor: '', sku_id: '' } }); },
  closeConvForm: function () { this.setData({ showConvForm: false }); },
  onConvField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var form = this.data.convForm; form[f] = v; this.setData({ convForm: form }); },
  saveConv: function () {
    var self = this, f = this.data.convForm;
    if (!f.from_unit || !f.factor) { wx.showToast({ title: '请填写完整', icon: 'none' }); return; }
    var payload = { from_unit: f.from_unit, to_unit: f.to_unit, factor: Number(f.factor) };
    if (f.sku_id) payload.sku_id = f.sku_id;
    app.request({ url: '/catalog/unit-conversions', method: 'POST', data: payload }).then(function () {
      self.setData({ showConvForm: false }); wx.showToast({ title: '已添加', icon: 'success' }); self.loadConversions();
    }).catch(function () { wx.showToast({ title: '换算添加失败', icon: 'none' }); });
  },
  deleteConv: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/catalog/unit-conversions/' + id, method: 'DELETE' }).then(function () {
      self.loadConversions();
    }).catch(function () { wx.showToast({ title: '删除失败', icon: 'none' }); });
  },

  // ── 供应商 ──
  loadSuppliers: function () { var self = this; this.setData({ loading: true }); app.request({ url: '/catalog/suppliers' }).then(function (data) { self.setData({ suppliers: data || [], loading: false }); }).catch(function () { self.setData({ loading: false }); }); },
  openSupForm: function (e) { var s = e ? this.data.suppliers.find(function (x) { return x.supplier_id === e.currentTarget.dataset.id; }) : null; this.setData({ showSupForm: true, editSupId: s ? s.supplier_id : '', supForm: s ? { name: s.name, contact: s.contact || '', lead_time_hours: s.lead_time_hours ? String(s.lead_time_hours) : '' } : { name: '', contact: '', lead_time_hours: '' } }); },
  closeSupForm: function () { this.setData({ showSupForm: false }); },
  onSupField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var form = this.data.supForm; form[f] = v; this.setData({ supForm: form }); },
  saveSup: function () {
    var self = this, f = this.data.supForm;
    if (!f.name.trim()) { wx.showToast({ title: '名称不能为空', icon: 'none' }); return; }
    var payload = { name: f.name.trim(), contact: f.contact, lead_time_hours: f.lead_time_hours ? Number(f.lead_time_hours) : null };
    var req = this.data.editSupId ? app.request({ url: '/catalog/suppliers/' + this.data.editSupId, method: 'PUT', data: payload }) : app.request({ url: '/catalog/suppliers', method: 'POST', data: payload });
    req.then(function () { self.setData({ showSupForm: false }); wx.showToast({ title: '已保存', icon: 'success' }); self.loadSuppliers(); }).catch(function (err) { wx.showToast({ title: (err.body && err.body.detail) || '保存失败', icon: 'none' }); });
  },
  deleteSup: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '停用供应商', content: '确认停用？', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/catalog/suppliers/' + id, method: 'DELETE' }).then(function () {
        wx.showToast({ title: '已停用', icon: 'none' }); self.loadSuppliers();
      }).catch(function () { wx.showToast({ title: '停用失败', icon: 'none' }); });
    }});
  },
  viewStatement: function (e) { var id = e.currentTarget.dataset.id; wx.navigateTo({ url: '/pages/purchase/purchase?statement=' + id }); },
});
