import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseVoiceText } from './voice-parser.js';

const PRODUCTS = ['西红柿', '土豆', '白菜', '鸡蛋', '猪肉'];

test('卖西红柿3斤15元 → 销售/西红柿/3斤/合计15', () => {
  const r = parseVoiceText('卖西红柿3斤15元', PRODUCTS);
  assert.equal(r.event_type, 'sale');
  assert.equal(r.product, '西红柿');
  assert.equal(r.quantity, 3);
  assert.equal(r.unit, '斤');
  assert.equal(r.total_amount, 15);
  assert.equal(r.total_revenue, 15);
});

test('进了土豆5斤20元 → 采购/土豆/合计20（total_cost）', () => {
  const r = parseVoiceText('进了土豆5斤20元', PRODUCTS);
  assert.equal(r.event_type, 'purchase');
  assert.equal(r.product, '土豆');
  assert.equal(r.quantity, 5);
  assert.equal(r.total_cost, 20);
});

test('未知语义默认归为采购，且回传缺失字段（与服务端一致）', () => {
  const r = parseVoiceText('随便说点什么', PRODUCTS);
  assert.equal(r.event_type, 'purchase'); // 服务端默认 purchase
  assert.ok(r.missing_fields.includes('product'));
  assert.ok(r.missing_fields.includes('quantity'));
  assert.ok(r.confidence < 1);
});

test('中文数字归一：五十斤 → 50斤', () => {
  const r = parseVoiceText('卖了五十斤白菜三十元', PRODUCTS);
  assert.equal(r.quantity, 50);
  assert.equal(r.total_amount, 30);
});
