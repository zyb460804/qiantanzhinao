/**
 * 库存状态判断 — 纯函数模块，不依赖 wx，可在 Node 环境单测。
 *
 * 抽取自 inventory._decorateItems 与 index._renderHomeData/_rebuildTasks
 * 三处重复的阈值逻辑，统一 "余量较少" 的判定口径（qty ≤ LOW_STOCK_THRESHOLD）。
 */

/** 余量较少阈值：大于 0 且 ≤ 此值视为 low（仅数量提示，不代表必须补货）。 */
var LOW_STOCK_THRESHOLD = 10;

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
 * 判定库存状态。
 *   qty ≤ 0           → 'empty'    已售罄
 *   0 < qty ≤ 阈值     → 'low'      余量较少
 *   qty > 阈值         → 'healthy'  有库存
 * @param {number} qty
 * @returns {'empty'|'low'|'healthy'}
 */
function inventoryStatus(qty) {
  var q = Number(qty) || 0;
  if (q <= 0) return 'empty';
  if (q <= LOW_STOCK_THRESHOLD) return 'low';
  return 'healthy';
}

/** 是否属于"余量较少"（用于首页计数与待办触发）。 */
function isLowStock(qty) {
  var q = Number(qty) || 0;
  return q > 0 && q <= LOW_STOCK_THRESHOLD;
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
  LOW_STOCK_THRESHOLD: LOW_STOCK_THRESHOLD,
  resolveQty: resolveQty,
  inventoryStatus: inventoryStatus,
  isLowStock: isLowStock,
  isInStock: isInStock,
  statusText: statusText,
  statusHint: statusHint,
};
