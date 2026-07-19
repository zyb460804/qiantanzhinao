/**
 * 数字孪生看板 — 实时库存与风险镜像
 * Tab: 库存镜像 / 风险镜像
 *   经营走势统一在「经营报告」页查看，避免重复展示。
 *
 * 图表策略:
 *   - 库存 Tab: stock-chart 组件 (复用)
 *   - 风险 Tab: risk-gauge 组件 (复用)
 */
var app = getApp();

Page({
  data: {
    activeTab: 'inventory',  // inventory | risk
    tabs: [
      { key: 'inventory', label: '库存', icon: '📦' },
      { key: 'risk', label: '风险', icon: '⚠️' },
    ],

    // 数据
    dashboard: null,
    inventoryMirror: null,
    inventoryChartItems: [],
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

    this.setData({ loading: true });

    // app.request 依赖 app 作为 this，不能脱离对象直接调用。
    var req = function (options) { return app.request(options); };

    Promise.all([
      req({ url: '/twin/dashboard' }).catch(function(){return null}),
      req({ url: '/twin/inventory-mirror' }).catch(function(){return null}),
      req({ url: '/twin/risk-mirror' }).catch(function(){return null}),
      req({ url: '/advice/daily' }).catch(function(){return null}),
    ]).then(function (results) {
      var invData = results[1];
      var riskData = results[2];

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

      var db = results[0];
      var healthScore = db ? Math.max(0, Math.min(100, 100 - (db.risk_score || 0))) : 0;
      self.setData({
        dashboard: db,
        inventoryMirror: invData,
        inventoryChartItems: chartItems,
        heatmapRows: self._buildHeatmap(invData),
        riskMirror: riskData,
        riskGaugeData: riskGaugeData,
        recommendations: results[3] ? results[3].recommendations : [],
        healthScore: healthScore,
        healthLevel: self._healthLevel(healthScore),
        loading: false,
      });
    });
  },

  // ── Tab 切换 ─────────────────────────────────────────

  onTabChange: function (e) {
    var tab = e.currentTarget.dataset.key;
    this.setData({ activeTab: tab });
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

  _healthLevel: function (score) {
    var s = Number(score) || 0;
    if (s >= 85) return '经营状况优秀,继续保持';
    if (s >= 70) return '经营状况良好,小幅优化';
    if (s >= 60) return '经营状况一般,需关注异常';
    if (s > 0) return '经营状况偏弱,建议改进';
    return '暂未评分';
  },
});
