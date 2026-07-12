/**
 * 决策沙盘页 — What-if 模拟 + 三种方案对比柱状图
 * 依赖: ECharts (通过 ec-canvas 组件)
 * 安装: npm install echarts-for-weixin --production
 */
var app = getApp();

Page({
  data: {
    // 商品选择
    products: [],
    productIndex: 0,

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
    var mid = app.getMerchantId();

    // 并行获取品类 + 库存
    Promise.all([
      app.request({ url: '/vision/categories' }).catch(function () { return []; }),
      app.request({ url: '/inventory/current', data: { merchant_id: mid } }).catch(function () { return []; }),
    ]).then(function (results) {
      var categories = results[0] || [];
      var names = categories.map(function (c) { return c.name || '商品'; });
      self.setData({
        products: names.length > 0 ? names : ['白菜', '土豆', '黄瓜', '番茄', '西瓜'],
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
    var mid = app.getMerchantId();
    var productName = this.data.products[this.data.productIndex];

    this.setData({ loading: true, result: null });

    // Step 1: 找 product_id
    app.request({ url: '/vision/categories' }).then(function (cats) {
      var pid = 1;
      (cats || []).forEach(function (c) {
        if (c.name === productName) pid = c.product_id || c.id;
      });

      // Step 2: 请求单场景模拟
      return app.request({
        url: '/simulate/what-if',
        method: 'POST',
        data: {
          merchant_id: mid,
          product_id: pid,
          scenario: {
            purchase_qty: self.data.purchaseQty,
            unit_cost: self.data.unitCost,
            unit_price: self.data.unitPrice,
          },
        },
      });
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
          merchant_id: mid,
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
      // 延迟渲染图表 (确保 DOM 更新)
      setTimeout(function () { self.renderChart(); }, 200);
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

    var scenarios = result.multi;

    // 使用 Canvas 2D 绘制简单柱状图
    var query = wx.createSelectorQuery().in(this);
    query.select('#sandboxCanvas')
      .fields({ node: true, size: true })
      .exec((function (res) {
        if (!res[0] || !res[0].node) return;
        var canvas = res[0].node;
        var ctx = canvas.getContext('2d');
        var dpr = wx.getWindowInfo().pixelRatio;
        var width = res[0].width;
        var height = res[0].height;

        canvas.width = width * dpr;
        canvas.height = height * dpr;
        ctx.scale(dpr, dpr);

        this._drawBarChart(ctx, width, height, scenarios);
      }).bind(this));
  },

  _drawBarChart: function (ctx, w, h, scenarios) {
    var pad = { top: 34, right: 18, bottom: 58, left: 40 };
    var chartW = w - pad.left - pad.right;
    var chartH = h - pad.top - pad.bottom;
    var count = scenarios.length;
    if (!count) return;
    ctx.clearRect(0, 0, w, h);

    var profits = scenarios.map(function (item) { return Number(item.net_profit) || 0; });
    var maxAbs = Math.max.apply(null, profits.map(Math.abs).concat([1]));
    maxAbs = Math.ceil(maxAbs / 10) * 10;
    var zeroY = pad.top + chartH / 2;
    var groupW = chartW / count;
    var barW = Math.max(26, Math.min(52, groupW * .48));
    var colors = ['#78A890', '#175C45', '#F3A83B'];
    var bestIndex = 0;
    profits.forEach(function (value, index) { if (value > profits[bestIndex]) bestIndex = index; });

    var roundedBar = function (x, y, width, height, radius) {
      var r = Math.min(radius, width / 2, height / 2);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + width, y, x + width, y + height, r);
      ctx.arcTo(x + width, y + height, x, y + height, r);
      ctx.arcTo(x, y + height, x, y, r);
      ctx.arcTo(x, y, x + width, y, r);
      ctx.closePath();
    };

    [-1, 0, 1].forEach(function (step) {
      var y = zeroY - step * chartH / 2;
      ctx.beginPath();
      if (ctx.setLineDash && step !== 0) ctx.setLineDash([3, 4]);
      ctx.moveTo(pad.left, y);
      ctx.lineTo(w - pad.right, y);
      ctx.strokeStyle = step === 0 ? '#B9C8BF' : '#E5EAE6';
      ctx.lineWidth = step === 0 ? 1.2 : 1;
      ctx.stroke();
      if (ctx.setLineDash) ctx.setLineDash([]);
    });

    ctx.fillStyle = '#8A938D';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText('+' + Math.round(maxAbs), pad.left - 5, pad.top);
    ctx.fillText('0', pad.left - 5, zeroY);
    ctx.fillText('-' + Math.round(maxAbs), pad.left - 5, pad.top + chartH);

    scenarios.forEach(function (scenario, i) {
      var centerX = pad.left + groupW * (i + .5);
      var x = centerX - barW / 2;
      var profit = profits[i];
      var actualH = Math.abs(profit) / maxAbs * chartH / 2;
      var visualH = Math.max(actualH, 3);
      var y = profit >= 0 ? zeroY - visualH : zeroY;
      var color = profit < 0 ? '#D9524A' : colors[i % colors.length];

      roundedBar(x, y, barW, visualH, 8);
      ctx.fillStyle = color;
      ctx.fill();

      ctx.fillStyle = color;
      ctx.font = '700 12px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      var valueY = profit >= 0 ? Math.max(13, y - 7) : Math.min(h - pad.bottom + 14, y + visualH + 16);
      ctx.fillText((profit >= 0 ? '+' : '-') + '¥' + Math.abs(profit).toFixed(0), centerX, valueY);

      if (i === bestIndex) {
        var pillY = Math.max(2, valueY - 25);
        roundedBar(centerX - 18, pillY, 36, 17, 8.5);
        ctx.fillStyle = '#E7F1EB';
        ctx.fill();
        ctx.fillStyle = '#175C45';
        ctx.font = '700 9px sans-serif';
        ctx.textBaseline = 'middle';
        ctx.fillText('推荐', centerX, pillY + 8.5);
      }

      ctx.fillStyle = '#263B31';
      ctx.font = '600 11px sans-serif';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(scenario.name || ['保守', '标准', '激进'][i] || '方案', centerX, h - 25);
      ctx.fillStyle = '#8A938D';
      ctx.font = '9px sans-serif';
      ctx.fillText((scenario.purchase_qty || 0) + '斤', centerX, h - 9);
    });
  },
});
