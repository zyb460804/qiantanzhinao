var app = getApp();
var offlineSync = require('../../utils/offline-sync');

function money(value) { return Math.round(Number(value || 0) * 100) / 100; }
function paymentLabel(method) {
  return { cash: '现金', wechat: '微信', alipay: '支付宝', card: '银行卡', credit: '赊账' }[method] || method;
}

Page({
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
    showRefund: false, refundOrder: null, refundReason: '', refundReturnStock: true, refundSubmitting: false
  },

  onShow: function () {
    this.setData({ skin: app.resolveSkin() });
    this.loadData();
    this.syncPendingOrders();
    this.loadHeldOrders();
  },

  // ==================== 数据加载 ====================

  loadData: function () {
    var self = this;
    this.setData({ loading: true });
    Promise.all([
      app.request({ url: '/inventory/current' }),
      app.request({ url: '/pos/orders?limit=10' })
    ]).then(function (res) {
      var products = (res[0] || []).filter(function (item) { return Number(item.current_qty) > 0; }).map(function (item) {
        var price = item.default_sale_price;
        if (price === null || price === undefined) price = money(Number(item.avg_cost || 0) * 1.3);
        return Object.assign({}, item, { sale_price: money(price), price_is_estimated: !item.default_sale_price });
      });
      self.setData({ products: products, records: self.mergePendingRecords(res[1] || []), loading: false });
    }).catch(function () { self.setData({ loading: false }); });
  },

  loadHeldOrders: function () {
    var self = this;
    app.request({ url: '/pos/orders/held' }).then(function (data) {
      self.setData({ heldOrders: data || [] });
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
        if (r.confirm && price >= 0) self.addToCart(product, price);
      }});
      return;
    }
    this.addToCart(product, product.sale_price);
  },

  addToCart: function (product, price) {
    var cart = this.data.cart.slice();
    var found = -1;
    for (var i = 0; i < cart.length; i++) if (cart[i].product_id === product.product_id) found = i;
    if (found >= 0) cart[found].quantity = money(cart[found].quantity + 1);
    else cart.push({ product_id: product.product_id, sku_id: product.sku_id, product_name: product.sku_name || product.product_name, quantity: 1, max_qty: product.current_qty, unit: product.unit, unit_price: money(price) });
    this.updateCart(cart);
  },

  changeQty: function (e) {
    var index = Number(e.currentTarget.dataset.index);
    var delta = Number(e.currentTarget.dataset.delta);
    var cart = this.data.cart.slice();
    if (!cart[index]) return;
    cart[index].quantity = money(cart[index].quantity + delta);
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
    if (Math.abs(assigned - payable) > 0.01) {
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

  selectPayment: function (e) { this.setData({ paymentMethod: e.currentTarget.dataset.method }); },

  // ==================== 结账 ====================

  checkout: function () {
    if (!this.data.cart.length || this.data.submitting) return;
    var self = this;
    var isCredit = this.data.multiPay
      ? (Number(this.data.paySplit.credit || 0) > 0)
      : this.data.paymentMethod === 'credit';
    if (isCredit) {
      wx.showModal({ title: '赊账客户', editable: true, placeholderText: '例如：张记饭店', success: function (r) {
        if (r.confirm && String(r.content || '').trim()) self.doCheckout(String(r.content).trim());
      }});
    } else {
      this.doCheckout('');
    }
  },

  doCheckout: function (customerName) {
    var self = this;
    var payable = this.data.payableAmount;

    if (this.data.multiPay) {
      // Validate combined payment total
      var split = this.data.paySplit;
      var totalSplit = money((Number(split.wechat) || 0) + (Number(split.cash) || 0) + (Number(split.alipay) || 0) + (Number(split.credit) || 0));
      if (Math.abs(totalSplit - payable) > 0.01) {
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
    if (!this.data.cart.length) { wx.showToast({ title: '购物车为空', icon: 'none' }); return; }
    var self = this;
    this.setData({ submitting: true });
    app.request({ url: '/pos/orders/hold', method: 'POST', data: {
      items: this.data.cart.map(function (item) { return { product_id: item.product_id, sku_id: item.sku_id || null, quantity: item.quantity, unit: item.unit, unit_price: item.unit_price }; }),
      discount_amount: this.data.discountAmount
    }}).then(function () {
      self.setData({ submitting: false, cart: [], grossAmount: 0, discountAmount: 0, payableAmount: 0 });
      wx.showToast({ title: '订单已挂起', icon: 'none' });
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
    wx.showModal({ title: '组合支付 ¥' + order.total_amount, editable: true, placeholderText: '微信金额', content: String(order.total_amount), success: function (r1) {
      var wechatAmt = money(r1.content);
      if (!r1.confirm) return;
      var remaining = money(order.total_amount - wechatAmt);
      if (remaining <= 0) {
        self.setData({ submitting: true });
        app.request({ url: '/pos/orders/' + orderId + '/resume', method: 'POST', data: { payments: [{ method: 'wechat', amount: wechatAmt }] } }).then(function () {
          self.setData({ submitting: false }); wx.showToast({ title: '已取回收款', icon: 'none' }); self.loadHeldOrders(); self.loadData();
        }).catch(function (err) { self.setData({ submitting: false }); wx.showToast({ title: (err.body && err.body.detail) || '失败', icon: 'none' }); });
        return;
      }
      wx.showActionSheet({ itemList: ['现金 ¥' + remaining, '赊账 ¥' + remaining], success: function (r2) {
        var method2 = r2.tapIndex === 0 ? 'cash' : 'credit';
        self.setData({ submitting: true });
        app.request({ url: '/pos/orders/' + orderId + '/resume', method: 'POST', data: { payments: [{ method: 'wechat', amount: wechatAmt }, { method: method2, amount: remaining }] } }).then(function () {
          self.setData({ submitting: false }); wx.showToast({ title: '已取回收款', icon: 'none' }); self.loadHeldOrders(); self.loadData();
        }).catch(function (err) { self.setData({ submitting: false }); wx.showToast({ title: (err.body && err.body.detail) || '失败', icon: 'none' }); });
      }});
    }});
  },

  // ==================== 退款 ====================

  openRefund: function (e) {
    var orderId = e.currentTarget.dataset.id;
    var order = this.data.records.find(function (r) { return r.order_id === orderId; });
    if (!order) return;
    if (order.status === 'held' || order.status === 'pending' || order.status === 'cancelled') {
      wx.showToast({ title: '当前状态不可退款', icon: 'none' }); return;
    }
    this.setData({ showRefund: true, refundOrder: order, refundReason: '', refundReturnStock: true });
  },

  closeRefund: function () {
    this.setData({ showRefund: false, refundOrder: null, refundReason: '', refundReturnStock: true });
  },

  inputRefundReason: function (e) { this.setData({ refundReason: e.detail.value }); },
  toggleReturnStock: function () { this.setData({ refundReturnStock: !this.data.refundReturnStock }); },

  confirmRefund: function () {
    var self = this;
    var order = this.data.refundOrder;
    var reason = this.data.refundReason.trim();
    if (!reason) { wx.showToast({ title: '请填写退款原因', icon: 'none' }); return; }
    this.setData({ refundSubmitting: true });
    app.request({ url: '/pos/orders/' + order.order_id + '/refund', method: 'POST', data: {
      reason: reason, return_to_stock: this.data.refundReturnStock
    }}).then(function (data) {
      self.setData({ refundSubmitting: false, showRefund: false, refundOrder: null });
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
    var today = new Date().toISOString().slice(0, 10);
    wx.showModal({ title: '确认日结', content: '将按系统销售、各渠道实收、采购付款、赊账余额生成对账结果。日结后不可直接修改历史记录。', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/pos/daily-settlement/' + today + '/close', method: 'POST' }).then(function (data) {
        self.setData({ settlement: data });
        wx.showToast({ title: Math.abs(data.diff_amount || 0) < 0.01 ? '日结完成，账目平衡' : '日结完成，存在差异', icon: 'none' });
      });
    }});
  }
});
