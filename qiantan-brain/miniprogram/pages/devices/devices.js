/** 设备管理 + 价目屏 */
var app = getApp();
Page({
  stopMaskTap: function () {},
  data: {
    skin: '', loading: false, tab: 'devices',
    devices: [], showDevForm: false, devForm: { device_type: 'scale', device_name: '', serial_number: '' },
    priceDisplays: [], deviceSubmitting: false, syncSubmitting: false, deviceActionId: '', loadError: false,
  },
  onShow: function () { this.setData({ skin: 'skin-' + app.resolveSkin() }); this.loadAll(); },
  switchTab: function (e) { this.setData({ tab: e.currentTarget.dataset.tab }); },

  scanDevice: function () {
    var self = this;
    wx.scanCode({
      scanType: ['qrCode', 'barCode'],
      success: function (res) {
        self.setData({ showDevForm: true, devForm: { device_type: 'scale', device_name: '', serial_number: res.result || '' } });
      },
      fail: function (err) {
        if (err && err.errMsg && err.errMsg.indexOf('cancel') >= 0) return;
        wx.showToast({ title: '扫码失败，请检查相机权限', icon: 'none' });
      }
    });
  },
  loadAll: function () {
    var self = this;
    this.setData({ loading: true, loadError: false });
    Promise.all([
      app.request({ url: '/devices' }).then(function (data) { return { ok: true, data: data }; }).catch(function (err) { return { ok: false, data: [], err: err }; }),
      app.request({ url: '/devices/price-display' }).then(function (data) { return { ok: true, data: data }; }).catch(function (err) { return { ok: false, data: [], err: err }; })
    ]).then(function (res) {
      var failed = !res[0].ok || !res[1].ok;
      self.setData({
        devices: res[0].data || [],
        priceDisplays: res[1].data || [],
        loading: false,
        loadError: failed
      });
      if (failed) {
        var msg = !res[0].ok && !res[1].ok ? '设备数据加载失败' : (!res[0].ok ? '设备列表加载失败' : '价目屏数据加载失败');
        wx.showToast({ title: msg, icon: 'none' });
      }
    });
  },
  retryLoad: function () { this.loadAll(); },

  openDevForm: function () {
    this.setData({ showDevForm: true, devForm: { device_type: 'scale', device_name: '', serial_number: '' } });
  },
  closeDevForm: function () {
    if (this.data.deviceSubmitting) return;
    this.setData({ showDevForm: false });
  },
  onDevField: function (e) {
    var f = e.currentTarget.dataset.field;
    var v = (e.detail.value !== undefined && e.detail.value !== null) ? e.detail.value : e.currentTarget.dataset.val;
    var update = {};
    update['devForm.' + f] = v;
    this.setData(update);
  },
  saveDevice: function () {
    var self = this, df = this.data.devForm;
    if (this.data.deviceSubmitting) return;
    if (!df.device_name || !df.device_name.trim()) { wx.showToast({ title: '设备名不能为空', icon: 'none' }); return; }
    this.setData({ deviceSubmitting: true });
    app.request({ url: '/devices', method: 'POST', data: {
      device_type: df.device_type,
      device_name: df.device_name.trim(),
      serial_number: (df.serial_number || '').trim() || null
    } }).then(function () {
      self.setData({ showDevForm: false, deviceSubmitting: false });
      wx.showToast({ title: '已注册', icon: 'success' });
      self.loadAll();
    }).catch(function (err) {
      self.setData({ deviceSubmitting: false });
      wx.showToast({ title: (err.body && err.body.detail) || '注册失败，请重试', icon: 'none' });
    });
  },
  sendHeartbeat: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    if (this.data.deviceActionId) return;
    this.setData({ deviceActionId: id });
    app.request({ url: '/devices/' + id + '/heartbeat', method: 'POST', data: {} }).then(function () {
      self.setData({ deviceActionId: '' });
      wx.showToast({ title: '心跳已发送', icon: 'success' }); self.loadAll();
    }).catch(function (err) {
      self.setData({ deviceActionId: '' });
      wx.showToast({ title: (err.body && err.body.detail) || '心跳发送失败', icon: 'none' });
    });
  },
  deactivateDevice: function (e) {
    var id = e.currentTarget.dataset.id, self = this;
    if (this.data.deviceActionId) return;
    this.setData({ deviceActionId: id });
    wx.showModal({ title: '停用设备', content: '停用后将不能再发送心跳，确认继续？', success: function (r) {
      if (!r.confirm) { self.setData({ deviceActionId: '' }); return; }
      app.request({ url: '/devices/' + id, method: 'DELETE' }).then(function () {
        self.setData({ deviceActionId: '' });
        wx.showToast({ title: '已停用', icon: 'none' }); self.loadAll();
      }).catch(function (err) {
        self.setData({ deviceActionId: '' });
        wx.showToast({ title: (err.body && err.body.detail) || '停用失败', icon: 'none' });
      });
    }, fail: function () { self.setData({ deviceActionId: '' }); }});
  },
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
      wx.showToast({ title: (err.body && err.body.detail) || '同步失败，请重试', icon: 'none' });
    });
  },
});
