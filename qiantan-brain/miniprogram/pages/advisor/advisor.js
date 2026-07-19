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

    // AI 可执行动作
    aiActions: [],
    aiActionsLoading: false,
    aiHistory: [],
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
      app.request({ url: '/env/today', data: { city: app.getCity() } }).catch(function () { return null; }),
      app.request({ url: '/voice/today-count' }).catch(function () { return null; }),
      app.request({ url: '/ai-actions/pending' }).catch(function () { return null; }),
    ]).then(function (results) {
      var advice = results[0];
      var dashboard = results[1];
      var weather = results[2];
      var voiceCount = (results[3] && results[3].today_count) || 0;
      var pendingActions = results[4] || [];

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

      // 美化 AI 动作卡片
      var aiCards = (pendingActions || []).map(function (a) {
        return self._beautifyAiAction(a);
      });

      self.setData({
        recommendations: recs,
        recommendationIds: ids,
        envSummary: env,
        envError: envError,
        activePush: pushCards,
        pushLoading: false,
        aiActions: aiCards,
        aiActionsLoading: false,
      });

      // 小智开口
      var name = (app.globalData.merchantName || '老板').replace(/摊$/, '');
      var taskCount = pushCards.length + aiCards.length;
      if (taskCount === 0) taskCount = recs.length || 0;
      self.saysReply(name + '，我帮你盘了一下，今天这 ' + taskCount + ' 件事最要紧');

      // 加载效果复盘（不阻塞主流程）
      self.loadAiHistory();
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

  // ── 效果复盘：加载已执行的 AI 动作历史 ────────
  loadAiHistory: function () {
    var self = this;
    app.request({ url: '/ai-actions/history', data: { limit: 5 } }).then(function (data) {
      var items = (data || []).filter(function (a) { return a.status !== 'pending'; });
      var history = items.slice(0, 5).map(function (a) {
        var typeMap = {
          price: '改价', purchase: '采购', clearance: '清货', lock_batch: '锁定',
        };
        var label = typeMap[a.action_type] || a.action_type;
        var statusText = a.status === 'executed' ? '已执行' : a.status === 'rejected' ? '已忽略' : a.status === 'failed' ? '失败' : a.status;
        var resultText = '';
        if (a.result) {
          if (a.result.list_id) resultText = '生成采购单 #' + a.result.item_count + '项';
          else if (a.result.updated) resultText = '更新' + a.result.updated + '个SKU';
          else if (a.result.batch_id) resultText = '批次已锁定';
          else if (a.result.error) resultText = '错误: ' + a.result.error.slice(0, 30);
        }
        return {
          id: a.id,
          typeLabel: label,
          title: a.title || '',
          statusText: statusText,
          statusClass: a.status,
          resultText: resultText,
          executedAt: a.executed_at ? a.executed_at.slice(5, 16).replace('T', ' ') : '',
        };
      });
      self.setData({ aiHistory: history });
    }).catch(function () { /* 静默处理 */ });
  },

  // ── AI 动作卡片美化 ──────────────────────
  _beautifyAiAction: function (a) {
    var typeMap = {
      price: { label: '改价', icon: 'tag', tone: 'info' },
      purchase: { label: '采购', icon: 'cart', tone: 'green' },
      clearance: { label: '清货', icon: 'bulb', tone: 'warn' },
      lock_batch: { label: '锁定', icon: 'lock', tone: 'warn' },
    };
    var meta = typeMap[a.action_type] || { label: a.action_type, icon: 'info', tone: 'info' };
    var payload = a.payload || {};

    // 根据动作类型生成摘要描述
    var desc = '';
    if (a.action_type === 'price' && payload.sku_name) {
      desc = payload.sku_name + '：¥' + (payload.old_price || '?') + ' → ¥' + payload.new_price;
    } else if (a.action_type === 'purchase') {
      var cnt = payload.items ? payload.items.length : 0;
      desc = '采购' + cnt + '项商品，预估¥' + (payload.total_cost || '?');
    } else if (a.action_type === 'clearance') {
      var n = payload.skus ? payload.skus.length : 0;
      desc = '临期清货，共' + n + '个SKU降价处理';
    } else if (a.action_type === 'lock_batch') {
      desc = '锁定批次 ' + (payload.batch_no || '') + '：' + (payload.reason || '食品安全风险');
    } else {
      desc = JSON.stringify(payload).slice(0, 60);
    }

    return {
      id: a.id,
      actionType: a.action_type,
      typeLabel: meta.label,
      icon: meta.icon,
      tone: meta.tone,
      title: a.title || 'AI建议动作',
      desc: desc,
      createdAt: a.created_at || '',
    };
  },

  // ── 执行 AI 动作 ──────────────────────────
  executeAiAction: function (e) {
    var self = this;
    var actionId = e.currentTarget.dataset.id;
    if (!actionId) return;

    wx.showModal({
      title: '确认执行',
      content: '执行后将立即生效，确定吗？',
      confirmText: '执行',
      cancelText: '取消',
      success: function (r) {
        if (!r.confirm) return;
        wx.showLoading({ title: '执行中…' });
        app.request({
          url: '/ai-actions/' + actionId + '/execute',
          method: 'POST',
          data: { status: 'executed' },
        }).then(function (res) {
          wx.hideLoading();
          // 从列表中移除已执行的动作
          var remaining = self.data.aiActions.filter(function (a) { return a.id !== actionId; });
          self.setData({ aiActions: remaining });
          wx.showToast({ title: res.message || '已执行', icon: 'success' });
        }).catch(function (err) {
          wx.hideLoading();
          wx.showToast({ title: '执行失败', icon: 'none' });
          app.logError('advisor/executeAiAction', '执行AI动作失败', { silent: true });
        });
      },
    });
  },

  // ── 拒绝 AI 动作 ──────────────────────────
  rejectAiAction: function (e) {
    var self = this;
    var actionId = e.currentTarget.dataset.id;
    if (!actionId) return;

    app.request({
      url: '/ai-actions/' + actionId + '/execute',
      method: 'POST',
      data: { status: 'rejected' },
    }).then(function () {
      var remaining = self.data.aiActions.filter(function (a) { return a.id !== actionId; });
      self.setData({ aiActions: remaining });
      wx.showToast({ title: '已忽略', icon: 'none' });
    }).catch(function () {
      wx.showToast({ title: '操作失败', icon: 'none' });
    });
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

  // ── 跳转决策沙盘（试算统一在 sandbox 页） ───────────
  goSandbox: function () {
    wx.navigateTo({ url: '/pages/sandbox/sandbox' });
  },
});
