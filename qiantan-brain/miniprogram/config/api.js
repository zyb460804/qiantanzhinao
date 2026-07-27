/** API environment isolation for develop / trial / release builds. */
var DEV_API_BASE = 'http://127.0.0.1:8000/api/v1';
// ============================================================================
// 生产 API 地址 —— 发布前必填（二选一）：
//   方式 A（推荐）：在微信公众平台「小程序后台 → 扩展 → 扩展属性配置」里
//                  设置 extConfig.apiBase = "https://your-domain.com/api/v1"
//   方式 B：直接把域名填到下面的常量里（仅当未配置 extConfig 时作为兜底）
// 注意：必须 https:// 开头、禁止 localhost/127.0.0.1/0.0.0.0。
// 若两者都未配置，trial/release 版会 fail-closed 停止所有请求并弹窗提示。
// ============================================================================
// TODO(release): 发布正式版前确认 extConfig.apiBase 已配置或在此填入域名。
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
  if (error) {
    console.error('[api] 生产 API 地址未就绪：', error,
      '请在「小程序后台 → 扩展属性」配置 extConfig.apiBase，',
      '或在 config/api.js 的 FIXED_PRODUCTION_API_BASE 填入域名。');
  }
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
