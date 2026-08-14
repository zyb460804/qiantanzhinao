/**
 * 摊位设置 — 从「我的」页面拆出的独立设置页
 * 基础信息 + 建议偏好 + 皮肤主题
 */
var app = getApp();
var Theme = require('../../utils/theme');

// 旧存储值 → 权威方言键（与 voice.js _dialectAliases、后端 DIALECT_MAP 兼容键对齐）
var DIALECT_ALIASES = { sichuanese: 'sichuan', shanghainese: 'mandarin', southwest: 'mandarin' };
function normalizeDialect(code) {
  if (!code) return 'mandarin';
  return DIALECT_ALIASES[code] || code;
}

Page({
  data: {
    skinClass: '',
    // 基础信息
    merchantName: '',
    cityOptions: ['上海', '北京', '广州', '深圳', '杭州', '南京', '成都', '武汉', '重庆', '西安'],
    cityIndex: 0,
    merchantCity: '上海',
    // 方言选项与 voice.js _dialectOptions / 后端 DIALECT_MAP 权威键统一（M4）
    dialects: ['普通话', '四川话', '粤语', '河南话', '山东话'],
    dialectValues: ['mandarin', 'sichuan', 'cantonese', 'henan', 'shandong'],
    dialectIndex: 0,
    voiceDialect: 'mandarin',
    hoursOptions: ['早市 (6:00-12:00)', '午市 (12:00-18:00)', '晚市 (18:00-24:00)', '全天'],
    hoursValues: ['morning', 'noon', 'evening', 'all'],
    hoursIndex: 0,
    businessHours: 'morning',
    notificationEnabled: true,
    // 建议偏好
    riskProfile: 'neutral',
    // 皮肤主题
    skinManual: null, // morning/evening/auto
  },

  onShow: function () {
    this.applySkin();
    // 同步本地手动皮肤选择（用于"皮肤主题"单选回显）
    this.setData({ skinManual: app.globalData.skinManual || 'auto' });
    this.loadSettings();
  },

  applySkin: function () {
    // 用 Theme.apply 尊重手动皮肤设置(skinManual),而非强制按小时
    Theme.apply(this);
  },

  loadSettings: function () {
    var storedDialect = normalizeDialect(wx.getStorageSync('voiceDialect'));
    var storedRisk = wx.getStorageSync('riskProfile') || 'neutral';
    var storedHours = wx.getStorageSync('businessHours') || 'morning';
    var storedNotify = wx.getStorageSync('notificationEnabled');
    if (storedNotify === '') storedNotify = true;
    else storedNotify = storedNotify !== false;
    var di = this.data.dialectValues.indexOf(storedDialect);
    var hi = this.data.hoursValues.indexOf(storedHours);
    this.setData({
      merchantName: app.globalData.merchantName || '',
      voiceDialect: storedDialect, riskProfile: storedRisk,
      businessHours: storedHours, notificationEnabled: storedNotify,
      dialectIndex: di >= 0 ? di : 0, hoursIndex: hi >= 0 ? hi : 0,
      merchantCity: app.getCity(),
      cityIndex: Math.max(0, this.data.cityOptions.indexOf(app.getCity())),
    });
    // 从后端同步偏好设置（跨设备同步）
    var self = this;
    app.request({ url: '/auth/me/preferences', auth: true }).then(function (prefs) {
      if (!prefs) return;
      var dialect = normalizeDialect(prefs.voice_dialect || storedDialect);
      var risk = prefs.risk_profile || storedRisk;
      var hours = prefs.business_hours || storedHours;
      var notify = prefs.notification_enabled !== undefined ? prefs.notification_enabled : storedNotify;
      var city = prefs.merchant_city || app.getCity();
      var di2 = self.data.dialectValues.indexOf(dialect);
      var hi2 = self.data.hoursValues.indexOf(hours);
      var ci2 = Math.max(0, self.data.cityOptions.indexOf(city));
      self.setData({
        voiceDialect: dialect, riskProfile: risk,
        businessHours: hours, notificationEnabled: notify,
        dialectIndex: di2 >= 0 ? di2 : 0, hoursIndex: hi2 >= 0 ? hi2 : 0,
        merchantCity: city, cityIndex: ci2,
      });
      // 同步到本地缓存
      wx.setStorageSync('voiceDialect', dialect);
      wx.setStorageSync('riskProfile', risk);
      wx.setStorageSync('businessHours', hours);
      wx.setStorageSync('notificationEnabled', notify);
      app.setCity(city);
    }).catch(function () { /* 后端同步失败时使用本地设置，静默处理 */ });
  },

  onNameChange: function (e) { this.setData({ merchantName: e.detail.value }); },
  onRiskChange: function (e) { this.setData({ riskProfile: e.detail.value }); },
  onDialectChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ dialectIndex: index, voiceDialect: this.data.dialectValues[index] });
  },
  onBusinessHoursChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ hoursIndex: index, businessHours: this.data.hoursValues[index] });
  },
  onCityChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ cityIndex: index, merchantCity: this.data.cityOptions[index] });
  },
  onNotificationToggle: function () {
    // 注意：此开关仅记录偏好，实际微信订阅消息推送需要用户在每条通知授权时勾选。
    // TODO 后端推送任务读取此偏好后决定是否下发（目前后端推送链路尚未接入此字段）。
    this.setData({ notificationEnabled: !this.data.notificationEnabled });
  },
  /** 皮肤手动切换：morning/noon/evening/auto */
  onSkinChange: function (e) {
    var skin = e.detail.value;
    if (skin === 'auto') {
      app.globalData.skinManual = null;
      wx.removeStorageSync('skinManual');
    } else {
      app.globalData.skinManual = skin;
      wx.setStorageSync('skinManual', skin);
    }
    this.applySkin();
  },

  saveProfile: function () {
    wx.setStorageSync('merchantName', this.data.merchantName);
    wx.setStorageSync('voiceDialect', this.data.voiceDialect);
    wx.setStorageSync('riskProfile', this.data.riskProfile);
    wx.setStorageSync('businessHours', this.data.businessHours);
    wx.setStorageSync('notificationEnabled', this.data.notificationEnabled);
    app.setCity(this.data.merchantCity);
    app.globalData.merchantName = this.data.merchantName;
    // 同步摊位名称到后端 merchant.name 字段（之前只写本地 storage 导致换设备丢失）
    app.request({
      url: '/auth/me', method: 'PUT',
      data: { name: this.data.merchantName },
    }).then(function () {}).catch(function () { /* 名称更新失败不阻塞偏好保存 */ });
    // 推送偏好到后端（跨设备同步）
    app.request({
      url: '/auth/me/preferences', method: 'PUT',
      data: {
        voice_dialect: this.data.voiceDialect,
        risk_profile: this.data.riskProfile,
        business_hours: this.data.businessHours,
        notification_enabled: this.data.notificationEnabled,
        merchant_city: this.data.merchantCity,
      },
    }).then(function () {}).catch(function () {});
    wx.showToast({ title: '偏好已保存', icon: 'success' });
  },
});
