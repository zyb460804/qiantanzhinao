/** 设备管理 + 价目屏 */
var app = getApp();
Page({
  data: {
    skin: '', loading: false, tab: 'devices',
    devices: [], showDevForm: false, devForm: { device_type: 'scale', device_name: '', serial_number: '' },
    priceDisplays: [], showSyncBtn: false,
  },
  onShow: function () { this.setData({ skin: 'skin-' + app.resolveSkin() }); this.loadAll(); },
  switchTab: function (e) { this.setData({ tab: e.currentTarget.dataset.tab }); this.loadAll(); },
  loadAll: function () {
    var self = this;
    this.setData({ loading: true });
    Promise.all([
      app.request({ url: '/devices' }).catch(function () { return []; }),
      app.request({ url: '/devices/price-display' }).catch(function () { return []; })
    ]).then(function (res) {
      self.setData({ devices: res[0] || [], priceDisplays: res[1] || [], loading: false });
    });
  },
  // Device
  openDevForm: function () { this.setData({ showDevForm: true, devForm: { device_type: 'scale', device_name: '', serial_number: '' } }); },
  closeDevForm: function () { this.setData({ showDevForm: false }); },
  onDevField: function (e) { var f = e.currentTarget.dataset.field; var v = e.detail.value; var df = this.data.devForm; df[f] = v; this.setData({ devForm: df }); },
  saveDevice: function () {
    var self = this, df = this.data.devForm;
    if (!df.device_name.trim()) { wx.showToast({ title: '设备名不能为空', icon: 'none' }); return; }
    app.request({ url: '/devices', method: 'POST', data: df }).then(function () {
      self.setData({ showDevForm: false }); wx.showToast({ title: '已注册', icon: 'success' }); self.loadAll();
    });
  },
  sendHeartbeat: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    app.request({ url: '/devices/' + id + '/heartbeat', method: 'POST', data: {} }).then(function () {
      wx.showToast({ title: '心跳已发送', icon: 'success' }); self.loadAll();
    });
  },
  deactivateDevice: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    wx.showModal({ title: '停用设备', content: '确认停用？', success: function (r) { if (!r.confirm) return; app.request({ url: '/devices/' + id, method: 'DELETE' }).then(function () { wx.showToast({ title: '已停用', icon: 'none' }); self.loadAll(); }); }});
  },
  // Price Display
  syncPrices: function () {
    var self = this;
    wx.showLoading({ title: '同步中…' });
    app.request({ url: '/devices/price-display/sync', method: 'POST', data: {} }).then(function (data) {
      wx.hideLoading(); wx.showToast({ title: '已同步', icon: 'success' }); self.loadAll();
    }).catch(function () { wx.hideLoading(); });
  },
});
