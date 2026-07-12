/**
 * AI参谋页面 — 经营建议 + 沙盘模拟 (v2.2 生鲜元气)
 * 新增：时段皮肤 / 小智主动推送 / 打字中流式口吻；保留建议与沙盘逻辑
 */
var app = getApp();

Page({
  data: {
    skin: 'noon',
    recommendations: [],
    recommendationIds: [],
    envSummary: null,
    activePush: [
      { id: 'p1', icon: 'bulb', tone: 'warn', title: '黄瓜临期 2 斤，今晚清货', desc: '损耗风险升高，建议 8 折带单', cta: '改价', doneText: '已改价', adopted: false, act: 'price' },
      { id: 'p2', icon: 'calendar', tone: 'info', title: '明日小雨，叶菜走量放缓', desc: '少进 15% 叶菜，转多备耐储根茎', cta: '记下了', doneText: '已记下', adopted: false, act: 'note' },
    ],
    saysText: '',

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

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
  },

  // ── 每日建议 ───────────────────────────────
  loadAdvice: function () {
    var self = this;
    var mid = app.getMerchantId();

    app.request({ url: '/advice/daily', data: { merchant_id: mid } })
      .then(function (d) {
        self.setData({
          recommendations: d.recommendations || [],
          recommendationIds: d.recommendation_ids || [],
          envSummary: d.env_summary || null,
        });
        // 小智开口：流式街坊口吻
        var name = (app.globalData.merchantName || '老板').replace(/摊$/, '');
        self.saysReply(name + '，我帮你盘了一下，今天这 ' + ((d.recommendations || []).length || 3) + ' 件事最要紧 👇');
      })
      .catch(function () { wx.showToast({ title: '加载建议失败', icon: 'none' }); });
  },

  // ── 流式口吻 ───────────────────────────────
  saysReply: function (text) {
    var self = this;
    if (app.globalData.reduceMotion) { this.setData({ saysText: text }); return; }
    this.setData({ saysText: '' });
    var i = 0;
    var t = setInterval(function () {
      i++;
      self.setData({ saysText: text.slice(0, i) });
      if (i >= text.length) {
        clearInterval(t);
        setTimeout(function () { self.setData({ saysText: '' }); }, 2600);
      }
    }, 42);
  },

  // ── 采纳小智主动推送（跨屏联动）──────────
  adoptPush: function (e) {
    var id = e.currentTarget.dataset.id;
    var list = this.data.activePush.slice();
    var hit = null;
    for (var i = 0; i < list.length; i++) { if (list[i].id === id) { hit = list[i]; list[i].adopted = true; break; } }
    this.setData({ activePush: list });

    if (hit && hit.act === 'price') {
      wx.showToast({ title: '已改价，记得同步价签', icon: 'none' });
    } else {
      wx.showToast({ title: '已记下，今晚盘货时提醒你', icon: 'none' });
    }
  },

  // ── 加入采购清单 ───────────────────────────
  addToPurchase: function () {
    var self = this;
    var mid = app.getMerchantId();
    var recIds = this.data.recommendationIds;

    if (!recIds || recIds.length === 0) { wx.showToast({ title: '暂无可用的建议', icon: 'none' }); return; }

    app.request({ url: '/purchase/from-advice', method: 'POST', data: { merchant_id: mid, recommendation_ids: recIds } })
      .then(function (res) {
        // 同时写入本地采购草稿，采购页可直接看到
        var draft = app.globalData.purchaseDraft || [];
        (res.items || []).forEach(function (it) { draft.push({ name: it.name || it.product_name, qty: it.qty || 10, unit: '斤', from: '参谋建议' }); });
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
    var field = e.detail.field; var val = e.detail.value;
    var key = 'sim' + field.charAt(0).toUpperCase() + field.slice(1);
    var patch = {}; patch[key] = val; this.setData(patch);
  },
  onSimProduct: function (e) { this.setData({ simProductIndex: e.detail.value }); },
  runSimulation: function () {
    var self = this;
    var mid = app.getMerchantId();
    var rec = this.data.recommendations[this.data.simProductIndex];
    var pid = rec ? rec.product_id : 1;
    this.setData({ simLoading: true });
    app.request({ url: '/simulate/what-if', method: 'POST', data: { merchant_id: mid, product_id: pid, scenario: { purchase_qty: this.data.simPurchaseQty, unit_cost: this.data.simUnitCost, unit_price: this.data.simUnitPrice } } })
      .then(function (res) { self.setData({ simResult: res, simLoading: false }); })
      .catch(function () { self.setData({ simLoading: false }); wx.showToast({ title: '模拟失败', icon: 'none' }); });
  },
});
