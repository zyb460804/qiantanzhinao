var app = getApp();
var offlineSync = require('../../utils/offline-sync');

// 金额四舍五入到分。加 Number.EPSILON 规避 JS 浮点边界值（1.005、2.675）被错位。
function money(value) {
  var n = Number(value || 0);
  return Math.round((n + Number.EPSILON) * 100) / 100;
}
function paymentLabel(method) {
  return { cash: '现金', wechat: '微信', alipay: '支付宝', card: '银行卡', credit: '赊账' }[method] || method;
}
// 以「分」为整数单位比较金额，规避 0.01 容差累积导致的对账漂移。
function centEquals(a, b) { return Math.round((Number(a || 0) + Number.EPSILON) * 100) === Math.round((Number(b || 0) + Number.EPSILON) * 100); }
// 取 CST(UTC+8) 日的 yyyy-mm-dd。后端用 UTC 存储但日结按本地日界切分。
function cstToday() {
  var now = new Date();
  var cst = new Date(now.getTime() + 8 * 3600 * 1000);
  return cst.getUTCFullYear() + '-' + String(cst.getUTCMonth() + 1).padStart(2, '0') + '-' + String(cst.getUTCDate()).padStart(2, '0');
}

Page({
  // 阻止弹窗/卡片内部 tap 冒泡到外层关闭区域。
  stopMaskTap: function () {},
  data: {
    skin: 'noon', loading: true, submitting: false,
    products: [], cart: [], grossAmount: 0, discountAmount: 0, payableAmount: 0,
    // 支付
    paymentMethod: 'wechat',
    multiPay: false,  // 组合支付开关
    paySplit: {},     // {wechat: 0, cash: 0, alipay: 0, credit: 0}
    paymentMethods: [
      { key: 'wechat', label: '微信' }, { key: 'cash', label: '现金' },
      { key: 'alipay', label: '支付宝' }, { key: 'credit', label: '赊账' }
    ],
    // 订单列表
    records: [], pendingCount: 0, settlement: null,
    // 挂单
    heldOrders: [], showHeld: false,
    // 退款弹窗
    showRefund: false, refundOrder: null, refundReason: '', refundReturnStock: true, refundSubmitting: false,
    refundPartial: false, refundItems: [], refundItemsLoading: false,
    // 网络/加载错误态（P2：网络/空态/错误反馈）
    loadError: false, networkOnline: true,
    // 赊账客户历史（P2：赊账客户名输入，最多 5 条，加「其他」共 6 条不超 wx.showActionSheet 上限）
    recentCustomers: []
  },

  onLoad: function () {
    // 页面级网络恢复监听（P2）：网络从断→通时，若停留在 pos 页则自动重放离线订单。
    var self = this;
    this._netListener = function (res) {
      self.setData({ networkOnline: res.isConnected });
      if (res.isConnected) {
        self.syncPendingOrders();
        if (self.data.loadError) self.loadData();
      }
    };
    wx.onNetworkStatusChange(this._netListener);
    // 初始网络态
    wx.getNetworkType({
      success: function (res) {
        var online = res.networkType && res.networkType !== 'none';
        self.setData({ networkOnline: online });
      }
    });
  },

  onUnload: function () {
    if (this._netListener) wx.offNetworkStatusChange(this._netListener);
    this._netListener = null;
  },

  onShow: function () {
    this.setData({ skin: app.resolveSkin() });
    this.loadData();
    this.loadRecentCustomers();   // P2：预加载赊账客户历史
    this.loadSettlement();        // P1：回显今日日结状态，避免误覆盖
    this.syncPendingOrders();
    this.loadHeldOrders();
  },

  // ==================== 数据加载 ====================

  loadData: function () {
    var self = this;
    this.setData({ loading: true, loadError: false });
    Promise.all([
      app.request({ url: '/inventory/current' }),
      app.request({ url: '/pos/orders?limit=10' })
    ]).then(function (res) {
      var products = (res[0] || []).filter(function (item) { return Number(item.current_qty) > 0; }).map(function (item) {
        var price = item.sale_price;
        if (price === null || price === undefined) price = item.default_sale_price;
        if (price === null || price === undefined) price = money(Number(item.avg_cost || 0) * 1.3);
        return Object.assign({}, item, {
          sale_price: money(price),
          price_is_promotion: item.promotion_price !== null && item.promotion_price !== undefined,
          price_is_estimated: (item.sale_price === null || item.sale_price === undefined) && !item.default_sale_price,
        });
      });
      self.setData({ products: products, records: self.mergePendingRecords(res[1] || []), loading: false, loadError: false });
    }).catch(function (err) {
      // P2：区分网络错误与业务错误，避免把「无网络」误显示为「暂无库存」。
      var isNet = err && err.type === 'network_error';
      self.setData({ loading: false, loadError: true, products: [], records: self.mergePendingRecords([]) });
      wx.showToast({ title: isNet ? '网络异常，请检查网络后重试' : '加载商品失败', icon: 'none' });
    });
  },

  loadHeldOrders: function () {
    var self = this;
    app.request({ url: '/pos/orders/held' }).then(function (data) {
      self.setData({ heldOrders: data || [] });
    }).catch(function (err) {
      // P2：不再静默吞掉，明确提示用户挂单加载失败。
      var isNet = err && err.type === 'network_error';
      wx.showToast({ title: isNet ? '挂单加载失败：网络异常' : '挂单加载失败', icon: 'none' });
      self.setData({ heldOrders: [] });
    });
  },

  // P1：回显今日日结状态。后端 GET /pos/daily-settlement/{date} 在未日结时返回 status=open 的实时统计，
  // 已日结时返回 status=closed。setData 后 WXML 按钮会切换为「重新日结」，避免误覆盖。
  loadSettlement: function () {
    var self = this;
    var today = cstToday();
    app.request({ url: '/pos/daily-settlement/' + today }).then(function (data) {
      if (!data) return;
      // 已关闭则回填；status=open 时不预填 settlement，避免按钮文案歧义。
      if (data.status === 'closed') {
        data.isBalanced = Math.abs(data.diff_amount || 0) < 0.005;
        self.setData({ settlement: data });
      } else {
        self.setData({ settlement: null });
      }
    }).catch(function () {
      // 静默失败：日结回显是辅助提示，不应阻塞主流程。
    });
  },

  // P2：拉取最近 50 笔订单中的赊账客户名，去重后作为快速选择候选。
  loadRecentCustomers: function () {
    var self = this;
    app.request({ url: '/pos/orders?limit=50' }).then(function (data) {
      if (!data || !data.length) return;
      var seen = {};
      var names = [];
      data.forEach(function (o) {
        var n = (o.customer_name || '').trim();
        if (n && !seen[n]) { seen[n] = 1; names.push(n); }
      });
      self.setData({ recentCustomers: names.slice(0, 5) });
    }).catch(function () {});
  },

  mergePendingRecords: function (serverRecords) {
    var pending = wx.getStorageSync('pendingPosOrders') || [];
    this.setData({ pendingCount: pending.filter(function (x) { return x.status === 'pending'; }).length });
    var local = pending.map(function (x) {
      return { order_id: x.client_id, order_no: '本地-' + x.client_id.slice(0, 8), total_amount: x.payable, status: x.status, created_at: x.created_at, payment_method: x.payment_method };
    });
    return local.concat(serverRecords);
  },

  // ==================== 购物车 ====================

  addProduct: function (e) {
    var index = Number(e.currentTarget.dataset.index);
    var product = this.data.products[index];
    if (!product) return;
    var self = this;
    if (Number(product.sale_price) <= 0) {
      wx.showModal({ title: '设置售价', editable: true, placeholderText: '请输入每' + product.unit + '售价', success: function (r) {
        var price = Number(r.content);
        if (r.confirm && price > 0) {
          // P2：弹框输入的售价同步写回 SKU 档案，避免下次加购重复输入。
          self._persistSkuPrice(product, price);
          self.addToCart(product, price);
        } else if (r.confirm) wx.showToast({ title: '售价必须大于0', icon: 'none' });
      }});
      return;
    }
    this.addToCart(product, product.sale_price);
  },

  // P2：调用已有 PUT /catalog/skus/{id} 写回 default_sale_price。
  // 失败不阻塞加购（仅本次有效），由 toast 提示。
  _persistSkuPrice: function (product, price) {
    if (!product || !product.sku_id) return;
    app.request({
      url: '/catalog/skus/' + product.sku_id, method: 'PUT', data: { default_sale_price: price }
    }).then(function () {
      wx.showToast({ title: '售价已更新档案', icon: 'none' });
    }).catch(function () {
      wx.showToast({ title: '售价仅本次有效，档案未更新', icon: 'none' });
    });
  },

  addToCart: function (product, price) {
    var cart = this.data.cart.slice();
    var found = -1;
    for (var i = 0; i < cart.length; i++) if (cart[i].product_id === product.product_id) found = i;
    var stock = Number(product.current_qty);
    if (found >= 0) {
      // P1：放宽余量<1时的加购拦截，菜摊/肉摊 0.3/0.5 斤仍可销售。步长仍按 1 斤，
      // 超过 max_qty 由 editQty 输入精确称重值。
      var nextQty = money(cart[found].quantity + 1);
      if (nextQty > Number(cart[found].max_qty) + Number.EPSILON) { wx.showToast({ title: '已达库存上限 ' + cart[found].max_qty + cart[found].unit, icon: 'none' }); return; }
      cart[found].quantity = nextQty;
    } else {
      // P1：current_qty <= 0 才视为库存不足；余量不足 1 斤时，默认加购全部余量。
      if (stock <= 0) { wx.showToast({ title: '库存不足', icon: 'none' }); return; }
      var initQty = stock < 1 ? money(stock) : 1;
      cart.push({ product_id: product.product_id, sku_id: product.sku_id, product_name: product.sku_name || product.product_name, quantity: initQty, max_qty: product.current_qty, unit: product.unit, unit_price: money(price) });
    }
    this.updateCart(cart);
  },

  changeQty: function (e) {
    var index = Number(e.currentTarget.dataset.index);
    var delta = Number(e.currentTarget.dataset.delta);
    var cart = this.data.cart.slice();
    if (!cart[index]) return;
    var nextQty = money(cart[index].quantity + delta);
    if (nextQty > Number(cart[index].max_qty) + Number.EPSILON) { wx.showToast({ title: '不能超过当前库存 ' + cart[index].max_qty + cart[index].unit, icon: 'none' }); return; }
    cart[index].quantity = nextQty;
    if (cart[index].quantity <= 0) cart.splice(index, 1);
    this.updateCart(cart);
  },

  editQty: function (e) {
    var index = Number(e.currentTarget.dataset.index);
    var item = this.data.cart[index];
    var self = this;
    if (!item) return;
    wx.showModal({ title: item.product_name + '称重', editable: true, placeholderText: '输入数量（' + item.unit + '）', content: String(item.quantity), success: function (r) {
      var qty = Number(r.content);
      if (r.confirm && qty > Number(item.max_qty)) { wx.showToast({ title: '不能超过当前库存 ' + item.max_qty + item.unit, icon: 'none' }); return; }
      if (r.confirm && qty > 0) { var cart = self.data.cart.slice(); cart[index].quantity = money(qty); self.updateCart(cart); }
    }});
  },

  updateCart: function (cart) {
    var gross = 0;
    cart.forEach(function (item) { item.line_total = money(item.quantity * item.unit_price); gross += item.line_total; });
    var discount = Math.min(this.data.discountAmount, gross);
    this.setData({ cart: cart, grossAmount: money(gross), discountAmount: money(discount), payableAmount: money(gross - discount) });
    this._updatePaySplit();
  },

  editDiscount: function () {
    var self = this;
    wx.showModal({ title: '整单优惠', editable: true, content: String(this.data.discountAmount), placeholderText: '输入优惠金额', success: function (r) {
      var value = money(r.content);
      if (r.confirm && value >= 0 && value <= self.data.grossAmount) { self.setData({ discountAmount: value, payableAmount: money(self.data.grossAmount - value) }); self._updatePaySplit(); }
      else if (r.confirm) wx.showToast({ title: '优惠金额不合法', icon: 'none' });
    }});
  },

  // ==================== 支付 ====================

  toggleMultiPay: function () {
    var multi = !this.data.multiPay;
    this.setData({ multiPay: multi });
    if (multi) this._updatePaySplit();
  },

  _updatePaySplit: function () {
    if (!this.data.multiPay) return;
    var payable = this.data.payableAmount;
    var split = this.data.paySplit;
    var methods = ['wechat', 'cash', 'alipay', 'credit'];
    // Default: put everything on first non-credit method
    var assigned = 0;
    methods.forEach(function (m) { assigned += Number(split[m] || 0); });
    // P2：金额精度修复——使用整数分比较，去掉 0.01 容差，避免差异累积。
    if (!centEquals(assigned, payable)) {
      split = {};
      split['wechat'] = payable;
      this.setData({ paySplit: split });
    }
  },

  editPayAmount: function (e) {
    var method = e.currentTarget.dataset.method;
    var self = this;
    var current = this.data.paySplit[method] || 0;
    wx.showModal({ title: paymentLabel(method) + '金额', editable: true, content: String(current), placeholderText: '输入金额', success: function (r) {
      var val = money(r.content);
      if (r.confirm && val >= 0) { var split = Object.assign({}, self.data.paySplit); split[method] = val; self.setData({ paySplit: split }); }
    }});
  },

  fillRemaining: function (e) {
    var method = e.currentTarget.dataset.method;
    var payable = this.data.payableAmount;
    var split = Object.assign({}, this.data.paySplit);
    var methods = ['wechat', 'cash', 'alipay', 'credit'];
    var assigned = 0;
    methods.forEach(function (m) { if (m !== method) assigned += Number(split[m] || 0); });
    split[method] = Math.max(0, payable - assigned);
    this.setData({ paySplit: split });
  },

  selectPayment: function (e) { this.setData({ paymentMethod: e.currentTarget.dataset.method }); },

  // ==================== 结账 ====================

  checkout: function () {
    if (!this.data.cart.length || this.data.submitting) return;
    var self = this;
    var isCredit = this.data.multiPay
      ? (Number(this.data.paySplit.credit || 0) > 0)
      : this.data.paymentMethod === 'credit';
    if (isCredit) {
      // P2：先展示历史客户快捷选择，降低同名异写造成的对账噪音。
      this._promptCustomerName(function (name) { self.doCheckout(name); });
    } else {
      this.doCheckout('');
    }
  },

  // P2：客户名输入——优先用 ActionSheet 展示历史客户，选「其他」再走手输。
  _promptCustomerName: function (cb) {
    var recents = this.data.recentCustomers || [];
    var self = this;
    if (recents.length > 0) {
      wx.showActionSheet({
        itemList: recents.concat(['其他（手输）']),
        success: function (r) {
          if (r.tapIndex < recents.length) { cb(recents[r.tapIndex]); return; }
          // 选「其他」：进入手输流程
          self._promptManualCustomerName(cb);
        },
        fail: function () {
          // 用户取消 ActionSheet 时回退到手输，避免阻塞赊账开单。
          self._promptManualCustomerName(cb);
        }
      });
    } else {
      self._promptManualCustomerName(cb);
    }
  },

  _promptManualCustomerName: function (cb) {
    wx.showModal({
      title: '赊账客户', editable: true, placeholderText: '例如：张记饭店',
      success: function (r) {
        if (r.confirm && String(r.content || '').trim()) cb(String(r.content).trim());
      }
    });
  },

  doCheckout: function (customerName) {
    var self = this;
    var payable = this.data.payableAmount;

    if (this.data.multiPay) {
      // Validate combined payment total
      var split = this.data.paySplit;
      var totalSplit = money((Number(split.wechat) || 0) + (Number(split.cash) || 0) + (Number(split.alipay) || 0) + (Number(split.credit) || 0));
      // P2：金额精度——严格按整数分匹配，避免容差累积。
      if (!centEquals(totalSplit, payable)) {
        wx.showToast({ title: '组合支付合计 ¥' + totalSplit + ' ≠ 应收 ¥' + payable, icon: 'none', duration: 2500 });
        return;
      }
    }

    var order = {
      client_id: offlineSync.uuidv4(), created_at: new Date().toISOString(), status: 'pending', retries: 0,
      payment_method: this.data.multiPay ? 'cash' : this.data.paymentMethod,
      customer_name: customerName,
      discount_amount: this.data.discountAmount, gross: this.data.grossAmount, payable: payable,
      items: this.data.cart.map(function (item) { return { product_id: item.product_id, sku_id: item.sku_id || null, quantity: item.quantity, unit: item.unit, unit_price: item.unit_price }; })
    };

    // Build payment payload
    if (this.data.multiPay) {
      order.payments = [];
      var split = this.data.paySplit;
      ['wechat', 'cash', 'alipay', 'credit'].forEach(function (m) {
        var amt = Number(split[m] || 0);
        if (amt > 0) order.payments.push({ method: m, amount: amt });
      });
    }

    // Try sync first
    this.setData({ submitting: true });
    var payload = {
      client_id: order.client_id, payment_method: order.payment_method,
      customer_name: order.customer_name || null,
      discount_amount: order.discount_amount, items: order.items
    };
    if (order.payments) payload.payments = order.payments;

    app.request({ url: '/pos/orders', method: 'POST', data: payload }).then(function () {
      self.setData({ submitting: false, cart: [], grossAmount: 0, discountAmount: 0, payableAmount: 0, multiPay: false, paySplit: {} });
      wx.showToast({ title: '¥' + order.payable + ' 已入账', icon: 'none' });
      self.loadData();
    }).catch(function (err) {
      self.setData({ submitting: false });
      if (err && err.type === 'network_error') {
        // Offline: queue locally
        var pending = wx.getStorageSync('pendingPosOrders') || [];
        pending.unshift(order);
        wx.setStorageSync('pendingPosOrders', pending);
        self.setData({ cart: [], grossAmount: 0, discountAmount: 0, payableAmount: 0, multiPay: false, paySplit: {}, pendingCount: self.data.pendingCount + 1 });
        wx.showToast({ title: '离线保存，网络恢复后自动入账', icon: 'none' });
        self.loadData();
      } else {
        wx.showToast({ title: (err.body && err.body.detail) || '入账失败', icon: 'none' });
      }
    });
  },

  syncPendingOrders: function () {
    var self = this;
    if (this._syncingPos) return;
    var pending = wx.getStorageSync('pendingPosOrders') || [];
    var targets = pending.filter(function (x) { return x.status === 'pending'; });
    if (!targets.length) return;
    this._syncingPos = true;
    var chain = Promise.resolve();
    targets.forEach(function (order) {
      chain = chain.then(function () {
        var payload = {
          client_id: order.client_id, payment_method: order.payment_method, customer_name: order.customer_name || null,
          discount_amount: order.discount_amount, items: order.items
        };
        if (order.payments) payload.payments = order.payments;
        return app.request({ url: '/pos/orders', method: 'POST', data: payload }).then(function () {
          var list = wx.getStorageSync('pendingPosOrders') || [];
          list = list.filter(function (x) { return x.client_id !== order.client_id; });
          wx.setStorageSync('pendingPosOrders', list);
          wx.showToast({ title: '离线订单已入账 ¥' + order.payable, icon: 'none' });
        }).catch(function (err) {
          if (err && err.type !== 'network_error') {
            var list = wx.getStorageSync('pendingPosOrders') || [];
            list.forEach(function (x) { if (x.client_id === order.client_id) { x.status = 'failed'; x.error = (err.body && (err.body.detail || err.body.message)) || '入账失败'; } });
            wx.setStorageSync('pendingPosOrders', list);
          }
        });
      });
    });
    chain.then(function () { self._syncingPos = false; self.loadData(); }).catch(function () { self._syncingPos = false; });
  },

  retryFailed: function () {
    var list = wx.getStorageSync('pendingPosOrders') || [];
    list.forEach(function (x) { if (x.status === 'failed') x.status = 'pending'; });
    wx.setStorageSync('pendingPosOrders', list);
    this.syncPendingOrders();
  },

  // ==================== 挂单 ====================

  holdOrder: function () {
    if (this.data.submitting) return;
    if (!this.data.cart.length) { wx.showToast({ title: '购物车为空', icon: 'none' }); return; }
    var self = this;
    // P2：先选择挂单类型——「普通挂单」直接挂，「赊账挂单」需录入客户名。
    wx.showActionSheet({
      itemList: ['普通挂单', '赊账挂单（回头结账）'],
      success: function (r) {
        if (r.tapIndex === 0) { self._submitHold(null); return; }
        // 赊账挂单：复用历史客户快捷选择。
        self._promptCustomerName(function (name) { self._submitHold(name); });
      },
      fail: function () { /* 用户取消 */ }
    });
  },

  _submitHold: function (customerName) {
    var self = this;
    this.setData({ submitting: true });
    var payload = {
      items: this.data.cart.map(function (item) { return { product_id: item.product_id, sku_id: item.sku_id || null, quantity: item.quantity, unit: item.unit, unit_price: item.unit_price }; }),
      discount_amount: this.data.discountAmount
    };
    // P2：customer_name 走后端 HoldOrderRequest 字段，使赊账挂单可被识别。
    if (customerName) payload.customer_name = customerName;
    app.request({ url: '/pos/orders/hold', method: 'POST', data: payload }).then(function () {
      self.setData({ submitting: false, cart: [], grossAmount: 0, discountAmount: 0, payableAmount: 0 });
      wx.showToast({ title: customerName ? '赊账挂单已保存' : '订单已挂起', icon: 'none' });
      self.loadHeldOrders();
    }).catch(function (err) {
      self.setData({ submitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '挂单失败', icon: 'none' });
    });
  },

  toggleHeldOrders: function () {
    this.setData({ showHeld: !this.data.showHeld });
    if (this.data.showHeld) this.loadHeldOrders();
  },

  resumeHeld: function (e) {
    var orderId = e.currentTarget.dataset.id;
    var self = this;
    var order = this.data.heldOrders.find(function (o) { return o.order_id === orderId; });
    if (!order) return;
    var isCredit = order.customer_name && order.customer_name.length > 0;
    wx.showActionSheet({
      itemList: ['微信收款', '现金收款', '组合支付'].concat(isCredit ? ['继续赊账(' + order.customer_name + ')'] : []).concat(['取消挂单']),
      success: function (r) {
        var method = r.tapIndex === 0 ? 'wechat' : r.tapIndex === 1 ? 'cash' : r.tapIndex === 2 ? 'combined' : r.tapIndex === 3 && isCredit ? 'credit' : 'cancel';
        if (method === 'cancel') {
          app.request({ url: '/pos/orders/' + orderId, method: 'DELETE' }).then(function () {
            wx.showToast({ title: '挂单已取消', icon: 'none' });
            self.loadHeldOrders();
          });
          return;
        }
        if (method === 'combined') {
          self._resumeWithCombined(orderId, order);
          return;
        }
        self.setData({ submitting: true });
        app.request({ url: '/pos/orders/' + orderId + '/resume', method: 'POST', data: { payment_method: method } }).then(function () {
          self.setData({ submitting: false });
          wx.showToast({ title: '已取回收款 ¥' + order.total_amount, icon: 'none' });
          self.loadHeldOrders(); self.loadData();
        }).catch(function (err) {
          self.setData({ submitting: false });
          wx.showToast({ title: (err.body && err.body.detail) || '取单失败', icon: 'none' });
        });
      }
    });
  },

  _resumeWithCombined: function (orderId, order) {
    var self = this;
    var total = money(order.total_amount);
    var payments = [];
    var customerName = (order.customer_name || '').trim();
    var methods = [
      { key: 'wechat', label: '微信' }, { key: 'alipay', label: '支付宝' },
      { key: 'cash', label: '现金' }, { key: 'credit', label: '赊账' }
    ];

    // P2：递归选择支付方式 + 金额，直到剩余为 0 或用户取消。支持 alipay、三段及以上分摊。
    function pickNext(remaining) {
      if (centEquals(remaining, 0)) { submit(); return; }
      wx.showActionSheet({
        itemList: methods.map(function (m) { return m.label + '（剩 ¥' + remaining + '）'; }),
        success: function (r) {
          var method = methods[r.tapIndex].key;
          if (method === 'credit' && !customerName) {
            // 赊账需先录入客户名
            self._promptCustomerName(function (name) {
              customerName = name;
              promptAmount(method, remaining);
            });
          } else {
            promptAmount(method, remaining);
          }
        }
      });
    }

    function promptAmount(method, remaining) {
      wx.showModal({
        title: methods.find(function (m) { return m.key === method; }).label + '金额',
        editable: true, placeholderText: '最多 ¥' + remaining,
        success: function (r) {
          if (!r.confirm) return;
          var amt = money(r.content);
          if (!isFinite(Number(r.content)) || amt <= 0) { wx.showToast({ title: '金额必须大于0', icon: 'none' }); return; }
          if (amt > remaining + Number.EPSILON) { wx.showToast({ title: '金额超过剩余 ¥' + remaining, icon: 'none' }); return; }
          payments.push({ method: method, amount: amt });
          pickNext(money(remaining - amt));
        }
      });
    }

    function submit() {
      self.setData({ submitting: true });
      var data = { payments: payments };
      if (customerName) data.customer_name = customerName;
      app.request({ url: '/pos/orders/' + orderId + '/resume', method: 'POST', data: data }).then(function () {
        self.setData({ submitting: false });
        wx.showToast({ title: '已取回收款 ¥' + total, icon: 'none' });
        self.loadHeldOrders(); self.loadData();
      }).catch(function (err) {
        self.setData({ submitting: false });
        wx.showToast({ title: (err.body && err.body.detail) || '取单失败', icon: 'none' });
      });
    }

    pickNext(total);
  },

  // ==================== 退款 ====================

  openRefund: function (e) {
    var orderId = e.currentTarget.dataset.id;
    var order = this.data.records.find(function (r) { return r.order_id === orderId; });
    if (!order) return;
    if (order.status === 'held' || order.status === 'pending' || order.status === 'cancelled') {
      wx.showToast({ title: '当前状态不可退款', icon: 'none' }); return;
    }
    // P2：进入退款弹窗时立即置 loading，避免用户在没加载到明细时提交整单退款。
    this.setData({
      showRefund: true, refundOrder: order, refundReason: '', refundReturnStock: true,
      refundPartial: false, refundItems: [], refundItemsLoading: true
    });
    // Fetch order details for partial refund support
    var self = this;
    app.request({ url: '/pos/orders/' + orderId }).then(function (data) {
      if (!data || !data.items) {
        self.setData({ refundItemsLoading: false });
        wx.showToast({ title: '订单明细为空，无法部分退款', icon: 'none' });
        return;
      }
      var items = data.items.map(function (it) {
        return {
          item_id: it.item_id || it.id,
          product_name: it.product_name || ('商品' + it.product_id),
          quantity: Number(it.quantity) || 0,
          refund_qty: 0,
          unit_price: Number(it.unit_price) || 0,
          unit: it.unit || '斤',
        };
      });
      self.setData({ refundItems: items, refundItemsLoading: false });
    }).catch(function (err) {
      // P2：失败时明确提示并关闭 loading；避免 refundItems 仍为 [] 时用户误点整单退款。
      var isNet = err && err.type === 'network_error';
      self.setData({ refundItemsLoading: false });
      wx.showToast({ title: isNet ? '订单明细加载失败：网络异常' : '订单明细加载失败，请稍后重试', icon: 'none' });
    });
  },

  closeRefund: function () {
    this.setData({ showRefund: false, refundOrder: null, refundReason: '', refundReturnStock: true, refundPartial: false, refundItems: [] });
  },

  inputRefundReason: function (e) { this.setData({ refundReason: e.detail.value }); },
  toggleReturnStock: function () { this.setData({ refundReturnStock: !this.data.refundReturnStock }); },
  toggleRefundPartial: function () {
    this.setData({
      refundPartial: !this.data.refundPartial,
      refundItems: this.data.refundItems.map(function (it) { return Object.assign({}, it, { refund_qty: 0 }); }),
    });
  },
  editRefundQty: function (e) {
    var idx = e.currentTarget.dataset.idx;
    var num = parseFloat(e.detail.value) || 0;
    if (num < 0) num = 0;
    var items = this.data.refundItems.slice();
    if (items[idx]) {
      items[idx] = Object.assign({}, items[idx], { refund_qty: Math.min(num, items[idx].quantity) });
    }
    this.setData({ refundItems: items });
  },

  confirmRefund: function () {
    if (this.data.refundSubmitting) return;
    var self = this;
    var order = this.data.refundOrder;
    var reason = this.data.refundReason.trim();
    if (!reason) { wx.showToast({ title: '请填写退款原因', icon: 'none' }); return; }
    this.setData({ refundSubmitting: true });

    var payload = { reason: reason, return_to_stock: this.data.refundReturnStock };

    // Partial refund: send selected items
    if (this.data.refundPartial) {
      var items = (this.data.refundItems || []).filter(function (it) { return it.refund_qty > 0; });
      if (items.length === 0) {
        self.setData({ refundSubmitting: false });
        wx.showToast({ title: '请填写退款数量', icon: 'none' });
        return;
      }
      payload.items = items.map(function (it) {
        return { item_id: it.item_id, quantity: it.refund_qty, return_to_stock: self.data.refundReturnStock };
      });
    }

    app.request({ url: '/pos/orders/' + order.order_id + '/refund', method: 'POST', data: payload }).then(function (data) {
      self.setData({ refundSubmitting: false, showRefund: false, refundOrder: null, refundPartial: false, refundItems: [] });
      wx.showToast({ title: '已退款 ¥' + data.refunded_amount, icon: 'none' });
      self.loadData();
    }).catch(function (err) {
      self.setData({ refundSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '退款失败', icon: 'none' });
    });
  },

  // ==================== 日结 ====================

  closeDay: function () {
    var self = this;
    if (this.data.submitting) return;
    // P1：日结前提醒未同步离线单，避免漏单入账——离线订单不在对账范围内。
    if (this.data.pendingCount > 0) {
      wx.showModal({
        title: '存在未同步订单',
        content: '有 ' + this.data.pendingCount + ' 笔未同步订单，日结可能遗漏，确定继续？',
        success: function (r) {
          if (r.confirm) self._doCloseDay();
        }
      });
      return;
    }
    this._doCloseDay();
  },

  // P1：实际的日结调用——拆出以便 closeDay 在 pendingCount>0 时先走提醒流程。
  _doCloseDay: function () {
    var self = this;
    var today = cstToday();
    // P1：明确告知「日结后当天无法再开新订单」这一副作用，避免晚高峰前误点。
    var alreadyClosed = this.data.settlement && this.data.settlement.status === 'closed';
    wx.showModal({
      title: alreadyClosed ? '重新日结（覆盖）' : '确认日结',
      content: '将按销售、各渠道实收、采购付款、赊账余额生成对账结果。\n注意：日结关闭后，当天将无法再开新订单、退款或挂单取回（如需恢复请联系管理员）。',
      success: function (r) {
        if (!r.confirm) return;
        self.setData({ submitting: true });
        app.request({ url: '/pos/daily-settlement/' + today + '/close', method: 'POST' }).then(function (data) {
          // WXML 不支持调用 Math.abs,在 JS 中预计算布尔值再绑定
          var isBalanced = Math.abs(data.diff_amount || 0) < 0.005;
          data.isBalanced = isBalanced;
          self.setData({ settlement: data, submitting: false });
          wx.showToast({ title: isBalanced ? '日结完成，账目平衡' : '日结完成，存在差异', icon: 'none' });
        }).catch(function (err) {
          self.setData({ submitting: false });
          // P1：若后端因日结已锁定返回 409，提示用户当天已日结。
          var detail = (err && err.body && err.body.detail) || '日结失败，请重试';
          wx.showToast({ title: detail, icon: 'none', duration: 2500 });
        });
      }
    });
  }
});
