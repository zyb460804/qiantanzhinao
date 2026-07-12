/**
 * 离线记账 / 断网同步 —— 客户端持久化队列与同步引擎。
 *
 * 设计目标：
 *   1. 核心逻辑不依赖 wx / app 实例，可通过依赖注入在 Node 测试。
 *   2. wx 环境使用 createWxQueueSync() 工厂一行接入。
 *   3. 幂等键由客户端生成，服务端按 idempotency_key 唯一约束去重。
 *
 * 使用方式（pos.js）：
 *   var offlineSync = require('../../utils/offline-sync');
 *   var queue = offlineSync.getQueue();           // 单例
 *   queue.enqueue({ idempotency_key, event_type:'sale', ... });
 *   queue.sync();                                 // 有网则立即同步
 */

var STORAGE_KEY = 'qt_offline_queue';
var MAX_RETRIES = 5;

function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = (Math.random() * 16) | 0;
    var v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function clone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

/**
 * 持久化队列核心。
 * 只负责本地队列的读写、状态流转，不直接发网络请求。
 */
function OfflineQueueCore(options) {
  this.storage = options.storage;          // { get(key), set(key, val) }
  this.key = options.key || STORAGE_KEY;
  this.maxItems = options.maxItems || 1000;
  this.ttlDays = options.ttlDays || 7;
}

OfflineQueueCore.prototype._read = function () {
  var raw = this.storage.get(this.key);
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  try { return JSON.parse(raw); } catch (e) { return []; }
};

OfflineQueueCore.prototype._write = function (items) {
  this.storage.set(this.key, items);
};

OfflineQueueCore.prototype.enqueue = function (item) {
  if (!item || !item.idempotency_key) {
    throw new Error('enqueue requires item.idempotency_key');
  }
  var items = this._read();
  if (items.length >= this.maxItems) {
    throw new Error('OfflineQueueFull: max ' + this.maxItems + ' items');
  }
  items.unshift({
    idempotency_key: item.idempotency_key,
    event_type: item.event_type || 'sale',
    product_id: item.product_id || null,
    product_name: item.product_name || null,
    quantity: item.quantity,
    unit: item.unit || '斤',
    unit_cost: item.unit_cost,
    unit_price: item.unit_price,
    total_amount: item.total_amount,
    event_time: item.event_time || new Date().toISOString(),
    notes: item.notes || '',
    source: item.source || 'offline',
    status: 'pending',
    retry_count: 0,
    created_at: Date.now(),
  });
  this._write(items);
  return items[0];
};

OfflineQueueCore.prototype.markSyncing = function (idempotency_key) {
  var items = this._read();
  var found = false;
  for (var i = 0; i < items.length; i++) {
    if (items[i].idempotency_key === idempotency_key) {
      items[i].status = 'syncing';
      found = true;
      break;
    }
  }
  if (found) this._write(items);
  return found;
};

OfflineQueueCore.prototype.markSynced = function (idempotency_key) {
  var items = this._read();
  var next = items.filter(function (it) { return it.idempotency_key !== idempotency_key; });
  this._write(next);
  return items.length !== next.length;
};

OfflineQueueCore.prototype.markFailed = function (idempotency_key, errType) {
  var items = this._read();
  var found = false;
  for (var i = 0; i < items.length; i++) {
    if (items[i].idempotency_key === idempotency_key) {
      items[i].status = 'pending';
      items[i].retry_count = (items[i].retry_count || 0) + 1;
      items[i].last_error = errType || 'unknown';
      found = true;
      break;
    }
  }
  if (found) this._write(items);
  return found;
};

OfflineQueueCore.prototype.markExhausted = function (idempotency_key, errType) {
  var items = this._read();
  var found = false;
  for (var i = 0; i < items.length; i++) {
    if (items[i].idempotency_key === idempotency_key) {
      items[i].status = 'failed';
      items[i].last_error = errType || 'max_retries';
      found = true;
      break;
    }
  }
  if (found) this._write(items);
  return found;
};

OfflineQueueCore.prototype.retryFailed = function (idempotency_key) {
  var items = this._read();
  var found = false;
  for (var i = 0; i < items.length; i++) {
    if (items[i].idempotency_key === idempotency_key && items[i].status === 'failed') {
      items[i].status = 'pending';
      items[i].retry_count = 0;
      items[i].last_error = null;
      found = true;
      break;
    }
  }
  if (found) this._write(items);
  return found;
};

OfflineQueueCore.prototype.counts = function () {
  var counts = { pending: 0, syncing: 0, failed: 0, total: 0 };
  this._read().forEach(function (item) {
    counts.total += 1;
    if (Object.prototype.hasOwnProperty.call(counts, item.status)) counts[item.status] += 1;
  });
  return counts;
};

OfflineQueueCore.prototype.prune = function () {
  var cutoff = Date.now() - this.ttlDays * 24 * 60 * 60 * 1000;
  var items = this._read().filter(function (it) {
    return (it.created_at || 0) > cutoff;
  });
  this._write(items);
  return items.length;
};

/**
 * 同步引擎。
 * 轮询 pending 项，按 FIFO 单条提交，支持指数退避重试。
 */
function SyncEngine(options) {
  this.queue = options.queue;
  this.transmitter = options.transmitter; // function(item) -> Promise
  this.isRunning = false;
  this.maxRetries = options.maxRetries || MAX_RETRIES;
  this.baseDelayMs = options.baseDelayMs || 2000;
  this.onResult = options.onResult || null;
}

SyncEngine.prototype._backoffMs = function (retryCount) {
  return Math.min(this.baseDelayMs * Math.pow(2, retryCount), 32000);
};

SyncEngine.prototype._sleep = function (ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
};

SyncEngine.prototype.start = async function () {
  if (this.isRunning) return;
  this.isRunning = true;
  try {
    await this._run();
  } finally {
    this.isRunning = false;
  }
};

SyncEngine.prototype._run = async function () {
  var items = this.queue._read().filter(function (it) { return it.status === 'pending'; });
  // Process in reverse order so we pop from the oldest (FIFO) via markSynced filter.
  for (var i = items.length - 1; i >= 0; i--) {
    var item = items[i];
    if (item.retry_count >= this.maxRetries) {
      this.queue.markExhausted(item.idempotency_key, 'max_retries');
      if (this.onResult) this.onResult({ idempotency_key: item.idempotency_key, status: 'failed', error: 'max_retries' });
      continue;
    }
    this.queue.markSyncing(item.idempotency_key);
    try {
      await this.transmitter(item);
      this.queue.markSynced(item.idempotency_key);
      if (this.onResult) this.onResult({ idempotency_key: item.idempotency_key, status: 'synced' });
    } catch (err) {
      var errType = err && err.type ? err.type : 'network_error';
      this.queue.markFailed(item.idempotency_key, errType);
      if (this.onResult) this.onResult({ idempotency_key: item.idempotency_key, status: 'failed', error: errType });
      if (errType === 'network_error' || errType === 'server_error') {
        await this._sleep(this._backoffMs(item.retry_count || 0));
      }
    }
  }
};

/**
 * 创建微信小程序环境的队列 + 同步引擎。
 */
function createWxQueueSync(options) {
  options = options || {};
  var storage = {
    get: function (key) { return wx.getStorageSync(key); },
    set: function (key, val) { wx.setStorageSync(key, val); },
  };
  var queue = new OfflineQueueCore({
    storage: storage,
    key: options.key || STORAGE_KEY,
    maxItems: options.maxItems || 1000,
    ttlDays: options.ttlDays || 7,
  });
  var app = getApp();
  var transmitter = options.transmitter || function (item) {
    return app.request({
      url: '/inventory/offline-sync',
      method: 'POST',
      data: { items: [item] },
    }).then(function (data) {
      var result = data && data.results && data.results[0];
      if (!result || (result.status !== 'created' && result.status !== 'duplicate')) {
        throw { type: 'business_error', result: result || null };
      }
      return result;
    });
  };
  var engine = new SyncEngine({
    queue: queue,
    transmitter: transmitter,
    maxRetries: options.maxRetries || MAX_RETRIES,
    baseDelayMs: options.baseDelayMs || 2000,
    onResult: options.onResult,
  });
  return {
    queue: queue,
    engine: engine,
    enqueue: function (item) { return queue.enqueue(item); },
    sync: function () { return engine.start(); },
    counts: function () { return queue.counts(); },
    retryFailed: function (key) { return queue.retryFailed(key); },
  };
}

var _singleton = null;

function getQueue(options) {
  if (!_singleton) _singleton = createWxQueueSync(options);
  return _singleton;
}

module.exports = {
  OfflineQueueCore: OfflineQueueCore,
  SyncEngine: SyncEngine,
  createWxQueueSync: createWxQueueSync,
  getQueue: getQueue,
  uuidv4: uuidv4,
};
