/**
 * 时段皮肤工具 — theme.js
 *
 * 统一 morning/noon/evening 三档皮肤逻辑,
 * 避免在各页面中重复 getSkinByHour / resolveSkin / applySkin。
 *
 * 使用方式:
 *   var Theme = require('../../utils/theme');
 *   Page({ onShow: function () { Theme.apply(this); } });
 */

var app = null;

/**
 * 根据小时返回时段皮肤名称。
 */
function getSkinByHour(h) {
  if (h < 11) return 'morning';
  if (h < 17) return 'noon';
  return 'evening';
}

/**
 * 获取当前的语义皮肤 (手动 > 自动)。
 */
function resolveSkin() {
  if (!app) app = getApp();
  var manual = app.globalData.skinManual;
  if (manual === 'morning' || manual === 'evening') return manual;
  return getSkinByHour(new Date().getHours());
}

/**
 * 获取时段问候语。
 */
function getGreeting() {
  var h = new Date().getHours();
  if (h < 5 || h >= 22) return '夜深了';
  if (h < 11) return '早上好';
  if (h < 18) return '下午好';
  return '晚上好';
}

/**
 * 将皮肤应用到页面 (一站式: 设置 skin + skinClass + uiDark)。
 * 调用方式: Theme.apply(this) — 在 Page 的 onShow 中调用。
 * 同时把深浅主题同步到原生导航栏与 tabBar（在 tab 页上下文里调用才生效，
 * 因此放这里而不是 app.setTheme——每个 tab 页 onShow 都会兜底刷新一次）。
 */
function apply(pageInstance) {
  var skin = resolveSkin();
  var dark = app.globalData.theme === 'dark';
  var patch = { skin: skin, uiDark: dark };
  if (pageInstance.setData) {
    // 兼容 skinClass 字段
    try { patch.skinClass = 'skin-' + skin; } catch (e) {}
    pageInstance.setData(patch);
  }
  // 原生 chrome 跟随深浅主题（失败静默：非 tab 页调 setTabBarStyle 会 fail）
  try {
    wx.setNavigationBarColor({
      frontColor: dark ? '#ffffff' : '#000000',
      backgroundColor: dark ? '#182019' : '#ffffff',
      fail: function () {},
    });
  } catch (e) {}
  try {
    wx.setTabBarStyle({
      backgroundColor: dark ? '#182019' : '#ffffff',
      color: dark ? '#6e7870' : '#87918a',
      selectedColor: dark ? '#57c17c' : '#1b7a44',
      borderStyle: dark ? 'black' : 'white',
      fail: function () {},
    });
  } catch (e) {}
  return skin;
}

module.exports = {
  getSkinByHour: getSkinByHour,
  resolveSkin: resolveSkin,
  getGreeting: getGreeting,
  apply: apply,
};
