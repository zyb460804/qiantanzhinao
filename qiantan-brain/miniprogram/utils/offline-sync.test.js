const { describe, it } = require('node:test');
const assert = require('node:assert');
const { OfflineQueueCore, SyncEngine, uuidv4 } = require('./offline-sync');

function makeStorage() {
  const store = {};
  return {
    get: (k) => store[k],
    set: (k, v) => { store[k] = v; },
  };
}

describe('OfflineQueueCore', () => {
  it('enqueues and reads back pending item', () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    const item = queue.enqueue({ idempotency_key: 'k1', event_type: 'sale', total_amount: 10 });
    assert.equal(item.status, 'pending');
    assert.equal(queue.counts().pending, 1);
  });

  it('throws without idempotency_key', () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    assert.throws(() => queue.enqueue({ event_type: 'sale' }), /idempotency_key/);
  });

  it('marks synced and removes item', () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    queue.enqueue({ idempotency_key: 'k1', event_type: 'sale' });
    queue.enqueue({ idempotency_key: 'k2', event_type: 'sale' });
    queue.markSynced('k1');
    assert.equal(queue.counts().pending, 1);
  });

  it('markFailed increments retry_count and keeps pending', () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    queue.enqueue({ idempotency_key: 'k1', event_type: 'sale' });
    queue.markFailed('k1', 'network_error');
    const items = queue.storage.get(queue.key);
    assert.equal(items[0].status, 'pending');
    assert.equal(items[0].retry_count, 1);
  });

  it('prunes old items', () => {
    const storage = makeStorage();
    const queue = new OfflineQueueCore({ storage, ttlDays: 1 });
    const now = Date.now();
    storage.set(queue.key, [
      { idempotency_key: 'new', created_at: now, status: 'pending' },
      { idempotency_key: 'old', created_at: now - 2 * 24 * 60 * 60 * 1000, status: 'pending' },
    ]);
    assert.equal(queue.prune(), 1);
  });
});

describe('SyncEngine', () => {
  it('transmits pending items and marks synced', async () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    queue.enqueue({ idempotency_key: 'k1', event_type: 'sale' });
    queue.enqueue({ idempotency_key: 'k2', event_type: 'sale' });

    const transmitted = [];
    const engine = new SyncEngine({
      queue,
      transmitter: async (item) => { transmitted.push(item.idempotency_key); },
    });
    await engine.start();

    assert.deepStrictEqual(transmitted.sort(), ['k1', 'k2']);
    assert.equal(queue.counts().pending, 0);
  });

  it('keeps business failures in the queue instead of deleting them', async () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    queue.enqueue({ idempotency_key: 'biz-fail', event_type: 'sale' });
    const engine = new SyncEngine({
      queue,
      transmitter: async () => { throw { type: 'business_error' }; },
      baseDelayMs: 1,
    });
    await engine.start();
    const items = queue.storage.get(queue.key);
    assert.equal(items.length, 1);
    assert.equal(items[0].status, 'pending');
    assert.equal(items[0].last_error, 'business_error');
  });

  it('moves exhausted records to failed and allows manual retry', async () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    queue.enqueue({ idempotency_key: 'dead', event_type: 'sale' });
    const items = queue.storage.get(queue.key);
    items[0].retry_count = 2;
    queue.storage.set(queue.key, items);
    const engine = new SyncEngine({ queue, transmitter: async () => {}, maxRetries: 2 });
    await engine.start();
    assert.equal(queue.counts().failed, 1);
    assert.equal(queue.retryFailed('dead'), true);
    assert.equal(queue.counts().pending, 1);
  });

  it('marks failed on network_error and retries with backoff', async () => {
    const queue = new OfflineQueueCore({ storage: makeStorage() });
    queue.enqueue({ idempotency_key: 'k1', event_type: 'sale' });

    let attempts = 0;
    const engine = new SyncEngine({
      queue,
      transmitter: async () => { attempts++; throw { type: 'network_error' }; },
      baseDelayMs: 10,
    });
    await engine.start();

    assert.equal(attempts, 1); // one failure per item, queue keeps pending
    const items = queue.storage.get(queue.key);
    assert.equal(items[0].status, 'pending');
    assert.equal(items[0].retry_count, 1);
    assert.equal(items[0].last_error, 'network_error');
  });
});

describe('uuidv4', () => {
  it('generates 36-char string with version 4', () => {
    const id = uuidv4();
    assert.equal(id.length, 36);
    assert.equal(id[14], '4');
  });
});
