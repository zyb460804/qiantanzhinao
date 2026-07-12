/**
 * Local storage utility — wraps wx.getStorageSync/setStorageSync.
 * Provides typed access to persistent app data.
 */

const STORAGE_KEYS = {
  MERCHANT_ID: 'merchantId',
  MERCHANT_NAME: 'merchantName',
  RISK_PROFILE: 'riskProfile',
  VOICE_DIALECT: 'voiceDialect',
};

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

module.exports = {
  getMerchantId, setMerchantId,
  getMerchantName, setMerchantName,
  getRiskProfile, setRiskProfile,
  getVoiceDialect, setVoiceDialect,
};
