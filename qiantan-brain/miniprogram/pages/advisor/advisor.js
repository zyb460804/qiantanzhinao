/**
 * AI参谋页面 — 经营建议 + 沙盘模拟 (v3.0 数据驱动)
 * 主动推送不再硬编码 → 基于真实数据动态生成。
 */
var app = getApp();
var streamText = require('../../utils/stream-text').streamText;

Page({
  data: {
    skin: 'noon',
    recommendations: [],
    recommendationIds: [],
    envSummary: null,
    activePush: [],          // 数据驱动, 每次 loadAdvice 重建
    pushLoading: true,
    saysText: '',
    envError: false,        // 环境条 API 失败时的降级标记
    feedbackMap: {},        // { recommendation_id: 'helpful' | 'not_helpful' }

    // 沙盘模拟
    showSim: false,
    simProducts: [],
    simProductIndex: 0,
    simPurchaseQty: 50,
    simUnitCost: 0.5,
    simUnitPrice: 2.0,
    simResult: null,
    simLoading: false,
  },

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this.loadAdvice();
  },

  onHide: function () { this._clearSaysTimer(); },

  _clearSaysTimer: function () {
    // streamText 返回 {cancel: function} 对象,不是 timer id,必须调 .cancel()
    if (this._saysTimerId && typeof this._saysTimerId.cancel === 'function') {
      this._saysTimerId.cancel();
    }
    this._saysTimerId = null;
    if (this._saysDoneTimerId) { clearTimeout(this._saysDoneTimerId); this._saysDoneTimerId = null; }
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
  },

  // ── 每日建议 + 主动推送 (数据驱动) ────────
  loadAdvice: function () {
    var self = this;
    this.setData({ pushLoading: true });

    // 并行获取多源数据构建推送卡片
    Promise.all([
      app.request({ url: '/advice/daily' }).catch(function () { return null; }),
      app.request({ url: '/twin/dashboard' }).catch(function () { return null; }),
      app.request({ url: '/env/today?city=%E4%B8%8A%E6%B5%B7' }).catch(function () { return null; }),
      app.request({ url: '/voice/today-count' }).catch(function () { return null; }),
    ]).then(function (results) {
      var advice = results[0];
      var dashboard = results[1];
      var weather = results[2];
      var voiceCount = (results[3] && results[3].today_count) || 0;

      var recs = advice ? (advice.recommendations || []) : [];
      var ids = advice ? (advice.recommendation_ids || []) : [];
      var env = advice ? (advice.env_summary || null) : null;

      // 环境条兜底
      var envError = !weather;
      if (envError && !env) {
        env = { temp_high: '--', rainfall_prob: 0, is_weekend: false };
      }

      // 数据驱动的主动推送
      var pushCards = self._buildPushCards(dashboard, weather, voiceCount);

      self.setData({
        recommendations: recs,
        recommendationIds: ids,
        envSummary: env,
        envError: envError,
        activePush: pushCards,
        pushLoading: false,
      });

      // 小智开口
      var name = (app.globalData.merchantName || '老板').replace(/摊$/, '');
      var taskCount = pushCards.length > 0 ? pushCards.length : (recs.length || 0);
      self.saysReply(name + '，我帮你盘了一下，今天这 ' + taskCount + ' 件事最要紧');
    }).catch(function () {
      self.setData({ pushLoading: false });
      app.logError('advisor/loadAdvice', '加载建议失败', { silent: true });
      wx.showToast({ title: '加载建议失败', icon: 'none' });
    });
  },

  // ── 推送规则引擎 (前端) ────────────────────
  _buildPushCards: function (dashboard, weather, voiceCount) {
    var cards = [];
    if (!dashboard) return [];

    // 规则1: 临期提醒
    var expiring = Number(dashboard.expiring_count) || 0;
    if (expiring > 0) {
      var expText = '有 ' + expiring + ' 件商品临期，建议尽快处理';
      cards.push({
        id: 'expiry',
        icon: 'bulb',
        tone: 'warn',
        title: expText,
        desc: '损耗风险升高，先查看临期商品再决定促销或报损',
        cta: '查看库存',
        route: 'inventory',
      });
    }

    // 规则2: 天气预警
    if (weather) {
      var rain = Number(weather.rainfall_prob) || 0;
      if (rain > 60) {
        cards.push({
          id: 'weather',
          icon: 'calendar',
          tone: 'info',
          title: '明日降雨概率 ' + rain + '%，叶菜走量放缓',
          desc: '少进 15% 叶菜，转多备耐储根茎类',
          cta: '调整进货',
          route: 'sandbox',
        });
      }
    }

    // 规则3: 今日未记账
    if (voiceCount === 0) {
      cards.push({
        id: 'no_record',
        icon: 'mic',
        tone: 'warn',
        title: '今天还没有经营流水，别忘了记一笔',
        desc: '进货、销售、损耗记完，利润和库存才会准确',
        cta: '去记账',
        route: 'voice',
      });
    }

    // 规则4: 库存不足
    var riskScore = Number(dashboard.risk_score) || 0;
    if (riskScore >= 60) {
      cards.push({
        id: 'risk',
        icon: 'bulb',
        tone: 'warn',
        title: '经营风险分偏高 (' + riskScore + '/100)',
        desc: '打开经营镜像查看风险来源：库存、现金流还是客流波动',
        cta: '看详情',
        route: 'dashboard',
      });
    }

    return cards.slice(0, 3);
  },

  // ── 流式口吻 ───────────────────────────────
  saysReply: function (text) {
    var self = this;
    this._clearSaysTimer();
    this._saysTimerId = streamText(text, function (display) {
      self.setData({ saysText: display });
    });
  },

  // ── 推送卡片点击 → 跳转对应页面 ─────────────
  handlePushTap: function (e) {
    var route = e.currentTarget.dataset.route;
    if (!route) return;
    if (route === 'voice' || route === 'inventory' || route === 'advisor') {
      wx.switchTab({ url: '/pages/' + route + '/' + route });
    } else {
      wx.navigateTo({ url: '/pages/' + route + '/' + route });
    }
  },

  // ── 建议反馈 ───────────────────────────────
  onFeedback: function (e) {
    var recId = e.currentTarget.dataset.id;
    var type = e.currentTarget.dataset.type;
    if (!recId) return;

    var fbMap = this.data.feedbackMap || {};
    fbMap[recId] = type;
    this.setData({ feedbackMap: fbMap });

    // 反馈到后端
    app.request({
      url: '/behavior/feedback',
      method: 'POST',
      data: {
        recommendation_id: recId,
        was_adopted: type === 'helpful',
      },
    }).catch(function () {});
  },

  // ── 加入采购清单 ───────────────────────────
  addToPurchase: function () {
    var self = this;
    var recIds = this.data.recommendationIds;

    if (!recIds || recIds.length === 0) { wx.showToast({ title: '暂无可用的建议', icon: 'none' }); return; }

    app.request({ url: '/purchase/from-advice', method: 'POST', data: { recommendation_ids: recIds } })
      .then(function (res) {
        var draft = app.globalData.purchaseDraft || [];
        (res.items || []).forEach(function (it) {
          // 默认采购量从建议中推断，而非硬编码 10
          var qty = it.suggested_qty || it.recommended_qty || (it.qty ? it.qty * 0.8 : 5);
          qty = Math.round(qty);
          draft.push({ name: it.name || it.product_name, qty: qty, unit: '斤', from: '参谋建议' });
        });
        app.globalData.purchaseDraft = draft;
        wx.setStorageSync('purchaseDraft', draft);

        wx.showModal({
          title: '采购清单已生成', content: '共' + res.item_count + '项建议。是否前往采购清单？',
          confirmText: '去查看', cancelText: '稍后',
          success: function (r) { if (r.confirm) wx.navigateTo({ url: '/pages/purchase/purchase' }); },
        });
      }).catch(function () {});
  },

  // ── 沙盘模拟 ───────────────────────────────
  toggleSim: function () {
    if (!this.data.showSim) {
      var recs = this.data.recommendations;
      if (recs.length > 0) this.setData({ simProducts: recs.map(function (r) { return r.product_name; }), simProductIndex: 0 });
    }
    this.setData({ showSim: !this.data.showSim, simResult: null });
  },
  onSimField: function (e) {
    var field = e.detail.field;
    var val = e.detail.value;
    var key = 'sim' + field.charAt(0).toUpperCase() + field.slice(1);
    var patch = {};
    patch[key] = val;
    this.setData(patch);
  },
  onSimProduct: function (e) { this.setData({ simProductIndex: e.detail.value }); },
  runSimulation: function () {
    var self = this;
    var rec = this.data.recommendations[this.data.simProductIndex];
    var pid = rec ? rec.product_id : 1;
    this.setData({ simLoading: true });
    app.request({ url: '/simulate/what-if', method: 'POST', data: { product_id: pid, scenario: { purchase_qty: this.data.simPurchaseQty, unit_cost: this.data.simUnitCost, unit_price: this.data.simUnitPrice } } })
      .then(function (res) { self.setData({ simResult: res, simLoading: false }); })
      .catch(function () { self.setData({ simLoading: false }); wx.showToast({ title: '模拟失败', icon: 'none' }); });
  },
});
