/** 设备管理 + 价目屏 */
var app = getApp();
Page({
  data: {
    skin: '', loading: false, tab: 'devices',
    devices: [], showDevForm: false, devForm: { device_type: 'scale', device_name: '', serial_number: '' },
    priceDisplays: [], showSyncBtn: false, deviceSubmitting: false, syncSubmitting: false, loadError: false,
  },
  onShow: function () { this.setData({ skin: 'skin-' + app.resolveSkin() }); this.loadAll(); },
  switchTab: function (e) { this.setData({ tab: e.currentTarget.dataset.tab }); this.loadAll(); },

  scanDevice: function () {
    var self = this;
    wx.scanCode({ scanType: ['qrCode', 'barCode'], success: function (res) {
      self.setData({ showDevForm: true, devForm: { device_type: 'scale', device_name: '', serial_number: res.result } });
    }});
  },
  loadAll: function () {
    var self = this;
    this.setData({ loading: true });
    Promise.all([
      app.request({ url: '/devices' }).then(function (data) { return { ok: true, data: data }; }).catch(function () { return { ok: false, data: [] }; }),
      app.request({ url: '/devices/price-display' }).then(function (data) { return { ok: true, data: data }; }).catch(function () { return { ok: false, data: [] }; })
    ]).then(function (res) {
      var failed = !res[0].ok && !res[1].ok;
      self.setData({ devices: res[0].data || [], priceDisplays: res[1].data || [], loading: false, loadError: failed });
      if (failed) wx.showToast({ title: '设备数据加载失败', icon: 'none' });
    });
  },
  // Device
  openDevForm: function () { this.setData({ showDevForm: true, devForm: { device_type: 'scale', device_name: '', serial_number: '' } }); },
  closeDevForm: function () { this.setData({ showDevForm: false }); },
  onDevField: function (e) {
    var f = e.currentTarget.dataset.field;
    // type-chip 点击时 e.detail.value 为 undefined,需从 dataset.val 读取
    var v = (e.detail.value !== undefined && e.detail.value !== null) ? e.detail.value : e.currentTarget.dataset.val;
    var df = this.data.devForm;
    df[f] = v;
    this.setData({ devForm: df });
  },
  saveDevice: function () {
    var self = this, df = this.data.devForm;
    if (this.data.deviceSubmitting) return;
    if (!df.device_name.trim()) { wx.showToast({ title: '设备名不能为空', icon: 'none' }); return; }
    this.setData({ deviceSubmitting: true });
    app.request({ url: '/devices', method: 'POST', data: df }).then(function () {
      self.setData({ showDevForm: false, deviceSubmitting: false }); wx.showToast({ title: '已注册', icon: 'success' }); self.loadAll();
    }).catch(function (err) {
      self.setData({ deviceSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '注册失败,请重试', icon: 'none' });
    });
  },
  sendHeartbeat: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/devices/' + id + '/heartbeat', method: 'POST', data: {} }).then(function () {
      wx.showToast({ title: '心跳已发送', icon: 'success' }); self.loadAll();
    }).catch(function () { wx.showToast({ title: '心跳发送失败', icon: 'none' }); });
  },
  deactivateDevice: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '停用设备', content: '确认停用？', success: function (r) {
      if (!r.confirm) return;
      app.request({ url: '/devices/' + id, method: 'DELETE' }).then(function () {
        wx.showToast({ title: '已停用', icon: 'none' }); self.loadAll();
      }).catch(function () { wx.showToast({ title: '停用失败', icon: 'none' }); });
    }});
  },
  // Price Display
  syncPrices: function () {
    var self = this;
    if (this.data.syncSubmitting) return;
    this.setData({ syncSubmitting: true });
    wx.showLoading({ title: '同步中…' });
    app.request({ url: '/devices/price-display/sync', method: 'POST', data: {} }).then(function () {
      wx.hideLoading(); self.setData({ syncSubmitting: false });
      wx.showToast({ title: '已同步', icon: 'success' }); self.loadAll();
    }).catch(function (err) {
      wx.hideLoading(); self.setData({ syncSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '同步失败,请重试', icon: 'none' });
    });
  },
});
