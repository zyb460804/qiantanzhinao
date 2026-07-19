/** 库存页 v3.0 — 下拉刷新 / 搜索 / 预警视觉 / 空状态引导 */
var app = getApp();

Page({
  data: {
    inventoryItems: [], displayItems: [],
    expiryAlerts: [], expiringCount: 0,
    totalCategories: 0, inStockCount: 0, healthyCount: 0, attentionCount: 0, emptyCount: 0,
    insightTitle: '库存记录平稳', insightText: '当前没有需要优先处理的库存问题。', insightTone: 'good',
    sortMode: 'low', filterMode: 'all', searchKeyword: '', filteredCount: 0,
    updatedAt: '--:--', loading: true, isEmpty: false,
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadInventory();
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadInventory(function () { wx.stopPullDownRefresh(); },
                      function () { wx.stopPullDownRefresh(); });
  },

  _decorateItems: function (items) {
    return (items || []).map(function (item) {
      var qty = Number(item.current_qty != null ? item.current_qty : item.total_qty) || 0;
      var name = item.product_name || ('商品' + item.product_id);
      var status = qty <= 0 ? 'empty' : (qty <= 10 ? 'low' : 'healthy');
      var copy = {};
      Object.keys(item).forEach(function (key) { copy[key] = item[key]; });
      copy.display_name = name;
      copy.avatar_text = name.slice(0, 1);
      copy.qty_value = Math.round(qty * 10) / 10;
      copy.status = status;
      copy.status_text = status === 'healthy' ? '有库存' : (status === 'low' ? '余量较少' : '已售罄');
      copy.status_hint = status === 'healthy' ? '当前仍有库存' : (status === 'low' ? '仅按数量提示，请结合销量判断是否补货' : '如仍在售，请安排补货或校准库存');
      return copy;
    });
  },

  _sortItems: function (items, mode) {
    var list = (items || []).slice();
    if (mode === 'low') return list.sort(function (a, b) { return a.qty_value - b.qty_value; });
    if (mode === 'name') return list.sort(function (a, b) { return a.display_name.localeCompare(b.display_name, 'zh-CN'); });
    return list.sort(function (a, b) { return b.qty_value - a.qty_value; });
  },

  _applyView: function () {
    var keyword = (this.data.searchKeyword || '').trim().toLowerCase();
    var mode = this.data.filterMode;
    var filtered = (this.data.inventoryItems || []).filter(function (item) {
      var matchesKeyword = !keyword || item.display_name.toLowerCase().indexOf(keyword) >= 0;
      var matchesStatus = mode === 'all' || (mode === 'attention' && item.status !== 'healthy') || item.status === mode;
      return matchesKeyword && matchesStatus;
    });
    this.setData({ displayItems: this._sortItems(filtered, this.data.sortMode), filteredCount: filtered.length });
  },

  _buildInsight: function (attention, empty) {
    if (empty > 0) return { tone: 'danger', title: empty + ' 个商品已售罄', text: '先确认是否仍在售；需要继续销售的商品再进入采购或补货。' };
    if (attention > 0) return { tone: 'warn', title: attention + ' 个商品余量较少', text: '这是数量提示，不代表一定缺货；请结合近期销量和明日客流判断。' };
    return { tone: 'good', title: '当前没有明显库存异常', text: '继续记录进货、销售和损耗，库存判断会更准确。' };
  },

  _decorateAlerts: function (alerts) {
    return (alerts || []).map(function (alert) {
      var hours = Number(alert.hours_remaining);
      var copy = {};
      Object.keys(alert).forEach(function (key) { copy[key] = alert[key]; });
      copy.time_text = isNaN(hours) ? '请尽快处理' : (hours <= 0 ? '即将到期' : (hours < 24 ? '剩余 ' + Math.max(1, Math.round(hours)) + ' 小时' : '剩余 ' + Math.ceil(hours / 24) + ' 天'));
      copy.tone = alert.status === 'expiring' ? 'danger' : 'warn';
      copy.status_text = alert.status === 'expiring' ? '临期' : '关注';
      copy.display_unit = alert.unit || '原单位';
      return copy;
    });
  },

  _loadAlerts: function () {
    var self = this;
    return app.request({ url: '/inventory/alerts' }).then(function (data) {
      self.setData({ expiryAlerts: self._decorateAlerts((data && data.expiry_alerts) || []), expiringCount: Number(data && data.expiring_count) || 0 });
    }).catch(function () { self.setData({ expiryAlerts: [], expiringCount: 0 }); });
  },

  loadInventory: function (onSuccess, onError) {
    if (this._loading) return;
    this._loading = true;
    this.setData({ loading: true });
    var self = this;
    this._loadAlerts();
    app.request({ url: '/inventory/current' }).then(function (items) {
      var decorated = self._decorateItems(Array.isArray(items) ? items : []);
      var inStock = decorated.filter(function (item) { return item.qty_value > 0; }).length;
      var healthy = decorated.filter(function (item) { return item.status === 'healthy'; }).length;
      var attention = decorated.filter(function (item) { return item.status === 'low'; }).length;
      var empty = decorated.filter(function (item) { return item.status === 'empty'; }).length;
      var insight = self._buildInsight(attention, empty);
      var now = new Date();
      var updatedAt = (now.getHours() < 10 ? '0' : '') + now.getHours() + ':' + (now.getMinutes() < 10 ? '0' : '') + now.getMinutes();
      self._loading = false;
      self.setData({
        inventoryItems: decorated, totalCategories: decorated.length, inStockCount: inStock,
        healthyCount: healthy, attentionCount: attention, emptyCount: empty,
        insightTitle: insight.title, insightText: insight.text, insightTone: insight.tone,
        updatedAt: updatedAt, loading: false, isEmpty: decorated.length === 0,
      }, function () { self._applyView(); });
      if (onSuccess) onSuccess();
    }).catch(function (err) {
      self._loading = false;
      self.setData({ loading: false });
      app.logError('inventory/load', err, { silent: true });
      if (onError) onError();
      else wx.showToast({ title: '库存加载失败', icon: 'none' });
    });
  },

  manualRefresh: function () { if (!this._loading) this.loadInventory(); },
  changeSort: function (e) { this.setData({ sortMode: e.currentTarget.dataset.mode }, this._applyView); },
  changeFilter: function (e) { this.setData({ filterMode: e.currentTarget.dataset.mode }, this._applyView); },
  onSearchInput: function (e) { this.setData({ searchKeyword: e.detail.value }, this._applyView); },
  clearSearch: function () { this.setData({ searchKeyword: '' }, this._applyView); },
  resetFilters: function () { this.setData({ searchKeyword: '', filterMode: 'all', sortMode: 'low' }, this._applyView); },
  navigateToVoice: function () { wx.switchTab({ url: '/pages/voice/voice' }); },
  navigateToAdvisor: function () { wx.switchTab({ url: '/pages/advisor/advisor' }); },
  // 临期商品统一到经营管理页处理（改价/清货），避免两处重复展示与操作
  navigateToClearance: function () { wx.navigateTo({ url: '/pages/ops/ops?tab=clearance' }); },
  // 余量较少商品快捷补货
  navigateToPurchase: function () { wx.navigateTo({ url: '/pages/purchase/purchase' }); },
  // 商品卡上的"补货"动作：直接跳采购页
  onRestockTap: function (e) {
    var name = e.currentTarget.dataset.name || '';
    wx.navigateTo({ url: '/pages/purchase/purchase' + (name ? '?focus=' + encodeURIComponent(name) : '') });
  },
});
