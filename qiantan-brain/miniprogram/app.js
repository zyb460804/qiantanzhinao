/**
 * 千摊智脑 — 小程序入口。
 * 统一负责微信登录、JWT 生命周期、API 请求、上传和网络恢复同步。
 */
App({
  globalData: {
    apiBase: 'http://127.0.0.1:8000/api/v1',
    accessToken: '',
    merchantId: '',
    merchantName: '',
    networkOnline: true,
    apiHealthy: null,
    skin: 'noon',
    skinManual: null,
    theme: 'light',
    reduceMotion: false,
  },

  _toastLock: false,
  _loginPromise: null,

  onLaunch: function () {
    var configuredBase = wx.getStorageSync('apiBase');
    if (configuredBase) this.globalData.apiBase = String(configuredBase).replace(/\/$/, '');
    this.globalData.accessToken = wx.getStorageSync('accessToken') || '';
    this.globalData.merchantId = wx.getStorageSync('merchantId') || '';
    this.globalData.merchantName = wx.getStorageSync('merchantName') || '';
    this.globalData.skin = this.getSkinByHour(new Date().getHours());

    this.checkApiHealth();
    this.ensureLogin(false).then(function () {
      try { require('./utils/offline-sync').getQueue().sync().catch(function () {}); } catch (e) {}
    }).catch(function (err) {
      console.error('登录初始化失败:', err);
    });

    wx.onNetworkStatusChange(function (res) {
      this.globalData.networkOnline = res.isConnected;
      if (res.isConnected) {
        this.checkApiHealth();
        this.ensureLogin(false).then(function () {
          return require('./utils/offline-sync').getQueue().sync();
        }).catch(function (err) {
          console.error('网络恢复后的登录/同步失败:', err);
        });
      }
    }.bind(this));
  },

  onError: function (err) { console.error('全局错误:', err); },
  onUnhandledRejection: function (res) { console.error('未处理的Promise拒绝:', res.reason); },
  onPageNotFound: function () { wx.redirectTo({ url: '/pages/index/index' }); },

  showToast: function (msg, icon) {
    if (this._toastLock) return;
    this._toastLock = true;
    wx.showToast({ title: msg, icon: icon || 'none', duration: 2500 });
    setTimeout(function () { this._toastLock = false; }.bind(this), 2500);
  },

  checkApiHealth: function () {
    var self = this;
    wx.request({
      url: this.globalData.apiBase + '/health',
      method: 'GET', timeout: 5000,
      success: function (res) { self.globalData.apiHealthy = res.statusCode === 200; },
      fail: function () { self.globalData.apiHealthy = false; },
    });
  },

  _persistLogin: function (data) {
    var merchant = data.merchant || {};
    this.globalData.accessToken = data.token;
    this.globalData.merchantId = merchant.id || '';
    this.globalData.merchantName = merchant.name || '';
    wx.setStorageSync('accessToken', data.token);
    wx.setStorageSync('merchantId', this.globalData.merchantId);
    wx.setStorageSync('merchantName', this.globalData.merchantName);
  },

  clearLogin: function () {
    this.globalData.accessToken = '';
    this.globalData.merchantId = '';
    this.globalData.merchantName = '';
    wx.removeStorageSync('accessToken');
    wx.removeStorageSync('merchantId');
    wx.removeStorageSync('merchantName');
  },

  _wechatCode: function () {
    return new Promise(function (resolve, reject) {
      wx.login({
        timeout: 10000,
        success: function (res) { res.code ? resolve(res.code) : reject({ type: 'login_error', detail: res }); },
        fail: function (err) { reject({ type: 'login_error', detail: err }); },
      });
    });
  },

  _authLogin: function (code) {
    var self = this;
    return new Promise(function (resolve, reject) {
      wx.request({
        url: self.globalData.apiBase + '/auth/wechat-login',
        method: 'POST',
        data: { code: code },
        header: { 'Content-Type': 'application/json' },
        timeout: 15000,
        success: function (res) {
          var body = res.data;
          if (res.statusCode === 200 && body && body.code === 0 && body.data && body.data.token) {
            self._persistLogin(body.data);
            resolve(body.data);
          } else {
            reject({ type: 'auth_error', statusCode: res.statusCode, body: body });
          }
        },
        fail: function (err) { reject({ type: 'network_error', err: err }); },
      });
    });
  },

  ensureLogin: function (force) {
    if (!force && this.globalData.accessToken) return Promise.resolve(this.globalData.accessToken);
    if (this._loginPromise) return this._loginPromise;
    if (force) this.clearLogin();
    var self = this;
    this._loginPromise = this._wechatCode()
      .then(function (code) { return self._authLogin(code); })
      .then(function (data) { return data.token; })
      .catch(function (err) {
        var msg = err && err.body && (err.body.detail || err.body.message);
        self.showToast(msg || '登录失败，请检查微信及后端配置');
        throw err;
      })
      .finally(function () { self._loginPromise = null; });
    return this._loginPromise;
  },

  _requestOnce: function (options, retried) {
    var self = this;
    var authRequired = options.auth !== false;
    var token = this.globalData.accessToken;
    return new Promise(function (resolve, reject) {
      var header = Object.assign({ 'Content-Type': 'application/json' }, options.header || {});
      if (authRequired && token) header.Authorization = 'Bearer ' + token;
      wx.request({
        url: self.globalData.apiBase + options.url,
        method: options.method || 'GET',
        data: options.data,
        header: header,
        timeout: options.timeout || 15000,
        success: function (res) {
          var body = res.data;
          if (body && body.code === 0) {
            self.globalData.apiHealthy = true;
            resolve(body.data);
          } else if (res.statusCode === 401 && authRequired && !retried) {
            self.ensureLogin(true).then(function () {
              return self._requestOnce(options, true);
            }).then(resolve).catch(reject);
          } else {
            var type = res.statusCode >= 500 ? 'server_error' : res.statusCode === 404 ? 'not_found' : 'business_error';
            var msg = body && (body.message || body.detail);
            if (type === 'server_error') self.showToast('服务器异常，请稍后重试');
            else if (msg) self.showToast(String(msg));
            reject({ type: type, statusCode: res.statusCode, body: body });
          }
        },
        fail: function (err) {
          self.globalData.apiHealthy = false;
          reject({ type: 'network_error', err: err });
        },
      });
    });
  },

  request: function (options) {
    var authRequired = options.auth !== false;
    if (!authRequired) return this._requestOnce(options, false);
    var self = this;
    return this.ensureLogin(false).then(function () { return self._requestOnce(options, false); });
  },

  _uploadOnce: function (options, retried) {
    var self = this;
    return new Promise(function (resolve, reject) {
      var header = Object.assign({}, options.header || {});
      if (self.globalData.accessToken) header.Authorization = 'Bearer ' + self.globalData.accessToken;
      wx.uploadFile({
        url: self.globalData.apiBase + options.url,
        filePath: options.filePath,
        name: options.name || 'image',
        formData: options.formData || {},
        header: header,
        timeout: options.timeout || 30000,
        success: function (res) {
          var body;
          try { body = JSON.parse(res.data); } catch (e) { reject({ type: 'parse_error', err: e }); return; }
          if (body && body.code === 0) resolve(body.data);
          else if (res.statusCode === 401 && !retried) {
            self.ensureLogin(true).then(function () { return self._uploadOnce(options, true); }).then(resolve).catch(reject);
          } else reject({ type: res.statusCode >= 500 ? 'server_error' : 'business_error', statusCode: res.statusCode, body: body });
        },
        fail: function (err) { reject({ type: 'network_error', err: err }); },
      });
    });
  },

  uploadFile: function (options) {
    var self = this;
    return this.ensureLogin(false).then(function () { return self._uploadOnce(options, false); });
  },

  getMerchantId: function () { return this.globalData.merchantId; },
  getSkinByHour: function (h) { return h < 11 ? 'morning' : h < 17 ? 'noon' : 'evening'; },
  resolveSkin: function () { return this.globalData.skinManual || this.getSkinByHour(new Date().getHours()); },
});
