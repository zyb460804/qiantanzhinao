/**
 * stock-chart 库存结构图 · v5.6 纯 CSS 版
 * 原 canvas 2d 自绘在真机同层渲染失败时会浮层错位（压住页面其它内容），
 * 已改为 WXML/WXSS 渲染：observer 里把 items 归一化成行数据，
 * wxml 只负责铺条。Props API 不变：items / compact / max / title。
 */
var PALETTE = ['#14502e', '#24914f', '#1b7a44', '#34a85e', '#57c17c', '#8ad8a5', '#b9e8c8', '#ddf4e3'];
var CORN = '#d99a26';

Component({
  properties: {
    items: {
      type: Array,
      value: [],
      observer: function () { this._rebuild(); },
    },
    title: { type: String, value: '' },
    max: { type: Number, value: 0 },
    compact: {
      type: Boolean,
      value: false,
      observer: function () { this._rebuild(); },
    },
  },

  data: { rows: [] },

  lifetimes: {
    attached: function () { this._rebuild(); },
  },

  methods: {
    _formatQty: function (qty) {
      var value = Number(qty) || 0;
      if (value >= 10000) return (value / 10000).toFixed(value >= 100000 ? 0 : 1) + '万';
      if (value >= 1000) return (value / 1000).toFixed(value >= 10000 ? 0 : 1) + 'k';
      return Math.round(value * 10) / 10 + '';
    },

    _rebuild: function () {
      var source = this.data.items || [];
      var limit = this.data.compact ? 5 : 8;
      var items = source.slice(0, limit);

      var maxVal = Number(this.data.max) || 0;
      items.forEach(function (item) { maxVal = Math.max(maxVal, Number(item.qty) || 0); });
      if (maxVal <= 0) maxVal = 1;

      var rows = items.map(function (item, i) {
        var qty = Math.max(0, Number(item.qty) || 0);
        var isLow = item.status === 'low' || item.status === 'empty';
        return {
          idxText: (i < 9 ? '0' : '') + (i + 1),
          hi: i < 3,
          name: item.name || '未命名商品',
          qtyText: this._formatQty(qty) + (item.unit || ''),
          pct: Math.max(qty > 0 ? 4 : 0, Math.round(qty / maxVal * 100)),
          color: item.color || (isLow ? CORN : PALETTE[i % PALETTE.length]),
        };
      }, this);

      this.setData({ rows: rows });
    },
  },
});
