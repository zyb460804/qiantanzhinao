const { describe, it } = require('node:test');
const assert = require('node:assert');

const {
  LOW_STOCK_THRESHOLD,
  resolveQty,
  inventoryStatus,
  isLowStock,
  isInStock,
  statusText,
  statusHint,
} = require('./inventory-status');

describe('resolveQty', () => {
  it('prefers current_qty over total_qty', () => {
    assert.equal(resolveQty({ current_qty: 5, total_qty: 20 }), 5);
  });

  it('falls back to total_qty when current_qty is null', () => {
    assert.equal(resolveQty({ total_qty: 20 }), 20);
  });

  it('returns 0 for missing or invalid values', () => {
    assert.equal(resolveQty(null), 0);
    assert.equal(resolveQty({}), 0);
    assert.equal(resolveQty({ current_qty: 'abc' }), 0);
    assert.equal(resolveQty({ current_qty: null, total_qty: undefined }), 0);
  });

  it('passes negative values through unchanged (sign handled downstream)', () => {
    // 与原页面 Number(x) || 0 行为一致：负数 truthy，原样返回
    assert.equal(resolveQty({ current_qty: -3 }), -3);
  });
});

describe('inventoryStatus', () => {
  it('returns empty when qty ≤ 0', () => {
    assert.equal(inventoryStatus(0), 'empty');
    assert.equal(inventoryStatus(-5), 'empty');
  });

  it('returns low when 0 < qty ≤ threshold', () => {
    assert.equal(inventoryStatus(1), 'low');
    assert.equal(inventoryStatus(LOW_STOCK_THRESHOLD), 'low');
  });

  it('returns healthy when qty > threshold', () => {
    assert.equal(inventoryStatus(LOW_STOCK_THRESHOLD + 1), 'healthy');
    assert.equal(inventoryStatus(100), 'healthy');
  });

  it('coerces string and NaN to safe values', () => {
    assert.equal(inventoryStatus('8'), 'low');
    assert.equal(inventoryStatus(NaN), 'empty');
    assert.equal(inventoryStatus(undefined), 'empty');
  });
});

describe('isLowStock', () => {
  it('true only for positive qty at or below threshold', () => {
    assert.equal(isLowStock(1), true);
    assert.equal(isLowStock(LOW_STOCK_THRESHOLD), true);
    assert.equal(isLowStock(LOW_STOCK_THRESHOLD + 1), false);
  });

  it('false for zero and negative (not "low", but "empty")', () => {
    assert.equal(isLowStock(0), false);
    assert.equal(isLowStock(-1), false);
  });
});

describe('isInStock', () => {
  it('true when qty > 0', () => {
    assert.equal(isInStock(0.5), true);
    assert.equal(isInStock(100), true);
  });

  it('false when qty ≤ 0', () => {
    assert.equal(isInStock(0), false);
    assert.equal(isInStock(-1), false);
  });
});

describe('statusText / statusHint', () => {
  it('statusText maps all three states', () => {
    assert.equal(statusText('healthy'), '有库存');
    assert.equal(statusText('low'), '余量较少');
    assert.equal(statusText('empty'), '已售罄');
    // 未知状态兜底为售罄文案
    assert.equal(statusText('unknown'), '已售罄');
  });

  it('statusHint maps all three states', () => {
    assert.equal(statusHint('healthy'), '当前仍有库存');
    assert.equal(statusHint('low'), '仅按数量提示，请结合销量判断是否补货');
    assert.equal(statusHint('empty'), '如仍在售，请安排补货或校准库存');
  });
});

describe('threshold boundary (regression guard)', () => {
  // 回归保护：阈值变更会同时影响首页待办与库存页警示，必须显式断言
  it('LOW_STOCK_THRESHOLD is 10', () => {
    assert.equal(LOW_STOCK_THRESHOLD, 10);
  });
});
