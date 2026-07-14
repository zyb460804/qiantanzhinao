/**
 * 经营报告 — 每日/每周/月度经营状况与 AI 总结
 *
 * 数据来源:
 *   /reports/daily    — 今日经营快照 + 行动建议
 *   /reports/weekly   — 近7日汇总 + 健康评分
 *   /reports/trends   — 营业趋势折线 (支持 days 参数)
 *
 * 图表策略:
 *   utils/chart.js 统一 Canvas 2D 折线图
 */
var app = getApp();
var Chart = require('../../utils/chart');

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
    errorText: '',
  },

  onLoad: function () {
    this._seenReportDataVersion = app.getReportDataVersion();
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.setData({ refreshTime: this._formatTime(new Date()) });
    this.loadReport('daily');
  },

  onShow: function () {
    var currentVersion = app.getReportDataVersion();
    if (this._seenReportDataVersion !== currentVersion) {
      this._seenReportDataVersion = currentVersion;
      this._tabCache = {};
      if (!this.data.loading) this.loadReport(this.data.activeTab);
      return;
    }
    // 如果 page 被回收(activeTab 已非初始 daily)或数据为空,重新加载当前 tab
    if (!this.data.loading && !this.data.hasData) {
      this.loadReport(this.data.activeTab);
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    var tab = this.data.activeTab;
    this._tabCache = {};
    this._seenReportDataVersion = app.getReportDataVersion();
    this.loadReport(tab, function () {
      wx.stopPullDownRefresh();
      app.showToast('报告已更新', 'success');
    }, function () {
      wx.stopPullDownRefresh();
    });
  },

  // ── Tab 切换 ─────────────────────────────────────────

  switchTab: function (e) {
    var tab = e.currentTarget.dataset.tab;
    if (!tab || tab === this.data.activeTab) return;

    // 缓存当前 Tab 数据
    var oldTab = this.data.activeTab;
    this._tabCache = this._tabCache || {};
    this._tabCache[oldTab] = {
      dailyData: this.data.dailyData,
      weeklyData: this.data.weeklyData,
      trendData: this.data.trendData,
      metrics: this.data.metrics,
      salesRanking: this.data.salesRanking,
      maxRankQty: this.data.maxRankQty,
      wasteList: this.data.wasteList,
      aiSummary: this.data.aiSummary,
      actionItems: this.data.actionItems,
      healthLevel: this.data.healthLevel,
      rangeLabel: this.data.rangeLabel,
      hasData: this.data.hasData,
      errorText: this.data.errorText,
      showAdoption: this.data.showAdoption,
      adoptionAdopted: this.data.adoptionAdopted,
      adoptionTotal: this.data.adoptionTotal,
      adoptionRate: this.data.adoptionRate,
    };

    var cached = this._tabCache[tab];
    if (cached) {
      // 有缓存：直接恢复（合并为一次 setData 避免批处理丢弃 activeTab）
      this.setData({
        activeTab: tab,
        hasData: cached.hasData,
        loading: false,
        dailyData: cached.dailyData,
        weeklyData: cached.weeklyData,
        trendData: cached.trendData,
        metrics: cached.metrics,
        salesRanking: cached.salesRanking,
        maxRankQty: cached.maxRankQty,
        wasteList: cached.wasteList,
        aiSummary: cached.aiSummary,
        actionItems: cached.actionItems,
        healthLevel: cached.healthLevel,
        rangeLabel: cached.rangeLabel,
        errorText: cached.errorText || '',
        showAdoption: cached.showAdoption,
        adoptionAdopted: cached.adoptionAdopted,
        adoptionTotal: cached.adoptionTotal,
        adoptionRate: cached.adoptionRate,
      });
      this._renderDerived();
    } else {
      // 无缓存：发起请求（合并 activeTab + loading 为一次 setData）
      this.setData({ activeTab: tab, loading: true, hasData: false });
      this.loadReport(tab);
    }
  },

  // ── 数据加载 ─────────────────────────────────────────

  /**
   * @param {string}   tab      - 'daily' | 'weekly' | 'monthly'
   * @param {function} onSuccess
   * @param {function} onError
   */
  loadReport: function (tab, onSuccess, onError) {
    var self = this;
    var tabName = tab || this.data.activeTab || 'daily';
    this._reportRequestSeq = (this._reportRequestSeq || 0) + 1;
    var requestSeq = this._reportRequestSeq;
    var isStale = function () { return requestSeq !== self._reportRequestSeq || self.data.activeTab !== tabName; };

    // 合并 activeTab + loading 为一次 setData
    this.setData({ activeTab: tabName, loading: true, hasData: false, errorText: '' });

    // app.request 依赖 app 作为 this，不能脱离对象直接调用。
    var req = function (options) { return app.request(options); };

    if (tabName === 'daily') {
      // 今日报告 + 近7日趋势
      Promise.all([
        req({ url: '/reports/daily' }).catch(function () { return null; }),
        req({ url: '/reports/trends?days=7' }).catch(function () { return null; }),
      ]).then(function (results) {
        if (isStale()) return;
        var daily = results[0];
        var trends = results[1] || [];
        if (daily && !results[1]) wx.showToast({ title: '趋势数据加载失败，已显示核心指标', icon: 'none' });

        if (!daily) {
          self.setData({
            loading: false,
            hasData: false,
            trendData: trends,
            rangeLabel: '今日经营报告',
            errorText: '今日报告加载失败，请下拉重试',
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
    } else if (tabName === 'weekly') {
      // 周报 + 近7日趋势
      Promise.all([
        req({ url: '/reports/weekly' }).catch(function () { return null; }),
        req({ url: '/reports/trends?days=7' }).catch(function () { return null; }),
      ]).then(function (results) {
        if (isStale()) return;
        var weekly = results[0];
        var trends = weekly && weekly.daily_trends ? weekly.daily_trends : (results[1] || []);
        if (weekly && !weekly.daily_trends && !results[1]) wx.showToast({ title: '趋势数据加载失败，已显示核心指标', icon: 'none' });

        if (!weekly) {
          self.setData({
            loading: false,
            hasData: false,
            trendData: trends,
            rangeLabel: '近7日经营报告',
            errorText: '近7日报告加载失败，请下拉重试',
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
      // 近30日月报：调用后端 /reports/monthly 端点
      req({ url: '/reports/monthly' })
        .catch(function () { return null; })
        .then(function (monthly) {
          if (isStale()) return;
          if (!monthly) {
            self.setData({
              loading: false,
              hasData: false,
              trendData: [],
              rangeLabel: '近30日经营报告',
              errorText: '近30日报告加载失败，请下拉重试',
            });
            self._renderDerived();
            if (onError) onError();
            return;
          }

          var trends = (monthly.daily_trends && monthly.daily_trends.length)
            ? monthly.daily_trends
            : [];

          self.setData({
            weeklyData: monthly,
            trendData: trends,
            rangeLabel: '近30日经营报告 · ' + (monthly.period || ''),
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
    // 用 nextTick 确保 DOM 更新后再绘制 Canvas(替代固定延时,避免竞态)
    wx.nextTick(function () {
      self.drawTrendChart();
    });
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
        profitDisplay: this._money(d.estimated_gross_profit != null ? d.estimated_gross_profit : d.profit),
        wasteDisplay: this._money(d.waste_amount || 0),
        revenueChangePct: changePct,
        revenueChangeAbs: this._pct(changePct) + (changeAbs !== 0 ? ' ¥' + this._money(Math.abs(changeAbs)) : ''),
        profitMargin: d.revenue > 0
          ? this._pct((Number(d.estimated_gross_profit != null ? d.estimated_gross_profit : d.profit || 0) / Number(d.revenue) * 100)) : null,
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
        costDisplay: this._money(w.week_purchase_cost != null ? w.week_purchase_cost : Number(w.week_revenue || 0) - Number(w.week_profit || 0)),
        profitDisplay: this._money(w.week_gross_profit != null ? w.week_gross_profit : w.week_profit),
        wasteDisplay: this._money(this._sumWasteAmount(w.waste_ranking || [])),
        revenueChangePct: changePct,
        revenueChangeAbs: this._pct(changePct),
        profitMargin: (w.week_revenue > 0)
          ? this._pct((Number(w.week_gross_profit != null ? w.week_gross_profit : w.week_profit || 0) / Number(w.week_revenue) * 100)) : null,
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
    Chart.initCanvas(this, '#trendCanvas').then(function (c) {
      if (!c) return;
      self.setData({ canvasWidth: c.width, canvasHeight: c.height });
      Chart.drawLineChart(c.ctx, c.width, c.height, data, {
        series: [
          { key: 'revenue', color: '#2f9e6e', axis: 'left' },
          { key: 'profit', color: '#e8552f', axis: 'left' },
          { key: 'customer_price', color: '#2E7DD1', axis: 'right' },
        ],
        fillArea: { key: 'revenue', gradientFrom: 'rgba(47,158,110,.22)', gradientTo: 'rgba(47,158,110,0)' },
      });
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
