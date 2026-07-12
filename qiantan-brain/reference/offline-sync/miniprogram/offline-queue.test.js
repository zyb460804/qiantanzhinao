import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  OfflineQueueCore,
  NoOpCipher,
  QueueFullError,
  uuidv4,
} from './offline-queue.js';

// 内存 persister，便于在 Node 中原地验证核心逻辑
function memPersister() {
  let store = null;
  return {
    load: () => store,
    save: (s) => { store = s; },
    _get: () => store,
  };
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test('enqueue 生成 client_id(UUIDv4) 并入队为 pending', () => {
  const p = memPersister();
  const q = new OfflineQueueCore({ persister: p });
  const it = q.enqueue('cashier', { event_type: 'sale', amount: 15 }, { merchantId: 'm1' });
  assert.match(it.client_id, UUID_RE);
  assert.equal(it.status, 'pending');
  assert.equal(q.size?.() ?? q.items.length, 1);
});

test('FIFO：nextPending 按入队顺序取首项', () => {
  const q = new OfflineQueueCore({ persister: memPersister() });
  const a = q.enqueue('cashier', { amount: 1 });
  const b = q.enqueue('cashier', { amount: 2 });
  assert.equal(q.nextPending().client_id, a.client_id);
  q.markSynced(a.client_id);
  assert.equal(q.nextPending().client_id, b.client_id);
});

test('崩溃可恢复：重启后停留在 syncing 的条目回到 pending', () => {
  const p = memPersister();
  const q1 = new OfflineQueueCore({ persister: p });
  const it = q1.enqueue('cashier', { amount: 9 });
  q1.markSyncing(it.client_id); // 模拟同步中途 App 被杀
  // 重新构造（模拟重启读盘）
  const q2 = new OfflineQueueCore({ persister: p });
  const recovered = q2.find(it.client_id);
  assert.equal(recovered.status, 'pending', 'syncing 应被重置为 pending');
});

test('容量上限：达到 MAX_ITEMS 后拒绝并入队，抛出 QueueFullError', () => {
  const q = new OfflineQueueCore({ persister: memPersister(), maxItems: 3 });
  q.enqueue('cashier', { amount: 1 });
  q.enqueue('cashier', { amount: 2 });
  q.enqueue('cashier', { amount: 3 });
  assert.throws(() => q.enqueue('cashier', { amount: 4 }), QueueFullError);
});

test('counts：离线汇总卡(R2) 所需的待同步笔数与金额', () => {
  const q = new OfflineQueueCore({ persister: memPersister() });
  q.enqueue('cashier', { amount: 10 });
  q.enqueue('cashier', { amount: 20 });
  q.enqueue('cashier', { amount: 30 });
  const c = q.counts();
  assert.equal(c.pending, 3);
  assert.equal(c.pendingAmount, 60);
  const synced = q.nextPending();
  q.markSynced(synced.client_id);
  assert.equal(q.counts().pending, 2);
  assert.equal(q.counts().synced, 1);
});

test('prune：已同步超保留期被清理（R7）', () => {
  const now = Date.now();
  const q = new OfflineQueueCore({ persister: memPersister() });
  const it = q.enqueue('cashier', { amount: 5 });
  q.markSynced(it.client_id);
  // 把 synced_at 改到 8 天前
  q.find(it.client_id).synced_at = now - 8 * 86400000;
  const removed = q.prune({ syncedDays: 7, now });
  assert.equal(removed, 1);
  assert.equal(q.find(it.client_id), null);
});

test('uuidv4 默认产出合法 v4', () => {
  for (let i = 0; i < 20; i++) assert.match(uuidv4(), UUID_RE);
});
