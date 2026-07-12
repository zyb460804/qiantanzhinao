/**
 * sync-engine.js — 离线 → 联网 同步引擎（参考实现 / 教学样例）
 * ───────────────────────────────────────────────────────────────────────
 * 对应 PRD §4.1.5 同步引擎：
 *   - 触发：wx.onNetworkStatusChange(isConnected) / 用户手动「立即同步」/ 冷启动联网
 *   - 串行 FIFO 处理；syncing 锁防并发双跑
 *   - 单条失败指数退避重试（最多 5 次：2/4/8/16/32s），仍失败置 failed 保留
 *   - 业务错（商品已删等）→ 标记 conflict，不再无限重试，交由「冲突中心」(R6)
 *   - 网络错 → 持续退避
 *   - 过程对用户无感：仅通过 onState 回调驱动顶部状态条 / 同步报告 (R5)
 *
 * 设计要点：核心只依赖注入的 `transmitter`（上送单条的函数），
 *  thus 完全可在 Node 里用假 transmitter 单测，无需 wx。
 */

const BACKOFF_MS = [2000, 4000, 8000, 16000, 32000]; // 2/4/8/16/32s
const MAX_RETRY = 5;

export class SyncEngine {
  /**
   * @param {object} opts
   * @param {import('./offline-queue').OfflineQueueCore} opts.queue
   * @param {function} opts.transmitter  async (item) => any；抛错需带 { type:'network'|'business', message }
   * @param {function} [opts.onState]    (evt) => void  事件：syncing|synced|conflict|failed|done
   */
  constructor({ queue, transmitter, onState }) {
    this.queue = queue;
    this.transmitter = transmitter;
    this.onState = onState || (() => {});
    this.syncing = false; // 防并发双跑锁
  }

  /** 网络恢复 / 手动触发时调用。已在上锁则直接忽略（防双跑）。 */
  start() {
    if (this.syncing) return;
    this._run().catch((e) => {
      console.error('[sync-engine] 未预期错误', e);
      this.syncing = false;
    });
  }

  async _run() {
    this.syncing = true;
    this.onState({ type: 'syncing' });

    // FIFO 串行：每条处理完再取下一条；同一失败条目会就地退避重试直到终态
    let item;
    while ((item = this.queue.nextPending())) {
      await this._syncOne(item);
    }

    this.syncing = false;
    this.onState({ type: 'done', summary: this.queue.counts() }); // R5 同步报告数据源
  }

  async _syncOne(item) {
    this.queue.markSyncing(item.client_id);
    try {
      // 上送 payload + client_id + merchant_id；后端做幂等去重（见 backend 参考实现）
      await this.transmitter(item);
      this.queue.markSynced(item.client_id);
      this.onState({ type: 'synced', client_id: item.client_id });
    } catch (e) {
      const kind = e && e.type; // 'network' | 'business'
      if (kind === 'business') {
        // 业务错：商品批次已作废等 → 标记 conflict，等用户处理，不无限重试
        this.queue.markConflict(item.client_id, e.message || '业务冲突');
        this.onState({ type: 'conflict', client_id: item.client_id, reason: e.message });
      } else {
        // 网络错：指数退避重试（最多 MAX_RETRY 次）
        if ((item.retry || 0) >= MAX_RETRY) {
          this.queue.markFailed(item.client_id, e && e.message);
          this.onState({ type: 'failed', client_id: item.client_id });
        } else {
          this.queue.incrementRetry(item.client_id);
          await this._backoff(item.retry); // 退避后 while 循环会再次取到这条 pending
        }
      }
    }
  }

  _backoff(retry) {
    const idx = Math.min(retry, BACKOFF_MS.length - 1);
    return new Promise((r) => setTimeout(r, BACKOFF_MS[idx]));
  }
}

/**
 * 生产用 transmitter 工厂：把 app.request 包成引擎需要的形态。
 * 关键：把后端响应映射成 { type:'network'|'business' }，引擎据此分流重试/冲突。
 */
export function createWxTransmitter(app) {
  return async function transmit(item) {
    try {
      // 注意：这里必须一并上送 client_id 与 merchant_id（幂等 + 多租户隔离）
      await app.request({
        url: '/offline/sync',
        method: 'POST',
        data: {
          client_id: item.client_id,
          merchant_id: item.merchant_id,
          kind: item.kind,
          payload: item.payload,
        },
      });
      // 后端对重复 client_id 返回 200（既有记录），我们视为成功（幂等）
      return;
    } catch (err) {
      // 与 app.request 的错误契约对齐（见 app.js）
      if (err && err.type === 'business_error') {
        throw { type: 'business', message: (err.body && err.body.message) || '业务冲突' };
      }
      if (err && err.type === 'not_found') {
        // 404 = 跨商户访问被拒（隔离生效），按业务冲突处理，避免无限重试
        throw { type: 'business', message: '记录不存在或无权限' };
      }
      throw { type: 'network', message: (err && err.type) || 'network' };
    }
  };
}
