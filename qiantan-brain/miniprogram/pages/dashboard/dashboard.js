/**
 * 数字孪生看板 — 四维度经营镜像可视化
 * Tab: 库存镜像 / 经营镜像 / 风险镜像
 *
 * 图表策略:
 *   - 库存 Tab: stock-chart 组件 (复用)
 *   - 经营 Tab: 内联 Canvas 双折线 (暂未抽组件)
 *   - 风险 Tab: risk-gauge 组件 (复用)
 */
var app = getApp();

Page({
  data: {
    activeTab: 'inventory',  // inventory | business | risk
    tabs: [
      { key: 'inventory', label: '库存', icon: '📦' },
      { key: 'business', label: '经营', icon: '📈' },
      { key: 'risk', label: '风险', icon: '⚠️' },
    ],

    // 数据
    dashboard: null,
    inventoryMirror: null,
    inventoryChartItems: [],
    businessMirror: null,
    bizRange: '7d',          // '7d' | '30d'
    bizSales: [],            // 当前选中区间的销售明细
    heatmapRows: [],         // 库存生命周期热力图分组结果
    heatmapBuckets: ['今日', '1天内', '2天内', '3天以上'],
    riskMirror: null,
    riskGaugeData: null,
    recommendations: [],

    loading: true,
    skinClass: '',
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadAllData();
  },

  // ── 数据加载 ─────────────────────────────────────────

  loadAllData: function () {
    var self = this;
    var mid = app.getMerchantId();

    this.setData({ loading: true });

    var req = app.request;

    Promise.all([
      req({ url: '/twin/dashboard',        data: { merchant_id: mid } }).catch(function(){return null}),
      req({ url: '/twin/inventory-mirror', data: { merchant_id: mid } }).catch(function(){return null}),
      req({ url: '/twin/business-mirror',  data: { merchant_id: mid } }).catch(function(){return null}),
      req({ url: '/twin/risk-mirror',      data: { merchant_id: mid } }).catch(function(){return null}),
      req({ url: '/advice/daily',          data: { merchant_id: mid } }).catch(function(){return null}),
    ]).then(function (results) {
      var invData = results[1];
      var riskData = results[3];

      // 映射 inventory-mirror 到 stock-chart 组件格式
      var chartItems = [];
      if (invData && invData.by_category) {
        chartItems = invData.by_category.slice(0, 7).map(function (c) {
          return {
            name: c.category || '',
            qty: Math.round(c.total_qty || 0),
            unit: '斤',
          };
        });
      }

      // 映射 risk-mirror 字段到 risk-gauge 组件字段
      var riskGaugeData = null;
      if (riskData) {
        riskGaugeData = {
          inventory_risk: riskData.inventory_risk || 0,
          weather_risk: riskData.weather_risk || 0,
          waste_risk: riskData.waste_risk || 0,
          traffic_risk: riskData.customer_flow_risk || 0,
          capital_risk: riskData.capital_risk || 0,
          concentration_risk: riskData.category_concentration_risk || 0,
        };
      }

      // 根据当前区间计算经营明细
      var bm = results[2];
      var bizArr = [];
      if (bm) {
        bizArr = self.data.bizRange === '30d'
          ? (bm.sales_30d || [])
          : (bm.sales_7d || []);
      }

      var db = results[0];
      var healthScore = db ? Math.max(0, Math.min(100, 100 - (db.risk_score || 0))) : 0;
      self.setData({
        dashboard: db,
        inventoryMirror: invData,
        inventoryChartItems: chartItems,
        businessMirror: bm,
        bizSales: bizArr,
        heatmapRows: self._buildHeatmap(invData),
        riskMirror: riskData,
        riskGaugeData: riskGaugeData,
        recommendations: results[4] ? results[4].recommendations : [],
        healthScore: healthScore,
        healthLevel: self._healthLevel(healthScore),
        loading: false,
      });
      // 经营 Tab 折线图需手动绘制; 库存/风险 Tab 由组件自动渲染
      setTimeout(function () { self.renderCurrentChart(); }, 300);
    });
  },

  // ── Tab 切换 ─────────────────────────────────────────

  onTabChange: function (e) {
    var tab = e.currentTarget.dataset.key;
    this.setData({ activeTab: tab });
    var self = this;
    setTimeout(function () { self.renderCurrentChart(); }, 200);
  },

  // 经营 Tab 区间切换 (近7日 / 近30日)
  onBizRangeChange: function (e) {
    var range = e.currentTarget.dataset.range;
    if (range === this.data.bizRange) return;
    var self = this;
    var bm = this.data.businessMirror;
    var arr = bm
      ? (range === '30d' ? (bm.sales_30d || []) : (bm.sales_7d || []))
      : [];
    this.setData({ bizRange: range, bizSales: arr }, function () {
      setTimeout(function () { self.renderCurrentChart(); }, 200);
    });
  },

  // 由 lifecycle_heatmap 构建热力图分组: 按商品分组, 每列取最严重颜色与最小剩余量
  _buildHeatmap: function (invData) {
    if (!invData || !invData.lifecycle_heatmap) return [];
    var buckets = ['today', '1day', '2days', '3days+'];
    var severity = { red: 3, yellow: 2, green: 1, gray: 0 };
    var colorHex = { red: '#E5484D', yellow: '#F2C037', green: '#2BA24C', gray: '#E6E8EB' };
    var textColor = { red: '#FFFFFF', yellow: '#5A4500', green: '#FFFFFF', gray: '#8A938D' };

    var byProduct = {};
    invData.lifecycle_heatmap.forEach(function (b) {
      if (!byProduct[b.product_name]) byProduct[b.product_name] = [];
      byProduct[b.product_name].push(b);
    });

    return Object.keys(byProduct).map(function (name) {
      var batches = byProduct[name];
      var cells = buckets.map(function (bk) {
        var inBucket = batches.filter(function (b) { return b.time_bucket === bk; });
        if (!inBucket.length) {
          return { bucket: bk, color: 'gray', style: 'background:' + colorHex.gray + ';', remaining_qty: null };
        }
        var worst = inBucket.reduce(function (acc, b) {
          return severity[b.color] > severity[acc.color] ? b : acc;
        });
        var minQty = Math.min.apply(null, inBucket.map(function (b) {
          return Number(b.remaining_qty) || 0;
        }));
        var c = worst.color;
        return {
          bucket: bk,
          color: c,
          style: 'background:' + colorHex[c] + ';color:' + textColor[c] + ';',
          remaining_qty: minQty,
        };
      });
      return { product_name: name, cells: cells };
    });
  },

  renderCurrentChart: function () {
    // 仅经营 Tab 需要手动绘制折线图
    // 库存 Tab 由 stock-chart 组件 observer 自动渲染
    // 风险 Tab 由 risk-gauge 组件 observer 自动渲染
    if (this.data.activeTab === 'business') {
      this._drawBusinessChart();
    }
  },

  // ── 经营镜像图表 (内联 Canvas 双折线) ─────────────────

  _drawBusinessChart: function () {
    var data = this.data.businessMirror;
    if (!data) return;
    var range = this.data.bizRange === '30d' ? data.sales_30d : data.sales_7d;
    if (!range || range.length === 0) return;

    var self = this;
    var query = wx.createSelectorQuery().in(this);
    query.select('#chartCanvas')
      .fields({ node: true, size: true })
      .exec(function (res) {
        if (!res[0] || !res[0].node) return;
        var canvas = res[0].node;
        var ctx = canvas.getContext('2d');
        var dpr = wx.getWindowInfo().pixelRatio;
        var w = res[0].width;
        var h = res[0].height;
        canvas.width = w * dpr; canvas.height = h * dpr;
        ctx.scale(dpr, dpr);

        var series = [
          { key: 'revenue', color: '#175C45', axis: 'left' },
          { key: 'profit', color: '#F3A83B', axis: 'left' },
          { key: 'customer_price', color: '#2E7DD1', axis: 'right' },
        ];
        self._drawLineChart(ctx, w, h, range, series);
      });
  },

  _drawLineChart: function (ctx, w, h, data, series) {
    var pad = { top: 22, right: 54, bottom: 34, left: 48 };
    var chartW = w - pad.left - pad.right;
    var chartH = h - pad.top - pad.bottom;
    var count = data.length;
    ctx.clearRect(0, 0, w, h);
    if (!count) return;

    // 双轴刻度: 左轴=金额(营业额/利润), 右轴=客单价
    var leftVals = [], rightVals = [];
    data.forEach(function (d) {
      series.forEach(function (s) {
        if (s.axis === 'right') rightVals.push(Number(d[s.key]) || 0);
        else leftVals.push(Number(d[s.key]) || 0);
      });
    });
    var maxLeft = Math.max.apply(null, leftVals.concat([1]));
    var magnitude = Math.pow(10, Math.max(0, String(Math.floor(maxLeft)).length - 2));
    maxLeft = Math.ceil(maxLeft / magnitude) * magnitude;
    var maxRight = Math.max.apply(null, rightVals.concat([1]));
    if (maxRight <= 10) maxRight = 10;
    else maxRight = Math.ceil(maxRight / (maxRight >= 100 ? 10 : 5)) * (maxRight >= 100 ? 10 : 5);

    var formatMoney = function (value) {
      if (value >= 10000) return '¥' + (value / 10000).toFixed(1) + '万';
      if (value >= 1000) return '¥' + (value / 1000).toFixed(1) + 'k';
      return '¥' + Math.round(value);
    };
    var formatAov = function (value) { return '¥' + (Math.round(value * 10) / 10); };
    var pointX = function (index) {
      return count === 1 ? pad.left + chartW / 2 : pad.left + index / (count - 1) * chartW;
    };
    var pointYL = function (value) {
      return pad.top + chartH - (Number(value) || 0) / maxLeft * chartH;
    };
    var pointYR = function (value) {
      return pad.top + chartH - (Number(value) || 0) / maxRight * chartH;
    };

    ctx.font = '10px sans-serif';
    ctx.textBaseline = 'middle';
    for (var g = 0; g <= 4; g++) {
      var gy = pad.top + g / 4 * chartH;
      ctx.beginPath();
      if (ctx.setLineDash) ctx.setLineDash([3, 4]);
      ctx.moveTo(pad.left, gy);
      ctx.lineTo(w - pad.right, gy);
      ctx.strokeStyle = g === 4 ? '#C9D5CD' : '#E4EAE5';
      ctx.lineWidth = 1;
      ctx.stroke();
      if (ctx.setLineDash) ctx.setLineDash([]);
      ctx.fillStyle = '#8A938D';
      ctx.textAlign = 'right';
      ctx.fillText(formatMoney(maxLeft * (1 - g / 4)), pad.left - 7, gy);
      ctx.fillStyle = '#2E7DD1';
      ctx.textAlign = 'left';
      ctx.fillText(formatAov(maxRight * (1 - g / 4)), w - pad.right + 6, gy);
    }

    // 第一条序列 (revenue) 下方填充渐变
    if (count > 1) {
      var gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
      gradient.addColorStop(0, 'rgba(23,92,69,.20)');
      gradient.addColorStop(1, 'rgba(23,92,69,0)');
      ctx.beginPath();
      data.forEach(function (d, i) {
        var x = pointX(i);
        var y = pointYL(d[series[0].key]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.lineTo(pointX(count - 1), pad.top + chartH);
      ctx.lineTo(pointX(0), pad.top + chartH);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    var drawLine = function (s) {
      var py = s.axis === 'right' ? pointYR : pointYL;
      ctx.beginPath();
      data.forEach(function (d, i) {
        var x = pointX(i);
        var y = py(d[s.key]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (count > 1) ctx.stroke();
      data.forEach(function (d, i) {
        var x = pointX(i);
        var y = py(d[s.key]);
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#FFFEFA';
        ctx.fill();
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 2.2;
        ctx.stroke();
      });
    };

    series.forEach(function (s) { drawLine(s); });

    ctx.fillStyle = '#7B8780';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    data.forEach(function (d, i) {
      var label = (d.date || '').slice(5).replace('-', '/');
      ctx.fillText(label, pointX(i), h - 8);
    });
  },

  _healthLevel: function (score) {
    var s = Number(score) || 0;
    if (s >= 85) return '经营状况优秀,继续保持';
    if (s >= 70) return '经营状况良好,小幅优化';
    if (s >= 60) return '经营状况一般,需关注异常';
    if (s > 0) return '经营状况偏弱,建议改进';
    return '暂未评分';
  },
});
