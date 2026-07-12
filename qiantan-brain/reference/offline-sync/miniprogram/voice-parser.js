/**
 * voice-parser.js — 端上轻量规则解析器（参考实现 / 教学样例）
 * ───────────────────────────────────────────────────────────────────────
 * 对应 PRD §4.1 P0-1「手动文字走端上轻量规则解析器（镜像服务端语义解析逻辑，纯本地）」
 * 与 D2「端上规则解析器需从服务端语义解析逻辑抽出一个纯函数本地版本，避免双份逻辑漂移」。
 *
 * ⚠️ 关键纪律：服务端 backend/app/services/voice_parser.py 与这里的逻辑必须保持一致。
 *    推荐做法：把「商品表 / 关键词 / 单位」抽成一份 JSON 规则（两端共用），
 *    并把核心解析函数做成纯函数 + 共享测试 fixture，CI 两端各跑一遍，防止漂移。
 *
 * 本文件是「纯函数、零依赖」，可在 Node 里直接单测（见 voice-parser.test.js）。
 */

const CN_NUM = { 零:0, 一:1, 二:2, 两:2, 三:3, 四:4, 五:5, 六:6, 七:7, 八:8, 九:9, 十:10, 百:100, 千:1000, 万:10000 };
const PURCHASE_KW = ['进了','进来','买的','买了','进货','上了','拉了','批了','采购'];
const SALE_KW = ['卖','卖了','卖出','一共卖','卖了钱','收入','赚了','收成'];
const WASTE_KW = ['坏了','扔了','烂了','掉了','损耗','报废','不能卖了'];
const FILLER = ['那个','嗯','啊','哦','呃','就是','然后','这个'];

function removeFillers(t) { let s = t; for (const w of FILLER) s = s.split(w).join(''); return s; }

function cnToNum(str) {
  // 健壮的中文数字→阿拉伯数字（支持 十/百/千 与 一二两三四五六七八九）。
  // 逐字符扫描，避免正则捕获组错位导致「五十→100」这类 bug。
  const d = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  let section = 0;
  let current = 0;
  for (const ch of str) {
    if (d[ch] != null) current = d[ch];
    else if (ch === '十') { section += (current || 1) * 10; current = 0; }
    else if (ch === '百') { section += (current || 1) * 100; current = 0; }
    else if (ch === '千') { section += (current || 1) * 1000; current = 0; }
  }
  return section + current;
}

function normalizeChineseNumbers(t) {
  // 仅转换「紧跟在单位/金额词之前的」中文数字，避免误伤普通文本。
  // 用向前断言，不消耗单位字符本身。
  return t.replace(
    /([零一二两三四五六七八九十百千万]+)(?=[斤公斤千克个把箱袋件元块毛分])/g,
    // ⚠️ 注意：replace 回调的第 1 个参数是「匹配到的整段字符串」，捕获组要取第 2、3 个参数！
    // 写成 (m) => m[1] 会误取到匹配串的第 2 个字符（经典 JS 坑）。
    (_full, g1) => String(cnToNum(g1))
  );
}

function normalizeMoney(t) {
  // ⚠️ 回调签名统一为 (_full, g1[, g2])：第 1 参是匹配串，捕获组从参数 2 起取。
  const mao = (_m, g1) => `${(CN_NUM[g1] || 0) * 0.1}元`;
  const fen = (_m, g1) => `${(CN_NUM[g1] != null ? CN_NUM[g1] : Number(g1)) * 0.01}元`;
  const kuai = (_m, g1) => {
    const raw = g1;
    const v = CN_NUM[raw] != null ? CN_NUM[raw] : Number(raw);
    return `${v}元`;
  };
  const kuaiMao = (_m, g1, g2) => {
    const k = CN_NUM[g1] != null ? CN_NUM[g1] : Number(g1);
    const mo = CN_NUM[g2] != null ? CN_NUM[g2] : Number(g2);
    return `${k + mo * 0.1}元`;
  };
  // 注意：金额正则必须「带 块/钱/元」才转换，绝不动裸数字，
  // 否则会把 "50斤" 里的 50 误变成 "50元斤"，破坏数量解析。
  return t
    .replace(/([一二三四五六七八九])(毛|角)钱?/g, mao)
    .replace(/([\d一二三四五六七八九两]+)分钱?/g, fen)
    .replace(/([\d一二三四五六七八九两]+)块([\d一二三四五六七八九两]+)毛?/g, kuaiMao)
    .replace(/([\d一二三四五六七八九两]+)(块|钱)/g, kuai)
    .replace(/(\d+)元(\d)角/g, '$1.$2元');
}

function detectEventType(t) {
  if (PURCHASE_KW.some((k) => t.includes(k))) return 'purchase';
  if (WASTE_KW.some((k) => t.includes(k))) return 'waste';
  if (SALE_KW.some((k) => t.includes(k))) return 'sale';
  return 'unknown';
}

function extractProduct(t, names) {
  let best = null;
  for (const n of names) if (t.includes(n) && (!best || n.length > best.length)) best = n;
  if (!best) {
    for (const n of names) if (n.length >= 2 && t.includes(n.slice(0, 2))) { best = n; break; }
  }
  return best;
}

function extractQuantity(t) {
  const units = ['斤','公斤','千克','个','把','箱','袋','件'];
  for (const u of units) {
    const m = t.match(new RegExp(`(\\d+(?:\\.\\d+)?)\\s*${u}`));
    if (m) return [parseFloat(m[1]), u];
  }
  return [null, '斤'];
}

function extractTotalAmount(t) {
  // 带前缀（一共/总计/花了/总价）+ 行尾裸「X元」兜底（口语「3斤15元」=合计15元）
  const pats = [
    /(?:一共|总计|花了|总价|一共花)\s*(\d+(?:\.\d+)?)\s*[元块]/,
    /(\d+(?:\.\d+)?)\s*[元块]\s*(?:一共|总计|总)/,
    /(\d+(?:\.\d+)?)\s*元\s*$/,
  ];
  for (const p of pats) { const m = t.match(p); if (m) return parseFloat(m[1]); }
  return null;
}

export function parseVoiceText(text, productNames = []) {
  let t = removeFillers(String(text || '').trim());
  t = normalizeChineseNumbers(t);
  t = normalizeMoney(t);

  const eventType0 = detectEventType(t);
  const product = extractProduct(t, productNames);
  const [quantity, unit] = extractQuantity(t);
  const totalAmount = extractTotalAmount(t);

  const missing = [];
  let guessed = 0;
  let eventType = eventType0;
  if (eventType === 'unknown') { eventType = 'purchase'; guessed += 1; } // 与服务端一致：默认采购
  if (!product) missing.push('product');
  if (quantity == null) missing.push('quantity');

  const confidence = Math.max(0, Math.min(1, 1 - 0.1 * missing.length - 0.05 * guessed));

  return {
    event_type: eventType,
    product,
    quantity,
    unit,
    unit_cost: eventType === 'purchase' ? null : undefined,
    unit_price: eventType === 'sale' ? null : undefined,
    total_cost: eventType === 'purchase' ? totalAmount : null,
    total_revenue: eventType === 'sale' ? totalAmount : null,
    total_amount: totalAmount,
    confidence: Math.round(confidence * 100) / 100,
    missing_fields: missing,
  };
}

export const PARSER_RULES = { CN_NUM, PURCHASE_KW, SALE_KW, WASTE_KW };

