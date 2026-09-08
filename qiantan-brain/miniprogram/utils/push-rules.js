/**
 * 主动推送规则引擎（统一版）— 纯函数模块，不依赖 wx，可在 Node 环境单测。
 *
 * 背景：index._rebuildTasks 与 advisor._buildPushCards 曾各自维护一套
 * 同源规则（临期/低库存/风险分/未记账/降雨），同一信息两处各算一遍且文案各异。
 * 本模块收敛两套规则的并集，文案取 advisor 版（重叠规则以 advisor 文案为准）。
 *
 * 规则并集（按优先级排序，最多返回 3 条）：
 *   1. expiry    临期批次 > 0            → 库存        （danger）
 *   2. low       余量较少品类 > 0         → 参谋建议     （warn，仅 index 原有）
 *   3. risk      风险分 ≥ 60             → 经营镜像     （warn）
 *   4. no_record 今日无经营流水           → 语音记账     （warn）
 *   5. weather   降雨概率 > 60%           → 决策沙盘     （info，仅 advisor 原有）
 *
 * 卡片字段：
 *   - severity: 'danger' | 'warn' | 'info' 语义级别，由消费方映射到各自的样式 tone
 *     （index: danger/warn 直接用、info→normal；advisor: danger→warn、warn/info 直接用）
 *   - glyph:  index 待办卡的字符徽标
 *   - icon:   advisor 推送卡预留的图标名
 *   - action: index 卡右侧 CTA 文案；cta: advisor 卡右侧 CTA 文案
 *   - route:  目标页面名（voice/inventory/advisor 为 tabBar 页，需 switchTab）
 */
var invStatus = require('./inventory-status');

var MAX_CARDS = 3;

/** 统计"余量较少"的品类数（阈值以后端下发的 low_stock_threshold 为准）。 */
function countLowStock(items) {
  var count = 0;
  (items || []).forEach(function (item) {
    if (invStatus.isLowStock(invStatus.resolveQty(item), invStatus.resolveThreshold(item))) {
      count += 1;
    }
  });
  return count;
}

/**
 * 构建主动推送卡片。
 * @param {object} data
 * @param {object|null} [data.dashboard]   /twin/dashboard 结果（expiring_count / risk_score）
 * @param {Array|null}  [data.inventory]   /inventory/current 结果（低库存判定，可缺省）
 * @param {object|null} [data.weather]     /env/today 结果（rainfall_prob）
 * @param {number|null} [data.voiceCount]  今日语音流水笔数（/voice/today-count）
 * @param {number|null} [data.recentCount] 最近流水条数（无 voiceCount 时的兜底信号）
 * @returns {Array<object>} 卡片数组，最多 3 条；无 dashboard 时返回 []
 */
function buildPushCards(data) {
  var input = data || {};
  var dashboard = input.dashboard;
  if (!dashboard) return [];

  var cards = [];

  // 规则1：临期提醒（advisor 文案）
  var expiring = Number(dashboard.expiring_count) || 0;
  if (expiring > 0) {
    cards.push({
      id: 'expiry',
      severity: 'danger',
      glyph: '临',
      icon: 'bulb',
      title: '有 ' + expiring + ' 件商品临期，建议尽快处理',
      desc: '损耗风险升高，先查看临期商品再决定促销或报损',
      action: '查看库存',
      cta: '查看库存',
      route: 'inventory',
    });
  }

  // 规则2：余量较少（index 文案）
  var lowCount = countLowStock(input.inventory);
  if (lowCount > 0) {
    cards.push({
      id: 'low',
      severity: 'warn',
      glyph: '补',
      icon: 'box',
      title: lowCount + ' 个品类余量较少',
      desc: '数量提示不等于必须补货，建议结合销量和明日客流判断。',
      action: '查看建议',
      cta: '查看建议',
      route: 'advisor',
    });
  }

  // 规则3：经营风险分偏高（advisor 文案）
  var riskScore = Number(dashboard.risk_score) || 0;
  if (riskScore >= 60) {
    cards.push({
      id: 'risk',
      severity: 'warn',
      glyph: '险',
      icon: 'bulb',
      title: '经营风险分偏高 (' + riskScore + '/100)',
      desc: '打开经营镜像查看风险来源：库存、现金流还是客流波动',
      action: '查看原因',
      cta: '看详情',
      route: 'dashboard',
    });
  }

  // 规则4：今日未记账（advisor 文案）。优先 voiceCount，缺省时用 recentCount 兜底。
  var noRecord = false;
  if (input.voiceCount != null) {
    noRecord = Number(input.voiceCount) === 0;
  } else if (input.recentCount != null) {
    noRecord = Number(input.recentCount) === 0;
  }
  if (noRecord) {
    cards.push({
      id: 'no_record',
      severity: 'warn',
      glyph: '记',
      icon: 'mic',
      title: '今天还没有经营流水，别忘了记一笔',
      desc: '进货、销售、损耗记完，利润和库存才会准确',
      action: '记一笔',
      cta: '去记账',
      route: 'voice',
    });
  }

  // 规则5：降雨预警（advisor 文案）
  var rain = input.weather ? Number(input.weather.rainfall_prob) || 0 : 0;
  if (rain > 60) {
    cards.push({
      id: 'weather',
      severity: 'info',
      glyph: '雨',
      icon: 'calendar',
      title: '明日降雨概率 ' + rain + '%，叶菜走量放缓',
      desc: '少进 15% 叶菜，转多备耐储根茎类',
      action: '调整进货',
      cta: '调整进货',
      route: 'sandbox',
    });
  }

  return cards.slice(0, MAX_CARDS);
}

module.exports = {
  buildPushCards: buildPushCards,
  MAX_CARDS: MAX_CARDS,
};
