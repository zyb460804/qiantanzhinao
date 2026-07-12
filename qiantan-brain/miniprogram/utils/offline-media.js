/**
 * 离线媒体上传队列 (§5.9) — 语音/图片离线暂存与重试上传。
 *
 * 场景：
 *   - 摊主拍照记一笔，网络不好时照片暂存本地，联网后自动上传
 *   - 语音记账录音暂存，断网不丢
 *   - 采购凭证、合格证、报损照片等文件类离线上传
 *
 * 设计约束（规格 §5.9）：
 *   1. 使用 wx.saveFile 持久化临时文件
 *   2. 服务端确认成功或重复后才能删除本地文件和任务
 *   3. 网络失败指数退避，业务失败进入 failed，支持手动重试
 *   4. 文件丢失时显示明确错误
 *   5. 控制本地存储上限并提供清理策略
 *   6. 上传携带 JWT，401 只允许重新登录并重试一次
 *
 * 使用方式：
 *   var offlineMedia = require('../../utils/offline-media');
 *   var queue = offlineMedia.getQueue();
 *
 *   // 入队
 *   queue.enqueue({
 *     filePath: res.tempFilePath,  // wx.chooseImage 返回的临时路径
 *     mediaType: 'image',           // image / audio / document
 *     businessType: 'purchase_cert', // 业务类型
 *     businessPayload: { purchase_id: '...' },
 *   });
 *
 *   // 手动触发同步（也可在 app.onShow 时自动触发）
 *   queue.syncAll();
 */

var STORAGE_KEY = 'qt_offline_media_queue';
var MAX_RETRIES = 5;
var MAX_TOTAL_SIZE_MB = 500;  // 本地总上限
var BASE_DELAY_MS = 2000;     // 首次重试间隔

/** 简单 UUID 生成 */
function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = (Math.random() * 16) | 0;
    var v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/** 指数退避延迟（毫秒） */
function backoffDelay(retryCount) {
  return Math.min(BASE_DELAY_MS * Math.pow(2, retryCount), 300000); // 最多 5 分钟
}

/**
 * 离线媒体任务队列
 */
function OfflineMediaQueue(options) {
  this.storage = options.storage;
  this.key = options.key || STORAGE_KEY;
  this.maxSizeMB = options.maxSizeMB || MAX_TOTAL_SIZE_MB;
  this.uploadUrl = options.uploadUrl || '';  // 服务端上传接口
  this.syncing = false;
}

OfflineMediaQueue.prototype._read = function () {
  var raw = this.storage.get(this.key);
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  try { return JSON.parse(raw); } catch (e) { return []; }
};

OfflineMediaQueue.prototype._write = function (items) {
  this.storage.set(this.key, items);
};

/**
 * 将临时文件持久化并入队
 *
 * @param {Object} opts
 * @param {string} opts.filePath     - 临时文件路径（wx.chooseImage/chooseMessageFile 返回）
 * @param {string} opts.mediaType    - image / audio / document
 * @param {string} opts.businessType - purchase_cert / quality_cert / waste_photo / voice_note / stocktake_photo
 * @param {Object} opts.businessPayload - 关联业务数据
 * @returns {Object} 任务对象
 */
OfflineMediaQueue.prototype.enqueue = function (opts) {
  var self = this;
  var idempotencyKey = uuidv4();
  var task = {
    id: uuidv4(),
    idempotency_key: idempotencyKey,
    savedPath: null,
    mediaType: opts.mediaType || 'image',
    businessType: opts.businessType || 'other',
    businessPayload: opts.businessPayload || {},
    status: 'pending',     // pending / saving / queued / uploading / synced / failed / missing
    retries: 0,
    nextRetryAt: null,
    errorMessage: null,
    serverFileId: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  // Step 1: persist temp file to local storage
  task.status = 'saving';
  var items = self._read();
  items.push(task);
  self._write(items);

  wx.saveFile({
    tempFilePath: opts.filePath,
    success: function (res) {
      task.savedPath = res.savedFilePath;
      task.status = 'queued';
      task.updatedAt = new Date().toISOString();
      self._updateTask(task);
      // 立即尝试上传
      self._uploadOne(task);
    },
    fail: function (err) {
      task.status = 'failed';
      task.errorMessage = '文件保存失败: ' + (err.errMsg || '未知错误');
      task.updatedAt = new Date().toISOString();
      self._updateTask(task);
    }
  });

  return task;
};

OfflineMediaQueue.prototype._updateTask = function (task) {
  var items = this._read();
  for (var i = 0; i < items.length; i++) {
    if (items[i].id === task.id) {
      items[i] = task;
      break;
    }
  }
  this._write(items);
};

/**
 * 上传单个任务
 */
OfflineMediaQueue.prototype._uploadOne = function (task) {
  var self = this;
  if (task.status === 'synced' || task.status === 'uploading') return;

  // Check file exists
  if (!task.savedPath) {
    task.status = 'missing';
    task.errorMessage = '本地文件已丢失，请重新采集';
    task.updatedAt = new Date().toISOString();
    self._updateTask(task);
    return;
  }

  task.status = 'uploading';
  task.updatedAt = new Date().toISOString();
  self._updateTask(task);

  var app = getApp();
  var token = app.globalData.token || '';

  wx.uploadFile({
    url: self.uploadUrl || (app.globalData.apiBase || '') + '/api/v1/media/upload',
    filePath: task.savedPath,
    name: 'file',
    header: {
      'Authorization': 'Bearer ' + token,
      'X-Idempotency-Key': task.idempotency_key,
    },
    formData: {
      media_type: task.mediaType,
      business_type: task.businessType,
      business_payload: JSON.stringify(task.businessPayload),
      idempotency_key: task.idempotency_key,
    },
    success: function (res) {
      if (res.statusCode === 200 || res.statusCode === 201 || res.statusCode === 409) {
        // 200/201: success, 409: duplicate (idempotent)
        var data = JSON.parse(res.data);
        task.status = 'synced';
        task.serverFileId = (data.data && data.data.file_id) || null;
        task.updatedAt = new Date().toISOString();
        self._updateTask(task);
        // 成功后删除本地文件
        self._cleanLocalFile(task);
        // 从队列中移除已同步的任务
        self._removeSynced();
      } else if (res.statusCode === 401) {
        // 401: token expired — retry after re-login (once)
        if (task.retries < 1) {
          task.retries += 1;
          task.status = 'queued';
          task.nextRetryAt = new Date(Date.now() + 5000).toISOString();
        } else {
          task.status = 'failed';
          task.errorMessage = '登录已过期，请重新登录后重试';
        }
        task.updatedAt = new Date().toISOString();
        self._updateTask(task);
      } else {
        self._handleUploadFailure(task, '上传失败: HTTP ' + res.statusCode);
      }
    },
    fail: function (err) {
      self._handleUploadFailure(task, '网络错误: ' + (err.errMsg || '未知'));
    },
  });
};

OfflineMediaQueue.prototype._handleUploadFailure = function (task, errorMsg) {
  task.retries += 1;
  if (task.retries >= MAX_RETRIES) {
    task.status = 'failed';
    task.errorMessage = errorMsg;
  } else {
    task.status = 'queued';
    task.errorMessage = errorMsg;
    task.nextRetryAt = new Date(Date.now() + backoffDelay(task.retries)).toISOString();
  }
  task.updatedAt = new Date().toISOString();
  this._updateTask(task);
};

/**
 * 同步所有待上传任务（联网时调用）
 */
OfflineMediaQueue.prototype.syncAll = function () {
  if (this.syncing) return;
  this.syncing = true;
  var self = this;
  var items = self._read();
  var now = Date.now();

  var pending = items.filter(function (t) {
    if (t.status === 'synced') return false;
    if (t.status === 'failed') return false;
    if (t.status === 'missing') return false;
    if (t.nextRetryAt && new Date(t.nextRetryAt).getTime() > now) return false;
    return true;
  });

  // Upload sequentially to avoid overwhelming the network
  function next(index) {
    if (index >= pending.length) {
      self.syncing = false;
      return;
    }
    self._uploadOne(pending[index]);
    setTimeout(function () { next(index + 1); }, 500);
  }
  next(0);
};

/**
 * 手动重试单个失败任务
 */
OfflineMediaQueue.prototype.retryTask = function (taskId) {
  var items = this._read();
  for (var i = 0; i < items.length; i++) {
    if (items[i].id === taskId) {
      items[i].retries = 0;
      items[i].status = 'queued';
      items[i].errorMessage = null;
      items[i].nextRetryAt = null;
      items[i].updatedAt = new Date().toISOString();
      this._write(items);
      this._uploadOne(items[i]);
      return;
    }
  }
};

/**
 * 清理已同步任务（保留最近 50 条历史供 UI 展示）
 */
OfflineMediaQueue.prototype._removeSynced = function () {
  var items = this._read();
  var synced = items.filter(function (t) { return t.status === 'synced'; });
  var others = items.filter(function (t) { return t.status !== 'synced'; });
  // Keep most recent 50 synced tasks for display
  if (synced.length > 50) {
    synced = synced.slice(-50);
  }
  this._write(others.concat(synced));
};

/**
 * 清理单个已同步任务的本地文件
 */
OfflineMediaQueue.prototype._cleanLocalFile = function (task) {
  if (!task.savedPath) return;
  var fs = wx.getFileSystemManager();
  try {
    fs.unlinkSync(task.savedPath);
  } catch (e) {
    // File already gone — that's fine
  }
};

/**
 * 获取队列统计信息
 */
OfflineMediaQueue.prototype.getStats = function () {
  var items = this._read();
  var pending = 0, uploading = 0, failed = 0, synced = 0, missing = 0;
  items.forEach(function (t) {
    if (t.status === 'queued') pending++;
    else if (t.status === 'uploading') uploading++;
    else if (t.status === 'failed') failed++;
    else if (t.status === 'synced') synced++;
    else if (t.status === 'missing') missing++;
  });
  return {
    total: items.length,
    pending: pending,
    uploading: uploading,
    failed: failed,
    synced: synced,
    missing: missing,
  };
};

/**
 * 清除所有已完成/失败的任务（释放本地存储空间）
 */
OfflineMediaQueue.prototype.clearCompleted = function () {
  var items = this._read();
  var toKeep = items.filter(function (t) {
    var keep = t.status === 'queued' || t.status === 'uploading' || t.status === 'saving' || t.status === 'pending';
    if (!keep && t.savedPath) {
      // 同时删除本地文件
      var fs = wx.getFileSystemManager();
      try { fs.unlinkSync(t.savedPath); } catch (e) {}
    }
    return keep;
  });
  this._write(toKeep);
  return items.length - toKeep.length;
};

// ── 单例 ──────────────────────────────────────────

var _queue = null;

function createWxQueue() {
  if (_queue) return _queue;
  _queue = new OfflineMediaQueue({
    storage: {
      get: function (key) {
        try {
          var raw = wx.getStorageSync(key);
          return raw;
        } catch (e) {
          return null;
        }
      },
      set: function (key, val) {
        try {
          wx.setStorageSync(key, val);
        } catch (e) {
          console.error('[offline-media] setStorageSync failed:', e);
        }
      },
    },
  });
  return _queue;
}

module.exports = {
  OfflineMediaQueue: OfflineMediaQueue,
  getQueue: createWxQueue,
  uuidv4: uuidv4,
};
