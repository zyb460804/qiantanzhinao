/**
 * offline-queue.js — 离线记账持久化队列（参考实现 / 教学样例）
 * ───────────────────────────────────────────────────────────────────────
 * 设计目标（对应 PRD §4.1.1 持久化队列 / §4.1.2 幂等键 / §6.2 安全合规）：
 *
 *   1. 写入即落盘；App 被杀后重启仍能读出未同步项（崩溃可恢复）。
 *   2. 每条记录携带客户端生成的 client_id（UUID v4）—— 这是「不记两次」的
 *      唯一可信保证，必须由后端唯一约束兜底（见 backend/ 参考实现）。
 *   3. 落盘前对整条队列做「静态加密」（at-rest），敏感字段不以明文驻留。
 *   4. 队列上限 1000 条 / 2MB，触顶「拒绝并提示」，绝不静默丢弃。
 *   5. 与 wx 解耦：核心逻辑是纯函数，可在 Node 里直接单测（见 *.test.js）。
 *
 * ⚠️ 这是「参考实现」，目的是统一团队对离线队列的正确认知。
 *    接入生产时把它迁进 miniprogram/utils/，并按项目约定替换密钥派生方式。
 */

// ── 常量 ────────────────────────────────────────────────────────────────
const MAX_ITEMS = 1000;
const MAX_BYTES = 2 * 1024 * 1024; // 2MB（setStorageSync 单键上限约 1MB，生产大体积走文件队列）
const DEFAULT_TTL_SYNCED_DAYS = 7; // R7：已同步项本地保留 7 天后清理
const DEFAULT_TTL_UNSYNCED_DAYS = 30; // R7：图片/音频类 30 天未同步过期删除

// ── 依赖注入的随机源（测试可注入确定性 rng）──────────────────────────────
// 生产环境请优先使用 wx.getRandomValues（基础库 ≥ 2.19.0），降级再用 Math.random。
export function uuidv4(rng = Math.random) {
  const hex = '0123456789abcdef';
  let out = '';
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) { out += '-'; continue; }
    if (i === 14) { out += '4'; continue; } // version 4
    if (i === 19) { out += hex[(Math.floor(rng() * 4) + 8)]; continue; } // variant
    out += hex[Math.floor(rng() * 16)];
  }
  return out;
}

// ── 加密（at-rest）──────────────────────────────────────────────────────
// 生产必须替换为真 AES-GCM：密钥经微信隐私接口 / 用户手势派生，绝不硬编码落盘。
// 这里提供两个占位实现，仅用于演示「存储边界加密」这一接缝（seam）：
//   - NoOpCipher ：开发/测试用，明文（方便调试）
//   - XorCipher  ：演示用，⚠️ 不安全，仅展示接口形态，切勿上生产
export const NoOpCipher = { encrypt: (s) => s, decrypt: (s) => s };

export function createXorCipher(key) {
  const kb = Array.from(Buffer.from(key, 'utf8'));
  const xor = (s) => {
    const b = Array.from(Buffer.from(s, 'utf8'));
    return Buffer.from(b.map((v, i) => v ^ kb[i % kb.length])).toString('base64');
  };
  const unxor = (c) => {
    const b = Array.from(Buffer.from(c, 'base64'));
    return Buffer.from(b.map((v, i) => v ^ kb[i % kb.length])).toString('utf8');
  };
  return { encrypt: xor, decrypt: unxor };
}

// ── 错误类型 ──────────────────────────────────────────────────────────────
export class QueueFullError extends Error {
  constructor(message) {
    super(message);
    this.name = 'QueueFullError';
    this.code = 'QUEUE_FULL';
  }
}

// ── 核心队列（纯逻辑，无 wx 依赖）────────────────────────────────────────
/**
 * @param {object} opts
 * @param {object} opts.persister  - { load(): string|null, save(s: string): void }
 * @param {object} [opts.cipher]   - { encrypt(s), decrypt(s) } 默认 NoOpCipher
 * @param {number} [opts.maxItems]
 * @param {number} [opts.maxBytes]
 */
export class OfflineQueueCore {
  constructor({ persister, cipher = NoOpCipher, maxItems = MAX_ITEMS, maxBytes = MAX_BYTES }) {
    if (!persister || typeof persister.load !== 'function' || typeof persister.save !== 'function') {
      throw new Error('OfflineQueueCore 需要一个 persister { load, save }');
    }
    this.persister = persister;
    this.cipher = cipher;
    this.maxItems = maxItems;
    this.maxBytes = maxBytes;
    this.items = this._load(); // 构造即恢复（崩溃可恢复）
  }

  // 读取并解密；崩溃恢复：上次停在 syncing 的条目视为未完成 → 重置为 pending
  _load() {
    const raw = this.persister.load();
    if (!raw) return [];
    try {
      const text = this.cipher.decrypt(raw);
      const arr = JSON.parse(text);
      if (!Array.isArray(arr)) return [];
      return arr.map((it) =>
        it.status === 'syncing' ? { ...it, status: 'pending', retry: it.retry || 0 } : it
      );
    } catch {
      // 存储损坏兜底：清空，避免白屏（极端情况会丢本地未同步项，但优于崩溃）
      return [];
    }
  }

  _persist() {
    const text = JSON.stringify(this.items);
    if (text.length > this.maxBytes) {
      // 理论上 enqueue 已限容，这里再兜底一道
      throw new QueueFullError('离线队列超出存储上限');
    }
    this.persister.save(this.cipher.encrypt(text));
  }

  /**
   * 入队一条离线记账。
   * @param {string} kind   voice_text | voice_audio | cashier | vision | purchase_confirm
   * @param {object} payload { event_type, product, qty, amount, ... }
   * @param {object} [meta] { merchantId, clientId }
   * @returns {object} 入队的条目（含 client_id）
   */
  enqueue(kind, payload, { merchantId, clientId = uuidv4() } = {}) {
    if (this.items.length >= this.maxItems) {
      throw new QueueFullError('离线队列已满，请联网同步'); // PRD §8.1：拒绝 + 提示，不静默丢弃
    }
    const item = {
      client_id: clientId,
      merchant_id: merchantId,
      kind,
      payload,
      created_at: Date.now(),
      status: 'pending', // pending | syncing | synced | failed | conflict
      retry: 0,
      last_error: null,
      synced_at: null,
    };
    this.items.push(item);
    this._persist();
    return item;
  }

  find(clientId) {
    return this.items.find((it) => it.client_id === clientId) || null;
  }

  nextPending() {
    return this.items.find((it) => it.status === 'pending') || null; // FIFO
  }

  pending() {
    return this.items.filter((it) => it.status === 'pending');
  }

  // 按状态计数，供「离线汇总卡」(R2) 与同步报告 (R5)
  counts() {
    const c = { pending: 0, syncing: 0, synced: 0, failed: 0, conflict: 0 };
    for (const it of this.items) c[it.status] = (c[it.status] || 0) + 1;
    const pendingAmount = this.pending().reduce(
      (sum, it) => sum + (Number(it.payload?.amount) || 0),
      0
    );
    return { ...c, total: this.items.length, pendingAmount: Math.round(pendingAmount * 100) / 100 };
  }

  // ── 状态迁移（由 sync-engine 调用）─────────────────────────────────────
  markSyncing(clientId) {
    const it = this.find(clientId);
    if (it) { it.status = 'syncing'; this._persist(); }
  }
  markSynced(clientId) {
    const it = this.find(clientId);
    if (it) { it.status = 'synced'; it.synced_at = Date.now(); this._persist(); }
  }
  markConflict(clientId, reason) {
    const it = this.find(clientId);
    if (it) { it.status = 'conflict'; it.last_error = reason; this._persist(); }
  }
  markFailed(clientId, reason) {
    const it = this.find(clientId);
    if (it) { it.status = 'failed'; it.last_error = reason; this._persist(); }
  }
  incrementRetry(clientId) {
    const it = this.find(clientId);
    if (it) { it.retry = (it.retry || 0) + 1; this._persist(); }
  }
  remove(clientId) {
    const i = this.items.findIndex((it) => it.client_id === clientId);
    if (i >= 0) { this.items.splice(i, 1); this._persist(); }
  }

  // ── 存储清理（R7）──────────────────────────────────────────────────────
  // 已同步且超过保留期 → 删除；图片/音频类超过未同步期 → 过期删除
  prune({ syncedDays = DEFAULT_TTL_SYNCED_DAYS, unsyncedDays = DEFAULT_TTL_UNSYNCED_DAYS, now = Date.now() } = {}) {
    const syncedCut = now - syncedDays * 86400000;
    const unsyncedCut = now - unsyncedDays * 86400000;
    const before = this.items.length;
    this.items = this.items.filter((it) => {
      if (it.status === 'synced' && (it.synced_at || it.created_at) < syncedCut) return false;
      if ((it.kind === 'vision' || it.kind === 'voice_audio') && it.status !== 'synced' && it.created_at < unsyncedCut) {
        return false; // 隐私 + 存储双考量：过期未同步的媒体项删除
      }
      return true;
    });
    if (this.items.length !== before) this._persist();
    return before - this.items.length;
  }
}

// ── wx 适配器：把微信本地存储接成 persister ───────────────────────────────
const QT_QUEUE_KEY = 'qt_offline_queue';
export function createWxQueue({ cipher = NoOpCipher } = {}) {
  const persister = {
    load: () => {
      try { return wx.getStorageSync(QT_QUEUE_KEY) || null; }
      catch { return null; }
    },
    save: (s) => { try { wx.setStorageSync(QT_QUEUE_KEY, s); } catch (e) { throw new QueueFullError('本地存储写入失败：可能已满'); } },
  };
  return new OfflineQueueCore({ persister, cipher });
}
