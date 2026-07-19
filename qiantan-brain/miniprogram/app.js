/**
 * 千摊智脑 — 小程序入口。
 * 统一负责微信登录、JWT 生命周期、API 请求、上传和网络恢复同步。
 *
 * API 地址环境隔离:
 *  - 开发环境: http://127.0.0.1:8000/api/v1（storage 可覆盖）
 *  - 体验版/正式版: 从 wx.getAccountInfoSync() 读取 envVersion，
 *    正式版拒绝 localhost/明文 HTTP，必须使用 HTTPS 合法域名
 */
/** 将微信/网络层抛出的对象转换为可读错误文本，避免控制台只显示 [object Object]。 */
function formatRuntimeError(value) {
  var err = value && value.reason ? value.reason : value;
  if (!err) return '未知错误';
  if (typeof err === 'string') return err;
  if (err.stack) return String(err.stack);
  if (err.message) return String(err.message);
  if (err.errMsg) return String(err.errMsg);

  var details = [];
  if (err.type) details.push('type=' + err.type);
  if (err.statusCode !== undefined) details.push('statusCode=' + err.statusCode);
  if (err.body) {
    try { details.push('body=' + JSON.stringify(err.body)); } catch (e) { details.push('body=[无法序列化]'); }
  }
  if (err.err) details.push('cause=' + formatRuntimeError(err.err));
  if (details.length) return details.join('; ');

  var seen = [];
  try {
    return JSON.stringify(err, function (key, item) {
      if (typeof item === 'object' && item !== null) {
        if (seen.indexOf(item) >= 0) return '[循环引用]';
        seen.push(item);
      }
      return item;
    });
  } catch (e) {
    return Object.prototype.toString.call(err);
  }
}

App({
  globalData: {
    apiBase: '',
    apiConfigured: false,
    envVersion: 'develop',
    accessToken: '',
    merchantId: '',
    merchantName: '',
    networkOnline: true,
    apiHealthy: null,
    servicePhone: '',   // 客服电话，由后端在登录/配置接口下发；空表示未配置
    skin: 'noon',
    skinManual: null,
    theme: 'light',
    reduceMotion: false,
    reportDataVersion: 0,
  },

  _toastLock: false,
  _loginPromise: null,
  _pendingRequests: {},   // 请求去重
  _requestSeq: 0,

  _createIdempotencyKey: function () {
    this._requestSeq += 1;
    var merchant = this.globalData.merchantId || 'anonymous';
    var random = Math.random().toString(36).slice(2, 12);
    return ['mp', merchant.slice(0, 12), Date.now(), this._requestSeq, random].join('-');
  },

  onLaunch: function () {
    var apiConfig = require('./config/api').resolveApiBase();
    this.globalData.apiBase = apiConfig.apiBase;
    this.globalData.apiConfigured = apiConfig.ok;
    this.globalData.envVersion = apiConfig.envVersion;
    if (!apiConfig.ok) {
      this.globalData.apiHealthy = false;
      this._showApiConfigurationError(apiConfig.error);
    }

    this.globalData.accessToken = wx.getStorageSync('accessToken') || '';
    this.globalData.merchantId = wx.getStorageSync('merchantId') || '';
    this.globalData.merchantName = wx.getStorageSync('merchantName') || '';
    this.globalData.skin = this.getSkinByHour(new Date().getHours());

    // 同步减少动效到 stream-text 模块
    try { require('./utils/stream-text').setReduceMotion(this.globalData.reduceMotion); } catch (e) {}

    if (this.globalData.apiConfigured) {
      this.checkApiHealth();
      this.ensureLogin(false).then(function () {
        try { require('./utils/offline-sync').getQueue().sync().catch(function () {}); } catch (e) {}
      }).catch(function (err) {
        console.error('登录初始化失败:', err);
      });
    }

    wx.onNetworkStatusChange(function (res) {
      this.globalData.networkOnline = res.isConnected;
      if (res.isConnected && this.globalData.apiConfigured) {
        this.checkApiHealth();
        this.ensureLogin(false).then(function () {
          return require('./utils/offline-sync').getQueue().sync();
        }).catch(function (err) {
          console.error('网络恢复后的登录/同步失败:', err);
        });
      }
    }.bind(this));
  },

  onError: function (err) { console.error('[小程序全局错误]', formatRuntimeError(err), err); },
  onUnhandledRejection: function (res) { console.error('[未处理 Promise 拒绝]', formatRuntimeError(res), res && (res.reason || res)); },
  onPageNotFound: function () { wx.switchTab({ url: '/pages/index/index' }); },

  _configurationError: function () {
    return {
      type: 'configuration_error',
      message: '当前版本未配置安全的后端 API 地址',
    };
  },

  _showApiConfigurationError: function (detail) {
    var message = '当前小程序版本的后端地址配置错误，已停止所有网络请求。';
    if (detail) message += '\n' + detail;
    console.error('[API 配置保护]', message);
    wx.showModal({
      title: '发布配置错误',
      content: message,
      showCancel: false,
    });
  },

  showToast: function (msg, icon) {
    if (this._toastLock) return;
    this._toastLock = true;
    wx.showToast({ title: msg, icon: icon || 'none', duration: 2500 });
    setTimeout(function () { this._toastLock = false; }.bind(this), 2500);
  },

  checkApiHealth: function () {
    if (!this.globalData.apiConfigured) {
      this.globalData.apiHealthy = false;
      return;
    }
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
    if (!this.globalData.apiConfigured) return Promise.reject(this._configurationError());
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

  _requestOnce: function (options, retried, attempt) {
    var self = this;
    attempt = attempt || 1;
    var authRequired = options.auth !== false;
    var token = this.globalData.accessToken;
    var maxRetries = options.maxRetries != null ? options.maxRetries : 2;
    var method = String(options.method || 'GET').toUpperCase();
    // 写请求（POST/PUT/PATCH/DELETE）默认禁止自动重试，
    // 除非显式设置 retrySafe: true（需配合服务端幂等键）
    var isWriteMethod = method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
    var allowRetry = !isWriteMethod || options.retrySafe === true;
    return new Promise(function (resolve, reject) {
      var header = Object.assign({ 'Content-Type': 'application/json' }, options.header || {});
      if (authRequired && token) header.Authorization = 'Bearer ' + token;
      if (isWriteMethod && options.retrySafe === true) {
        header['Idempotency-Key'] = options.idempotencyKey;
      }
      wx.request({
        url: self.globalData.apiBase + options.url,
        method: method,
        data: options.data,
        header: header,
        timeout: options.timeout || 15000,
        success: function (res) {
          var body = res.data;
          if (body && body.code === 0) {
            self.globalData.apiHealthy = true;
            if (isWriteMethod) self.markReportDirty();
            resolve(body.data);
          } else if (res.statusCode === 401 && authRequired && !retried) {
            self.ensureLogin(true).then(function () {
              return self._requestOnce(options, true, attempt);
            }).then(resolve).catch(reject);
          } else {
            // 5xx server errors: auto-retry with backoff（读请求 & 显式安全的写请求）
            var isRetryable = (res.statusCode >= 500 || res.statusCode === 0 || res.statusCode === 429)
              && allowRetry && attempt <= maxRetries;
            if (isRetryable) {
              var delay = Math.min(1000 * Math.pow(2, attempt - 1), 8000);
              setTimeout(function () {
                self._requestOnce(options, retried, attempt + 1).then(resolve).catch(reject);
              }, delay);
              return;
            }
            var type = res.statusCode >= 500 ? 'server_error' : res.statusCode === 404 ? 'not_found' : 'business_error';
            var msg = body && (body.message || body.detail);
            if (type === 'server_error') self.showToast('服务器异常，请稍后重试');
            else if (msg) self.showToast(String(msg));
            reject({ type: type, statusCode: res.statusCode, body: body });
          }
        },
        fail: function (err) {
          // Network errors: auto-retry with backoff (读请求 & 显式安全的写请求)
          if (allowRetry && attempt <= maxRetries) {
            var delay = Math.min(1000 * Math.pow(2, attempt - 1), 8000);
            setTimeout(function () {
              self._requestOnce(options, retried, attempt + 1).then(resolve).catch(reject);
            }, delay);
            return;
          }
          self.globalData.apiHealthy = false;
          reject({ type: 'network_error', err: err });
        },
      });
    });
  },

  request: function (options) {
    if (!this.globalData.apiConfigured) return Promise.reject(this._configurationError());
    options = Object.assign({}, options);
    var authRequired = options.auth !== false;
    var self = this;
    var method = String(options.method || 'GET').toUpperCase();
    var isWriteMethod = method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
    if (isWriteMethod && options.retrySafe === true && !options.idempotencyKey) {
      options.idempotencyKey = self._createIdempotencyKey();
    }

    // Request deduplication: if antiDuplicate is set, reuse in-flight promise
    if (options.antiDuplicate) {
      var dupKey = options.dupKey || (options.method || 'GET') + ':' + options.url + ':' + JSON.stringify(options.data || {});
      if (self._pendingRequests[dupKey]) return self._pendingRequests[dupKey];
    }

    var promise;
    if (!authRequired) {
      promise = self._requestOnce(options, false);
    } else {
      promise = self.ensureLogin(false).then(function () { return self._requestOnce(options, false); });
    }

    if (options.antiDuplicate) {
      var key = options.dupKey || (options.method || 'GET') + ':' + options.url + ':' + JSON.stringify(options.data || {});
      self._pendingRequests[key] = promise;
      promise.finally(function () { delete self._pendingRequests[key]; });
    }

    return promise;
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
    if (!this.globalData.apiConfigured) return Promise.reject(this._configurationError());
    var self = this;
    return this.ensureLogin(false).then(function () { return self._uploadOnce(options, false); });
  },

  markReportDirty: function () {
    var previous = Number(this.globalData.reportDataVersion) || 0;
    this.globalData.reportDataVersion = Math.max(Date.now(), previous + 1);
    return this.globalData.reportDataVersion;
  },

  getReportDataVersion: function () { return Number(this.globalData.reportDataVersion) || 0; },

  getMerchantId: function () { return this.globalData.merchantId; },
  getSkinByHour: function (h) { return h < 11 ? 'morning' : h < 17 ? 'noon' : 'evening'; },
  resolveSkin: function () { return this.globalData.skinManual || this.getSkinByHour(new Date().getHours()); },

  /** 获取商户所在城市（优先 storage，默认上海） */
  getCity: function () {
    return wx.getStorageSync('merchantCity') || '上海';
  },
  /** 设置商户城市 */
  setCity: function (city) {
    this.globalData.merchantCity = city;
    wx.setStorageSync('merchantCity', city);
  },

  /**
   * 统一错误日志 + 可选 Toast。
   * @param {string} context  - 错误发生位置 (如 'voice/parseText')
   * @param {Error}  err      - 错误对象
   * @param {Object} opts     - { silent: true 不弹Toast, level: 'warn'|'error' }
   */
  logError: function (context, err, opts) {
    opts = opts || {};
    var detail = (err && (err.message || err.errMsg)) || String(err);
    var level = opts.level || 'error';
    console[level]('[' + context + ']', detail, err);
    if (!opts.silent) {
      var msg = (err && err.body && (err.body.message || err.body.detail));
      if (msg) this.showToast(String(msg));
    }
  },
});
