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
 * 将皮肤应用到页面 (一站式: 设置 skin + skinClass)。
 * 调用方式: Theme.apply(this) — 在 Page 的 onShow 中调用。
 */
function apply(pageInstance) {
  var skin = resolveSkin();
  var patch = { skin: skin };
  if (pageInstance.setData) {
    // 兼容 skinClass 字段
    try { patch.skinClass = 'skin-' + skin; } catch (e) {}
    pageInstance.setData(patch);
  }
  return skin;
}

module.exports = {
  getSkinByHour: getSkinByHour,
  resolveSkin: resolveSkin,
  getGreeting: getGreeting,
  apply: apply,
};
