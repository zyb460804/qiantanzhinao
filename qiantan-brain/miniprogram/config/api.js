/** API environment isolation for develop / trial / release builds. */
var DEV_API_BASE = 'http://127.0.0.1:8000/api/v1';
// Optional compile-time production fallback. Prefer extConfig.apiBase in CI/release.
var FIXED_PRODUCTION_API_BASE = '';

function normalize(value) {
  return String(value || '').trim().replace(/\/$/, '');
}

function getEnvVersion() {
  try {
    var info = wx.getAccountInfoSync();
    return info && info.miniProgram && info.miniProgram.envVersion || 'develop';
  } catch (e) {
    return 'develop';
  }
}

function getExtApiBase() {
  try {
    var ext = wx.getExtConfigSync ? wx.getExtConfigSync() : {};
    return normalize(ext && ext.apiBase);
  } catch (e) {
    return '';
  }
}

function validateSecureBase(apiBase) {
  if (!apiBase) return '未配置 apiBase';
  if (apiBase.indexOf('https://') !== 0) return 'apiBase 必须使用 HTTPS';
  if (/localhost|127\.0\.0\.1|0\.0\.0\.0/i.test(apiBase)) {
    return 'apiBase 不能指向本机地址';
  }
  return '';
}

function resolveApiBase() {
  var envVersion = getEnvVersion();
  var extBase = getExtApiBase();

  if (envVersion === 'develop') {
    var stored = normalize(wx.getStorageSync('apiBase'));
    return {
      ok: true,
      envVersion: envVersion,
      apiBase: stored || extBase || DEV_API_BASE,
      error: '',
    };
  }

  // trial/release intentionally ignore storage to prevent a developer override
  // from leaking into a signed build. Both environments enforce HTTPS.
  var deployedBase = extBase || normalize(FIXED_PRODUCTION_API_BASE);
  var error = validateSecureBase(deployedBase);
  return {
    ok: !error,
    envVersion: envVersion,
    apiBase: error ? '' : deployedBase,
    error: error,
  };
}

module.exports = {
  resolveApiBase: resolveApiBase,
  validateSecureBase: validateSecureBase,
};
