/**
 * 库存状态判断 — 纯函数模块，不依赖 wx，可在 Node 环境单测。
 *
 * 抽取自 inventory._decorateItems 与 index._renderHomeData/_rebuildTasks
 * 三处重复的阈值逻辑，统一 "余量较少" 的判定口径（qty ≤ 低库存阈值）。
 *
 * P2 修复：阈值不再写死=10，改为按单位/商品下发。
 * - 后端 /inventory/current 已按单位返回 low_stock_threshold 字段；
 * - 调用方通过 resolveThreshold(item) 取得阈值后传入 inventoryStatus / isLowStock；
 * - 未传阈值时回退 DEFAULT_LOW_STOCK_THRESHOLD，保持向后兼容。
 */

/** 默认余量较少阈值：大于 0 且 ≤ 此值视为 low（仅数量提示，不代表必须补货）。 */
var DEFAULT_LOW_STOCK_THRESHOLD = 10;

/**
 * 取商品的可靠数量（优先 current_qty，回退 total_qty）。
 * 兼容后端两种字段命名；NaN/null 兜底为 0，负值原样透传（符号由下游判定处理）。
 * 行为与原 inventory/index 页面的 `Number(x) || 0` 一致。
 * @param {object} item - 库存记录
 * @returns {number}
 */
function resolveQty(item) {
  if (!item) return 0;
  var raw = item.current_qty != null ? item.current_qty : item.total_qty;
  return Number(raw) || 0;
}

/**
 * 取商品的"余量较少"阈值。
 * 优先使用后端按单位/商品下发的 low_stock_threshold；
 * 未提供或非正数则回退到 DEFAULT_LOW_STOCK_THRESHOLD。
 * 修复 P2：原写死=10 对菜摊/肉摊/调料摊均不合适。
 * @param {object} item - 库存记录（含可选 low_stock_threshold 字段）
 * @returns {number}
 */
function resolveThreshold(item) {
  if (item && Number(item.low_stock_threshold) > 0) {
    return Number(item.low_stock_threshold);
  }
  return DEFAULT_LOW_STOCK_THRESHOLD;
}

/**
 * 判定库存状态。
 *   qty ≤ 0           → 'empty'    已售罄
 *   0 < qty ≤ 阈值     → 'low'      余量较少
 *   qty > 阈值         → 'healthy'  有库存
 * @param {number} qty
 * @param {number} [threshold] - 可选，未传用默认阈值
 * @returns {'empty'|'low'|'healthy'}
 */
function inventoryStatus(qty, threshold) {
  var q = Number(qty) || 0;
  var t = Number(threshold) > 0 ? Number(threshold) : DEFAULT_LOW_STOCK_THRESHOLD;
  if (q <= 0) return 'empty';
  if (q <= t) return 'low';
  return 'healthy';
}

/** 是否属于"余量较少"（用于首页计数与待办触发）。 */
function isLowStock(qty, threshold) {
  var q = Number(qty) || 0;
  var t = Number(threshold) > 0 ? Number(threshold) : DEFAULT_LOW_STOCK_THRESHOLD;
  return q > 0 && q <= t;
}

/** 是否仍有库存（qty > 0）。 */
function isInStock(qty) {
  return (Number(qty) || 0) > 0;
}

/** 状态中文标签。 */
function statusText(status) {
  if (status === 'healthy') return '有库存';
  if (status === 'low') return '余量较少';
  return '已售罄';
}

/** 状态辅助说明（提示用户如何解读）。 */
function statusHint(status) {
  if (status === 'healthy') return '当前仍有库存';
  if (status === 'low') return '仅按数量提示，请结合销量判断是否补货';
  return '如仍在售，请安排补货或校准库存';
}

module.exports = {
  DEFAULT_LOW_STOCK_THRESHOLD: DEFAULT_LOW_STOCK_THRESHOLD,
  // 向后兼容别名（测试与历史调用方仍使用 LOW_STOCK_THRESHOLD）。
  LOW_STOCK_THRESHOLD: DEFAULT_LOW_STOCK_THRESHOLD,
  resolveQty: resolveQty,
  resolveThreshold: resolveThreshold,
  inventoryStatus: inventoryStatus,
  isLowStock: isLowStock,
  isInStock: isInStock,
  statusText: statusText,
  statusHint: statusHint,
};
