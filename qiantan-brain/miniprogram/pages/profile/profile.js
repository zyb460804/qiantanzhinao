// 商户设置页
var app = getApp();

Page({
  data: {
    merchantName: '',
    businessType: '蔬菜',
    riskProfile: 'neutral',
    voiceDialect: 'mandarin',
    entries: [
      { page: 'pos', name: '收银开单', glyph: '收', tone: 'green' },
      { page: 'dashboard', name: '经营镜像', glyph: '镜', tone: 'green' },
      { page: 'report', name: '经营报告', glyph: '报', tone: 'blue' },
      { page: 'sandbox', name: '决策沙盘', glyph: '算', tone: 'corn' },
      { page: 'purchase', name: '采购清单', glyph: '采', tone: 'green' },
      { page: 'stocktake', name: '库存盘点', glyph: '盘', tone: 'corn' },
      { page: 'calendar', name: '经营日历', glyph: '历', tone: 'blue' },
      { page: 'vision', name: '拍照识货', glyph: '识', tone: 'blue' },
      { page: 'catalog', name: '商品目录', glyph: '录', tone: 'green' },
      { page: 'ops', name: '经营管理', glyph: '管', tone: 'corn' },
      { page: 'devices', name: '设备管理', glyph: '设', tone: 'blue' },
      { page: 'finance', name: '财务管理', glyph: '财', tone: 'green' },
    ],
    dialects: ['普通话', '四川话', '粤语', '上海话'],
    dialectValues: ['mandarin', 'sichuanese', 'cantonese', 'shanghainese'],
    dialectIndex: 0,
    skinClass: '',
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    var storedDialect = wx.getStorageSync('voiceDialect') || 'mandarin';
    var storedRisk = wx.getStorageSync('riskProfile') || 'neutral';
    var index = this.data.dialectValues.indexOf(storedDialect);
    this.setData({ merchantName: app.globalData.merchantName || '', voiceDialect: storedDialect, riskProfile: storedRisk, dialectIndex: index >= 0 ? index : 0 });
  },

  onNameChange: function (e) { this.setData({ merchantName: e.detail.value }); },
  onRiskChange: function (e) { this.setData({ riskProfile: e.detail.value }); },
  onDialectChange: function (e) {
    var index = Number(e.detail.value) || 0;
    this.setData({ dialectIndex: index, voiceDialect: this.data.dialectValues[index] });
  },
  saveProfile: function () {
    wx.setStorageSync('merchantName', this.data.merchantName);
    wx.setStorageSync('voiceDialect', this.data.voiceDialect);
    wx.setStorageSync('riskProfile', this.data.riskProfile);
    app.globalData.merchantName = this.data.merchantName;
    wx.showToast({ title: '偏好已保存', icon: 'success' });
  },

  goDeep: function (e) {
    var page = e.currentTarget.dataset.page;
    if (!page) return;
    wx.navigateTo({ url: '/pages/' + page + '/' + page });
  },
});

