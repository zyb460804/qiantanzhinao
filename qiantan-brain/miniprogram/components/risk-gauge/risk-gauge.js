/** 风险雷达图：库存 / 天气 / 损耗 / 客流 / 资金 / 品类 */
Component({
  properties: {
    risks: {
      type: Object,
      value: {},
      observer: function () { if (this._canvasReady) this.render(); },
    },
  },

  data: { _canvasReady: false },

  lifetimes: {
    attached: function () { this._initCanvas(); },
  },

  methods: {
    _initCanvas: function () {
      var self = this;
      this.createSelectorQuery().select('#rg-canvas')
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
          self._ctx = ctx;
          self._width = res[0].width;
          self._height = res[0].height;
          self._canvasReady = true;
          self.setData({ _canvasReady: true });
          self.render();
        });
    },

    render: function () {
      var ctx = this._ctx;
      var w = this._width;
      var h = this._height;
      if (!ctx || !w || !h) return;

      var risks = this.data.risks || {};
      var axes = [
        { name: '库存', key: 'inventory_risk' },
        { name: '天气', key: 'weather_risk' },
        { name: '损耗', key: 'waste_risk' },
        { name: '客流', key: 'traffic_risk' },
        { name: '资金', key: 'capital_risk' },
        { name: '品类', key: 'concentration_risk' },
      ];
      var values = axes.map(function (axis) {
        return Math.max(0, Math.min(100, Number(risks[axis.key]) || 0));
      });
      var average = Math.round(values.reduce(function (sum, value) { return sum + value; }, 0) / values.length);
      var color = average >= 65 ? '#D9524A' : (average >= 35 ? '#F3A83B' : '#175C45');
      var fillColor = average >= 65 ? 'rgba(217,82,74,.16)' : (average >= 35 ? 'rgba(243,168,59,.18)' : 'rgba(23,92,69,.16)');

      ctx.clearRect(0, 0, w, h);
      var cx = w / 2;
      var cy = h / 2 + 2;
      var radius = Math.min(w * .34, h * .34);
      var n = axes.length;

      for (var ring = 4; ring >= 1; ring--) {
        var rr = radius * ring / 4;
        ctx.beginPath();
        for (var i = 0; i <= n; i++) {
          var angle = -Math.PI / 2 + Math.PI * 2 * i / n;
          var x = cx + Math.cos(angle) * rr;
          var y = cy + Math.sin(angle) * rr;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.fillStyle = ring % 2 === 0 ? 'rgba(23,92,69,.018)' : 'rgba(23,92,69,.035)';
        ctx.fill();
        ctx.strokeStyle = ring === 4 ? '#C9D8CF' : '#E0E8E2';
        ctx.lineWidth = ring === 4 ? 1.2 : 1;
        ctx.stroke();
      }

      for (var a = 0; a < n; a++) {
        var axisAngle = -Math.PI / 2 + Math.PI * 2 * a / n;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(axisAngle) * radius, cy + Math.sin(axisAngle) * radius);
        ctx.strokeStyle = '#DFE8E1';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      ctx.beginPath();
      var points = [];
      for (var p = 0; p < n; p++) {
        var pointAngle = -Math.PI / 2 + Math.PI * 2 * p / n;
        var pointRadius = radius * values[p] / 100;
        var px = cx + Math.cos(pointAngle) * pointRadius;
        var py = cy + Math.sin(pointAngle) * pointRadius;
        points.push({ x: px, y: py });
        if (p === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.2;
      ctx.lineJoin = 'round';
      ctx.stroke();

      points.forEach(function (point) {
        ctx.beginPath();
        ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#FFFEFA';
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
      });

      for (var m = 0; m < n; m++) {
        var labelAngle = -Math.PI / 2 + Math.PI * 2 * m / n;
        var labelR = radius + 24;
        var lx = cx + Math.cos(labelAngle) * labelR;
        var ly = cy + Math.sin(labelAngle) * labelR;
        if (Math.cos(labelAngle) > .35) ctx.textAlign = 'left';
        else if (Math.cos(labelAngle) < -.35) ctx.textAlign = 'right';
        else ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#263B31';
        ctx.font = '600 12px sans-serif';
        ctx.fillText(axes[m].name, lx, ly - 7);
        ctx.fillStyle = '#7B8780';
        ctx.font = '11px sans-serif';
        ctx.fillText(Math.round(values[m]) + ' 分', lx, ly + 8);
      }

      ctx.beginPath();
      ctx.arc(cx, cy, 28, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,254,250,.94)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(23,92,69,.09)';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = color;
      ctx.font = '700 17px sans-serif';
      ctx.fillText(average + '', cx, cy - 5);
      ctx.fillStyle = '#7B8780';
      ctx.font = '9px sans-serif';
      ctx.fillText('综合风险', cx, cy + 11);
    },
  },
});
