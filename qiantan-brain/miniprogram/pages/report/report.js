/**
 * 经营报告 — 每日/每周/月度经营状况与 AI 总结
 *
 * 数据来源:
 *   /reports/daily    — 今日经营快照 + 行动建议
 *   /reports/weekly   — 近7日汇总 + 健康评分
 *   /reports/trends   — 营业趋势折线 (支持 days 参数)
 *
 * 图表策略:
 *   内联 Canvas 2D 双折线 (营业额 + 利润), 参考 dashboard.js
 */
var app = getApp();

Page({
  data: {
    activeTab: 'daily',      // daily | weekly | monthly
    loading: true,
    dailyData: null,
    weeklyData: null,
    trendData: [],
    canvasWidth: 0,
    canvasHeight: 0,

    // 渲染辅助
    rangeLabel: '今日经营报告',
    refreshTime: '',
    hasData: false,
    metrics: {
      revenueDisplay: '0.00',
      costDisplay: '0.00',
      profitDisplay: '0.00',
      wasteDisplay: '0.00',
      revenueChangePct: null,
      revenueChangeAbs: '',
      profitMargin: null,
    },
    salesRanking: [],
    maxRankQty: 1,
    wasteList: [],
    aiSummary: '',
    actionItems: [],
    showAdoption: false,
    adoptionAdopted: 0,
    adoptionTotal: 0,
    adoptionRate: 0,
    healthLevel: '',
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.setData({ refreshTime: this._formatTime(new Date()) });
    this.loadReport();
  },

  onShow: function () {
    if (!this.data.loading && !this.data.dailyData) {
      this.loadReport();
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadReport(function () {
      wx.stopPullDownRefresh();
      app.showToast('报告已更新', 'success');
    }, function () {
      wx.stopPullDownRefresh();
    });
  },

  // ── Tab 切换 ─────────────────────────────────────────

  switchTab: function (e) {
    var tab = e.currentTarget.dataset.tab;
    if (tab === this.data.activeTab) return;
    this.setData({ activeTab: tab });
    this.loadReport();
  },

  // ── 数据加载 ─────────────────────────────────────────

  loadReport: function (onSuccess, onError) {
    var self = this;
    var tab = this.data.activeTab;
    var mid = app.getMerchantId();

    this.setData({ loading: true, hasData: false });

    var req = app.request;

    if (tab === 'daily') {
      // 今日报告 + 近7日趋势
      Promise.all([
        req({ url: '/reports/daily?merchant_id=' + mid }).catch(function () { return null; }),
        req({ url: '/reports/trends?merchant_id=' + mid + '&days=7' }).catch(function () { return null; }),
      ]).then(function (results) {
        var daily = results[0];
        var trends = results[1] || [];

        if (!daily) {
          self.setData({
            loading: false,
            hasData: false,
            trendData: trends,
            rangeLabel: '今日经营报告',
          });
          self._renderDerived();
          if (onError) onError();
          return;
        }

        self.setData({
          dailyData: daily,
          trendData: trends,
          rangeLabel: '今日经营报告 · ' + (daily.date || ''),
          refreshTime: self._formatTime(new Date()),
          loading: false,
          hasData: true,
        });
        self._renderDaily();
        self._renderDerived();
        if (onSuccess) onSuccess();
      });
    } else if (tab === 'weekly') {
      // 周报 + 近7日趋势
      Promise.all([
        req({ url: '/reports/weekly?merchant_id=' + mid }).catch(function () { return null; }),
        req({ url: '/reports/trends?merchant_id=' + mid + '&days=7' }).catch(function () { return null; }),
      ]).then(function (results) {
        var weekly = results[0];
        var trends = weekly && weekly.daily_trends ? weekly.daily_trends : (results[1] || []);

        if (!weekly) {
          self.setData({
            loading: false,
            hasData: false,
            trendData: trends,
            rangeLabel: '近7日经营报告',
          });
          self._renderDerived();
          if (onError) onError();
          return;
        }

        self.setData({
          weeklyData: weekly,
          trendData: trends,
          rangeLabel: '近7日经营报告 · ' + (weekly.period || ''),
          refreshTime: self._formatTime(new Date()),
          loading: false,
          hasData: true,
        });
        self._renderWeekly();
        self._renderDerived();
        if (onSuccess) onSuccess();
      });
    } else {
      // 近30日：拉取30天趋势,聚合核心指标
      req({ url: '/reports/trends?merchant_id=' + mid + '&days=30' })
        .catch(function () { return null; })
        .then(function (trends) {
          trends = trends || [];
          if (trends.length === 0) {
            self.setData({
              loading: false,
              hasData: false,
              trendData: [],
              rangeLabel: '近30日经营报告',
            });
            self._renderDerived();
            if (onError) onError();
            return;
          }

          // 聚合30天数据
          var totalRevenue = 0, totalCost = 0, totalProfit = 0;
          trends.forEach(function (d) {
            totalRevenue += Number(d.revenue) || 0;
            totalCost += Number(d.cost) || 0;
            totalProfit += Number(d.profit) || 0;
          });

          // 用聚合数据填充 weeklyData 供渲染复用
          var monthlyAgg = {
            period: '近30日',
            week_revenue: totalRevenue,
            week_profit: totalProfit,
            last_week_revenue: 0,
            revenue_change_pct: 0,
            daily_trends: trends,
            sales_ranking: [],
            waste_ranking: [],
            adoption_rate: 0,
            recommendation_total: 0,
            recommendation_adopted: 0,
            health_score: 0,
            ai_summary: '近30日累计营业额 ' + self._money(totalRevenue) +
                        ' 元,毛利润 ' + self._money(totalProfit) + ' 元。' +
                        '建议关注趋势变化,及时调整采购与销售策略。',
          };

          self.setData({
            weeklyData: monthlyAgg,
            trendData: trends,
            rangeLabel: '近30日经营报告',
            refreshTime: self._formatTime(new Date()),
            loading: false,
            hasData: true,
          });
          self._renderWeekly();
          self._renderDerived();
          if (onSuccess) onSuccess();
        });
    }
  },

  // ── 渲染派生数据 (图表、通用派生) ─────────────────────

  _renderDerived: function () {
    var self = this;
    // 延迟绘制 Canvas,确保 DOM 已更新
    setTimeout(function () {
      self.drawTrendChart();
    }, 280);
  },

  // ── 渲染今日数据 ─────────────────────────────────────

  _renderDaily: function () {
    var d = this.data.dailyData;
    if (!d) return;

    var changePct = (d.revenue_change_pct !== undefined && d.revenue_change_pct !== null)
      ? Number(d.revenue_change_pct) : null;
    var changeAbs = (d.revenue !== undefined && d.yesterday_revenue !== undefined)
      ? (Number(d.revenue) - Number(d.yesterday_revenue)) : 0;

    this.setData({
      metrics: {
        revenueDisplay: this._money(d.revenue),
        costDisplay: this._money(d.cost),
        profitDisplay: this._money(d.profit),
        wasteDisplay: this._money(d.waste_amount || 0),
        revenueChangePct: changePct,
        revenueChangeAbs: this._pct(changePct) + (changeAbs !== 0 ? ' ¥' + this._money(Math.abs(changeAbs)) : ''),
        profitMargin: (d.revenue > 0 && d.profit !== undefined)
          ? this._pct((Number(d.profit) / Number(d.revenue) * 100)) : null,
      },
      salesRanking: this._formatRanking(d.top_products || []),
      wasteList: this._formatSlowList(d.slow_moving || []),
      aiSummary: d.ai_summary || '',
      actionItems: d.action_items || [],
      showAdoption: (d.recommendation_total && d.recommendation_total > 0),
      adoptionAdopted: d.recommendation_adopted || 0,
      adoptionTotal: d.recommendation_total || 0,
      adoptionRate: d.recommendation_total
        ? Math.round((d.recommendation_adopted || 0) / d.recommendation_total * 100) : 0,
    });

    this._calcMaxRankQty(d.top_products || []);
  },

  // ── 渲染周报数据 ─────────────────────────────────────

  _renderWeekly: function () {
    var w = this.data.weeklyData;
    if (!w) return;

    var changePct = (w.revenue_change_pct !== undefined && w.revenue_change_pct !== null)
      ? Number(w.revenue_change_pct) : null;

    this.setData({
      metrics: {
        revenueDisplay: this._money(w.week_revenue),
        costDisplay: this._money(Number(w.week_revenue || 0) - Number(w.week_profit || 0)),
        profitDisplay: this._money(w.week_profit),
        wasteDisplay: this._money(this._sumWasteAmount(w.waste_ranking || [])),
        revenueChangePct: changePct,
        revenueChangeAbs: this._pct(changePct),
        profitMargin: (w.week_revenue > 0)
          ? this._pct((Number(w.week_profit) / Number(w.week_revenue) * 100)) : null,
      },
      salesRanking: this._formatRanking(w.sales_ranking || []),
      wasteList: this._formatWasteList(w.waste_ranking || []),
      aiSummary: w.ai_summary || '',
      actionItems: [],
      showAdoption: (w.recommendation_total && w.recommendation_total > 0),
      adoptionAdopted: w.recommendation_adopted || 0,
      adoptionTotal: w.recommendation_total || 0,
      adoptionRate: w.recommendation_total
        ? Math.round((w.recommendation_adopted || 0) / w.recommendation_total * 100) : (w.adoption_rate || 0),
      healthLevel: this._healthLevel(w.health_score),
    });

    this._calcMaxRankQty(w.sales_ranking || []);
  },

  // ── Canvas 趋势图绘制 ─────────────────────────────────

  drawTrendChart: function () {
    var data = this.data.trendData;
    if (!data || data.length === 0) return;

    var self = this;
    var query = wx.createSelectorQuery().in(this);
    query.select('#trendCanvas')
      .fields({ node: true, size: true })
      .exec(function (res) {
        if (!res[0] || !res[0].node) return;
        var canvas = res[0].node;
        var ctx = canvas.getContext('2d');
        var dpr = wx.getWindowInfo().pixelRatio;
        var w = res[0].width;
        var h = res[0].height;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.scale(dpr, dpr);

        self.setData({ canvasWidth: w, canvasHeight: h });
        self._drawLineChart(ctx, w, h, data);
      });
  },

  _drawLineChart: function (ctx, w, h, data) {
    var pad = { top: 22, right: 54, bottom: 34, left: 48 };
    var chartW = w - pad.left - pad.right;
    var chartH = h - pad.top - pad.bottom;
    var count = data.length;
    ctx.clearRect(0, 0, w, h);
    if (!count) return;

    // 采样:超过14个点时抽取
    var drawData = data;
    if (count > 14) {
      var step = Math.ceil(count / 14);
      drawData = [];
      for (var i = 0; i < count; i += step) {
        drawData.push(data[i]);
      }
      if (drawData[drawData.length - 1] !== data[count - 1]) {
        drawData.push(data[count - 1]);
      }
      count = drawData.length;
    }

    // 双轴刻度: 左轴=金额(营业额/利润), 右轴=客单价
    var leftVals = [], rightVals = [];
    drawData.forEach(function (d) {
      leftVals.push(Number(d.revenue) || 0);
      leftVals.push(Number(d.profit) || 0);
      rightVals.push(Number(d.customer_price) || 0);
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

    // 网格线 + 双轴刻度
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
      // 左轴(金额)
      ctx.fillStyle = '#8A938D';
      ctx.textAlign = 'right';
      ctx.fillText(formatMoney(maxLeft * (1 - g / 4)), pad.left - 7, gy);
      // 右轴(客单价)
      ctx.fillStyle = '#2E7DD1';
      ctx.textAlign = 'left';
      ctx.fillText(formatAov(maxRight * (1 - g / 4)), w - pad.right + 6, gy);
    }

    // 营业额区域填充(左轴)
    if (count > 1) {
      var gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
      gradient.addColorStop(0, 'rgba(47, 158, 110, 0.22)');
      gradient.addColorStop(1, 'rgba(47, 158, 110, 0)');
      ctx.beginPath();
      drawData.forEach(function (d, i) {
        var x = pointX(i);
        var y = pointYL(d.revenue);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.lineTo(pointX(count - 1), pad.top + chartH);
      ctx.lineTo(pointX(0), pad.top + chartH);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    // 绘制折线: 营业额/利润走左轴, 客单价走右轴
    var drawLine = function (key, color, axis) {
      var py = axis === 'right' ? pointYR : pointYL;
      ctx.beginPath();
      drawData.forEach(function (d, i) {
        var x = pointX(i);
        var y = py(d[key]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (count > 1) ctx.stroke();
      // 数据点
      drawData.forEach(function (d, i) {
        var x = pointX(i);
        var y = py(d[key]);
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#FFFEFA';
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.stroke();
      });
    };

    drawLine('revenue', '#2f9e6e', 'left');
    drawLine('profit', '#e8552f', 'left');
    drawLine('customer_price', '#2E7DD1', 'right');

    // X 轴日期标签
    ctx.fillStyle = '#7B8780';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'alphabetic';
    var labelStep = 1;
    if (count > 7) labelStep = Math.ceil(count / 6);
    drawData.forEach(function (d, i) {
      if (i % labelStep !== 0 && i !== count - 1) return;
      var label = (d.date || '').slice(5).replace('-', '/');
      ctx.fillText(label, pointX(i), h - 8);
    });
  },

  // ── 跳转语音记账 ─────────────────────────────────────

  goVoice: function () {
    wx.switchTab({ url: '/pages/voice/voice' });
  },

  // ── 工具方法 ─────────────────────────────────────────

  _formatRanking: function (list) {
    return (list || []).map(function (item) {
      var revenue = Number(item.revenue) || 0;
      return {
        product_id: item.product_id,
        product_name: item.product_name || '未命名商品',
        qty: Number(item.qty) || 0,
        revenue: revenue,
        revenueDisplay: revenue.toFixed(2),
      };
    });
  },

  _formatWasteList: function (list) {
    return (list || []).map(function (item) {
      var amount = Number(item.amount) || 0;
      return {
        product_id: item.product_id,
        product_name: item.product_name || '未命名商品',
        qty: Number(item.qty) || 0,
        amount: amount,
        amountDisplay: amount.toFixed(2),
      };
    });
  },

  _formatSlowList: function (list) {
    return (list || []).map(function (item) {
      return {
        product_id: item.product_id,
        product_name: item.product_name || '未命名商品',
        stock_qty: Number(item.stock_qty) || 0,
      };
    });
  },

  _calcMaxRankQty: function (list) {
    var max = 1;
    (list || []).forEach(function (item) {
      var q = Number(item.qty) || 0;
      if (q > max) max = q;
    });
    this.setData({ maxRankQty: max });
  },

  _sumWasteAmount: function (list) {
    var total = 0;
    (list || []).forEach(function (item) {
      total += Number(item.amount) || 0;
    });
    return total;
  },

  _money: function (value) {
    var num = Number(value) || 0;
    return num.toFixed(2);
  },

  _pct: function (value) {
    var num = Number(value) || 0;
    var prefix = num > 0 ? '+' : '';
    return prefix + num.toFixed(1) + '%';
  },

  _healthLevel: function (score) {
    var s = Number(score) || 0;
    if (s >= 85) return '经营状况优秀,继续保持';
    if (s >= 70) return '经营状况良好,小幅优化';
    if (s >= 60) return '经营状况一般,需关注异常';
    if (s > 0) return '经营状况偏弱,建议改进';
    return '暂未评分';
  },

  _formatTime: function (date) {
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    var y = date.getFullYear();
    var m = pad(date.getMonth() + 1);
    var d = pad(date.getDate());
    var hh = pad(date.getHours());
    var mm = pad(date.getMinutes());
    return y + '-' + m + '-' + d + ' ' + hh + ':' + mm;
  },
});
