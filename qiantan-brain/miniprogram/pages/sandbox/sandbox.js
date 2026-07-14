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
    app.request({ url: '/vision/categories' }).then(function (categories) {
      var cats = (categories || []).filter(function (c) {
        return c && c.name && (c.product_id || c.id);
      });
      var names = cats.map(function (c) { return c.name; });
      // 缓存可靠的 product_id 映射；缺少 ID 的商品不能用于模拟。
      self._productIdMap = {};
      cats.forEach(function (c) {
        self._productIdMap[c.name] = c.product_id || c.id;
      });
      self.setData({
        products: names,
        productIndex: 0,
        productsEmpty: names.length === 0,
      });
    }).catch(function () {
      self._productIdMap = {};
      self.setData({ products: [], productIndex: 0, productsEmpty: true });
      wx.showToast({ title: '商品列表加载失败', icon: 'none' });
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
    var data = {};
    // 保留空字符串，避免用户清空输入框时立刻被 0 顶回来。
    data[field] = e.detail.value;
    this.setData(data);
  },

  // ── 核心模拟 ─────────────────────────────────────────

  runSimulation: function () {
    var self = this;
    if (this.data.loading) return; // 防连点
    if (!this.data.products.length) { wx.showToast({ title: '商品列表暂不可用', icon: 'none' }); return; }
    var productName = this.data.products[this.data.productIndex];
    var pid = this._productIdMap && this._productIdMap[productName];
    var qty = Number(this.data.purchaseQty);
    var unitCost = Number(this.data.unitCost);
    var unitPrice = Number(this.data.unitPrice);
    if (!pid) { wx.showToast({ title: '商品信息无效，请重新进入页面', icon: 'none' }); return; }
    if (!isFinite(qty) || qty <= 0) { wx.showToast({ title: '进货量必须大于0', icon: 'none' }); return; }
    if (!isFinite(unitCost) || unitCost <= 0) { wx.showToast({ title: '请输入有效进货单价', icon: 'none' }); return; }
    if (!isFinite(unitPrice) || unitPrice <= 0) { wx.showToast({ title: '请输入有效计划售价', icon: 'none' }); return; }

    this.setData({ loading: true, result: null });

    // Step 1: 使用已验证的商品 ID，绝不回退到其他商品。

    // Step 2: 请求单场景模拟
    app.request({
      url: '/simulate/what-if',
      method: 'POST',
      data: {
        product_id: pid,
        scenario: {
          purchase_qty: qty,
          unit_cost: unitCost,
          unit_price: unitPrice,
        },
      },
    }).then(function (singleResult) {
      // Step 3: 生成三种对比方案 (保守 -20% / 标准 / 激进 +30%)
      var scenarios = [
        { name: '保守', purchase_qty: Math.max(1, Math.round(qty * 0.7)), unit_cost: unitCost, unit_price: unitPrice },
        { name: '标准', purchase_qty: qty, unit_cost: unitCost, unit_price: unitPrice },
        { name: '激进', purchase_qty: Math.max(1, Math.round(qty * 1.3)), unit_cost: unitCost, unit_price: unitPrice },
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
              net_profit: Number(r && r.net_profit) || 0,
              waste_rate: Number(r && r.waste_rate) || 0,
              estimated_sales: Number(r && r.estimated_sales) || 0,
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
      wx.showToast({ title: (err && err.body && err.body.detail) || '模拟失败，请检查参数', icon: 'none' });
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
