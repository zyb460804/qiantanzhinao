/**
 * stock-chart 库存结构图
 * Canvas 2D 自绘水平条形图，无外部依赖。
 */
Component({
  properties: {
    items: {
      type: Array,
      value: [],
      observer: function () {
        if (this._canvasReady) this.render();
      },
    },
    title: { type: String, value: '' },
    max: { type: Number, value: 0 },
    compact: { type: Boolean, value: false },
  },

  data: { _canvasReady: false },

  lifetimes: {
    attached: function () { this._initCanvas(); },
  },

  methods: {
    _initCanvas: function () {
      var self = this;
      this.createSelectorQuery().select('#sc-canvas')
        .fields({ node: true, size: true })
        .exec(function (res) {
          if (!res || !res[0] || !res[0].node) return;
          var canvas = res[0].node;
          var ctx = canvas.getContext('2d');
          var info = wx.getWindowInfo();
          var dpr = info.pixelRatio || 1;
          canvas.width = res[0].width * dpr;
          canvas.height = res[0].height * dpr;
          ctx.scale(dpr, dpr);
          self._canvas = canvas;
          self._ctx = ctx;
          self._width = res[0].width;
          self._height = res[0].height;
          self._canvasReady = true;
          self.setData({ _canvasReady: true });
          self.render();
        });
    },

    _roundRect: function (ctx, x, y, width, height, radius) {
      var r = Math.min(radius, height / 2, width / 2);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + width, y, x + width, y + height, r);
      ctx.arcTo(x + width, y + height, x, y + height, r);
      ctx.arcTo(x, y + height, x, y, r);
      ctx.arcTo(x, y, x + width, y, r);
      ctx.closePath();
    },

    _fitText: function (ctx, text, maxWidth) {
      var value = String(text || '未命名商品');
      if (ctx.measureText(value).width <= maxWidth) return value;
      while (value.length > 1 && ctx.measureText(value + '…').width > maxWidth) {
        value = value.slice(0, -1);
      }
      return value + '…';
    },

    _formatQty: function (qty) {
      var value = Number(qty) || 0;
      if (value >= 10000) return (value / 10000).toFixed(value >= 100000 ? 0 : 1) + '万';
      if (value >= 1000) return (value / 1000).toFixed(value >= 10000 ? 0 : 1) + 'k';
      return Math.round(value * 10) / 10 + '';
    },

    render: function () {
      var ctx = this._ctx;
      var w = this._width;
      var h = this._height;
      if (!ctx || !w || !h) return;

      ctx.clearRect(0, 0, w, h);
      var source = this.data.items || [];
      var items = source.slice(0, this.data.compact ? 5 : 8);
      if (!items.length) {
        ctx.fillStyle = '#8A938D';
        ctx.font = '13px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('暂无库存数据', w / 2, h / 2);
        return;
      }

      var maxVal = Number(this.data.max) || 0;
      items.forEach(function (item) { maxVal = Math.max(maxVal, Number(item.qty) || 0); });
      if (maxVal <= 0) maxVal = 1;

      var padX = 4;
      var padTop = 5;
      var padBottom = 5;
      var rowH = (h - padTop - padBottom) / items.length;
      var trackYGap = Math.min(22, rowH * 0.52);
      var barH = Math.max(6, Math.min(9, rowH * 0.22));
      var barX = 30;
      var barW = w - barX - padX;
      var palette = ['#175C45', '#28745B', '#3F896E', '#66A084', '#8CB69F', '#A8C6B5', '#C0D7CA', '#D2E2D9'];

      for (var i = 0; i < items.length; i++) {
        var item = items[i] || {};
        var qty = Math.max(0, Number(item.qty) || 0);
        var rowTop = padTop + i * rowH;
        var textY = rowTop + Math.max(10, rowH * 0.28);
        var trackY = rowTop + trackYGap;
        var fillW = qty > 0 ? Math.max(barH, qty / maxVal * barW) : 0;
        var isLow = item.status === 'low' || item.status === 'empty';
        var color = item.color || (isLow ? '#F3A83B' : palette[i % palette.length]);

        ctx.textBaseline = 'middle';
        ctx.font = '600 11px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = i < 3 ? '#175C45' : '#7B8780';
        ctx.fillText((i < 9 ? '0' : '') + (i + 1), 14, textY);

        ctx.font = '600 12px sans-serif';
        ctx.textAlign = 'left';
        ctx.fillStyle = '#20342B';
        ctx.fillText(this._fitText(ctx, item.name, Math.max(70, w - 155)), barX, textY);

        ctx.font = '700 12px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillStyle = color;
        ctx.fillText(this._formatQty(qty) + (item.unit || ''), w - padX, textY);

        this._roundRect(ctx, barX, trackY, barW, barH, barH / 2);
        ctx.fillStyle = '#E9EEE9';
        ctx.fill();
        if (fillW > 0) {
          this._roundRect(ctx, barX, trackY, fillW, barH, barH / 2);
          ctx.fillStyle = color;
          ctx.fill();
        }
      }
    },
  },
});

