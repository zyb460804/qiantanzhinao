/**
 * 时间格式化工具（QA2-15）：UTC → CST(UTC+8)「MM-DD HH:mm」
 * 后端统一以 UTC 存储时间，但部分端点返回的 ISO 串不带 Z 后缀
 * （如 2026-09-22T16:37:00.553663），直接 new Date() 会被 JS 引擎
 * 按「本地时区无时区标记」规则解析，造成 8 小时显示偏差。
 * 这里统一：无时区标记的 ISO 串按 UTC 解释；带 Z/±hh:mm 的交原生解析。
 */

var TZ_OFFSET_MS = 8 * 3600 * 1000; // CST = UTC+8

/** 解析后端时间值为真实时刻的 epoch 毫秒；无法解析返回 NaN */
function parseUtc(val) {
  if (val === undefined || val === null || val === '') return NaN;
  if (val instanceof Date) return val.getTime();
  if (typeof val === 'number' && isFinite(val)) return val; // epoch 毫秒直通
  var s = String(val).trim();
  if (/^\d+$/.test(s)) return Number(s); // 纯数字字符串按 epoch 毫秒
  // ISO 串统一走确定性解析（不依赖引擎原生 parse，规避 iOS JSCore 对
  // ≥4 位小数 / 部分偏移格式的兼容差异）：无时区标记按 UTC 解释，
  // 显式 Z / ±hh:mm(±hhmm) 按其偏移折算。
  var m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(s);
  if (m) {
    // 小数部分（可能为 6 位微秒）截断到毫秒：0.553663 → 553。
    // 截断而非四舍五入：59.9999s 若进位到下一秒会让显示分钟 +1。
    var frac = m[7] ? +(String(m[7]) + '000').slice(0, 3) : 0;
    var ts = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0), frac);
    var tz = m[8];
    if (tz && tz !== 'Z') {
      var sign = tz.charAt(0) === '-' ? -1 : 1;
      var hh = +tz.substr(1, 2);
      var mm = +tz.substr(tz.length - 2);
      ts -= sign * (hh * 3600 + mm * 60) * 1000; // UTC = 本地时刻 - 偏移
    }
    return ts;
  }
  // 其余格式兜底交给原生 Date 解析
  var ts2 = Date.parse(s);
  return isNaN(ts2) ? NaN : ts2;
}

/** UTC → CST「MM-DD HH:mm」；解析失败时原样返回，避免显示空串 */
function cstTime(val) {
  var ts = parseUtc(val);
  if (isNaN(ts)) return val === undefined || val === null ? '' : String(val);
  var d = new Date(ts + TZ_OFFSET_MS);
  var pad = function (n) { return n < 10 ? '0' + n : String(n); };
  return pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
}

/** 真实时刻的 epoch 毫秒（供相对时间/超时判断），无法解析返回 NaN */
function toTimestamp(val) {
  return parseUtc(val);
}

module.exports = {
  parseUtc: parseUtc,
  cstTime: cstTime,
  toTimestamp: toTimestamp,
};
