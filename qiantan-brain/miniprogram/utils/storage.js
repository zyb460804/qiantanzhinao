/**
 * Local storage utility — wraps wx.getStorageSync/setStorageSync.
 * Provides typed access to persistent app data.
 *
 * 注意：所有需要持久化的键必须在此处登记，避免散落在各页面的字符串字面量
 * 造成键名漂移（如 app.js 与本文件历史上各写一份 'merchantId' 字符串）。
 * 使用方式：
 *   var storage = require('../../utils/storage');
 *   wx.setStorageSync(storage.KEYS.MERCHANT_ID, id);
 */

const STORAGE_KEYS = {
  ACCESS_TOKEN: 'accessToken',
  MERCHANT_ID: 'merchantId',
  MERCHANT_NAME: 'merchantName',
  RISK_PROFILE: 'riskProfile',
  VOICE_DIALECT: 'voiceDialect',
  // 全局偏好（皮肤/主题/减少动效）—— styleguide 与设置页共用
  SKIN_MANUAL: 'skinManual',
  THEME: 'theme',
  REDUCE_MOTION: 'reduceMotion',
  // 员工身份切换（权限体系）—— owner 登录后可切换到员工身份
  STAFF_TOKEN: 'staffToken',
  CURRENT_STAFF: 'currentStaff',
  // 商户商品名缓存（voice 页纠错 chips 用）—— catalog 列表拉取后写入
  SKU_NAMES: 'skuNamesCache',
};

/** SKU 名缓存有效期：过期后仍可兜底展示，但触发后台刷新。 */
const SKU_CACHE_TTL = 10 * 60 * 1000;
/** 缓存商品名上限：chips 一屏内点得完即可。 */
const SKU_CACHE_MAX = 80;

function readSkuCache() {
  try {
    var cached = wx.getStorageSync(STORAGE_KEYS.SKU_NAMES);
    if (cached && Array.isArray(cached.items)) return cached;
  } catch (e) {}
  return null;
}

/**
 * Get stored merchant ID.
 * @returns {string|null}
 */
function getMerchantId() {
  return wx.getStorageSync(STORAGE_KEYS.MERCHANT_ID) || null;
}

/**
 * Set merchant ID.
 * @param {string} id
 */
function setMerchantId(id) {
  wx.setStorageSync(STORAGE_KEYS.MERCHANT_ID, id);
}

/**
 * Get stored merchant name.
 * @returns {string}
 */
function getMerchantName() {
  return wx.getStorageSync(STORAGE_KEYS.MERCHANT_NAME) || '老板';
}

/**
 * Set merchant name.
 * @param {string} name
 */
function setMerchantName(name) {
  wx.setStorageSync(STORAGE_KEYS.MERCHANT_NAME, name);
}

/**
 * Get risk profile preference.
 * @returns {string} 'conservative' | 'neutral' | 'aggressive'
 */
function getRiskProfile() {
  return wx.getStorageSync(STORAGE_KEYS.RISK_PROFILE) || 'neutral';
}

/**
 * Set risk profile.
 * @param {string} profile
 */
function setRiskProfile(profile) {
  wx.setStorageSync(STORAGE_KEYS.RISK_PROFILE, profile);
}

/**
 * Get voice dialect setting.
 * @returns {string}
 */
function getVoiceDialect() {
  return wx.getStorageSync(STORAGE_KEYS.VOICE_DIALECT) || 'mandarin';
}

/**
 * Set voice dialect.
 * @param {string} dialect
 */
function setVoiceDialect(dialect) {
  wx.setStorageSync(STORAGE_KEYS.VOICE_DIALECT, dialect);
}

/**
 * Get cached SKU names for voice correction chips.
 * 过期缓存也返回（有旧名兜底好过没有），是否刷新由 isSkuCacheStale() 决定。
 * @returns {string[]}
 */
function getSkuNames() {
  var cached = readSkuCache();
  return cached ? cached.items : [];
}

/**
 * Whether the SKU cache is empty or stale (caller should refresh in background).
 * @returns {boolean}
 */
function isSkuCacheStale() {
  var cached = readSkuCache();
  return !cached || cached.items.length === 0 || (Date.now() - (cached.t || 0)) > SKU_CACHE_TTL;
}

/**
 * Cache SKU names for voice correction chips.
 * @param {string[]} names
 */
function setSkuNames(names) {
  var items = (Array.isArray(names) ? names : [])
    .map(function (n) { return (n || '').trim(); })
    .filter(Boolean);
  wx.setStorageSync(STORAGE_KEYS.SKU_NAMES, { t: Date.now(), items: items.slice(0, SKU_CACHE_MAX) });
}

module.exports = {
  KEYS: STORAGE_KEYS,
  getMerchantId, setMerchantId,
  getMerchantName, setMerchantName,
  getRiskProfile, setRiskProfile,
  getVoiceDialect, setVoiceDialect,
  getSkuNames, setSkuNames, isSkuCacheStale,
};
