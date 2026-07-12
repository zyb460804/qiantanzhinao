/**
 * 决策沙盘页 — What-if 模拟 + 三种方案对比柱状图
 */
var app = getApp();
var Chart = require('../../utils/chart');

Page({
  data: {
    // 商品选择
    products: [],
    productIndex: 0,
    productsEmpty: false,

    // 输入参数
    purchaseQty: 50,
    unitCost: 0.5,
    unitPrice: 2.0,

    // 运行状态
    loading: false,
    result: null,

    // ECharts
    ecReady: false,
    chartInstance: null,
    skinClass: '',
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadProducts();
  },

  onReady: function () {
    this.setData({ ecReady: true });
    if (this.data.result) this.renderChart();
  },

  // ── 加载商品列表 ─────────────────────────────────────

  loadProducts: function () {
    var self = this;
    // 只请求品类(库存数据在沙盘中未使用,移除冗余请求)
    app.request({ url: '/vision/categories' }).catch(function () { return []; }).then(function (categories) {
      var cats = categories || [];
      var names = cats.map(function (c) { return c.name || '商品'; });
      // 缓存 product_id 映射,避免 runSimulation 时重复请求 /vision/categories
      self._productIdMap = {};
      cats.forEach(function (c) {
        if (c.name) self._productIdMap[c.name] = c.product_id || c.id || 1;
      });
      self.setData({
        products: names,
        productsEmpty: names.length === 0,
      });
    });
  },

  // ── 表单交互 ─────────────────────────────────────────

  onProductChange: function (e) {
    this.setData({ productIndex: parseInt(e.detail.value) });
  },

  onSliderChange: function (e) {
    this.setData({ purchaseQty: e.detail.value });
  },

  onFieldInput: function (e) {
    var field = e.currentTarget.dataset.field;
    var val = parseFloat(e.detail.value) || 0;
    var data = {};
    data[field] = val;
    this.setData(data);
  },

  // ── 核心模拟 ─────────────────────────────────────────

  runSimulation: function () {
    var self = this;
    if (this.data.loading) return; // 防连点
    if (!this.data.products.length) { wx.showToast({ title: '商品列表暂不可用', icon: 'none' }); return; }
    var productName = this.data.products[this.data.productIndex];

    this.setData({ loading: true, result: null });

    // Step 1: 从缓存的 product_id 映射中查找(避免重复请求 /vision/categories)
    var pid = (this._productIdMap && this._productIdMap[productName]) || 1;

    // Step 2: 请求单场景模拟
    app.request({
      url: '/simulate/what-if',
      method: 'POST',
      data: {
        product_id: pid,
        scenario: {
          purchase_qty: self.data.purchaseQty,
          unit_cost: self.data.unitCost,
          unit_price: self.data.unitPrice,
        },
      },
    }).then(function (singleResult) {
      // Step 3: 生成三种对比方案 (保守 -20% / 标准 / 激进 +30%)
      var qty = self.data.purchaseQty;
      var scenarios = [
        { name: '保守', purchase_qty: Math.round(qty * 0.7), unit_cost: self.data.unitCost, unit_price: self.data.unitPrice },
        { name: '标准', purchase_qty: qty, unit_cost: self.data.unitCost, unit_price: self.data.unitPrice },
        { name: '激进', purchase_qty: Math.round(qty * 1.3), unit_cost: self.data.unitCost, unit_price: self.data.unitPrice },
      ];

      return app.request({
        url: '/simulate/scenario',
        method: 'POST',
        data: {
          simulations: scenarios.map(function (s) {
            return {
              purchase_qty: s.purchase_qty,
              unit_cost: s.unit_cost,
              unit_price: s.unit_price,
              product_name: productName,
            };
          }),
        },
      }).then(function (multiResult) {
        return {
          single: singleResult,
          multi: (multiResult || []).map(function (r, i) {
            return {
              name: scenarios[i].name,
              purchase_qty: scenarios[i].purchase_qty,
              net_profit: r.output ? r.output.net_profit : 0,
              waste_rate: r.output ? r.output.waste_rate : 0,
              estimated_sales: r.output ? r.output.estimated_sales : 0,
            };
          }),
        };
      });
    }).then(function (combined) {
      self.setData({ loading: false, result: combined });
      // 用 nextTick 确保 DOM 更新后再绘制图表
      wx.nextTick(function () { self.renderChart(); });
    }).catch(function (err) {
      console.error('Simulation error:', err);
      self.setData({ loading: false });
      wx.showToast({ title: '模拟失败', icon: 'none' });
    });
  },

  // ── 图表渲染 (Canvas-based fallback) ─────────────────

  renderChart: function () {
    var result = this.data.result;
    if (!result || !result.multi) return;

    var self = this;
    Chart.initCanvas(this, '#sandboxCanvas').then(function (c) {
      if (!c) return;
      Chart.drawBarChart(c.ctx, c.width, c.height, result.multi, {
        valueKey: 'net_profit',
        labelKey: 'name',
        subKey: 'purchase_qty',
        subSuffix: '斤',
      });
    });
  },
});
