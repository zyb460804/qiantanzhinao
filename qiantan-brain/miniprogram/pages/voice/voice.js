/**
 * 语音记账页面 v3.2 — 页内纠错卡 / 多意图 / 草稿保护 / 上传进度 / 离线暂存
 *
 * v3.2 要点（低置信兜底路径体验重构）：
 *   - 串行 wx.showModal 纠错弹窗 → 页内纠错卡：商品 chips 点选（解析候选 +
 *     商户 SKU 缓存 + 打字兜底）、数量/金额 digit 数字键盘带单位、
 *     「重说这一项」单字段重录回填。correct 提交协议字段不变。
 *   - 多意图消费：后端 parse-text/upload 返回 events 数组（length>1）时
 *     渲染多张确认卡（逐条确认 + 一键全部确认）；events 缺失走旧单条路径。
 *   - SKU 名缓存：onShow 静默刷新（storage.getSkuNames/setSkuNames，TTL 10min）。
 *
 * v3.1 修复要点：
 *   - 录音改走 utils/recorder.js（顶层单次注册 onStop/onError），消除监听器叠加泄漏。
 *   - 上滑取消录音增加 _cancelledFlag 标志位，确保 onStop 回调丢弃已取消的录音。
 *   - 流式打印只做视觉，parseText 立即并行触发，避免打字停留期延迟入账。
 *   - confirmRecord 增加缺失字段防呆与 antiDuplicate 防双击。
 *   - 上传 fail 时 wx.saveFile 暂存录音文件，pending_retry 状态下可手动重传。
 *   - 上传改走 app.uploadFile 统一上传层：员工身份 401 退出员工身份并提示，不静默回退 owner token。
 *   - onHide 中停止进行中的录音，防止页面隐藏后静默上传。
 *   - 新增 onPullDownRefresh、chooseDialect、retryUpload 入口。
 */
var app = getApp();
var streamText = require('../../utils/stream-text').streamText;
var storage = require('../../utils/storage');
var recorder = require('../../utils/recorder');

Page({
  data: {
    state: 'idle', mode: 'voice', skin: 'noon',
    asrText: '', streamingText: '', textInput: '',
    parsed: null, todayCount: 0, recentLogs: [],
    waveBars: [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17],
    uploadProgress: 0,
    // 方言展示标签（点击切换）
    dialectLabel: '普通话',
    pendingUploadExists: false,
    debugMode: app.globalData && app.globalData.debugMode,
    // ── 多意图（v3.2）：events.length>1 时逐笔渲染 ──
    parsedEvents: null,        // 多意图事件数组（单条/旧后端为 null，走旧渲染）
    parseWarning: '',          // 后端 warning 字段（如部分内容没听清）
    confirmedCount: 0,         // 多意图已确认笔数
    showRecords: false,        // 确认卡是否可操作（独立于 state，重说录音时保持可点）
    // ── 页内纠错卡（v3.2）──
    correction: null,          // {voice_log_id, record, product, quantity, unit, amount, candidates, submitting}
  },

  // 方言代码与展示名映射（与后端 asr_iflytek.py DIALECT_MAP 权威键严格一致）
  _dialectOptions: [
    { code: 'mandarin', name: '普通话' },
    { code: 'cantonese', name: '粤语' },
    { code: 'sichuan', name: '四川话' },
    { code: 'henan', name: '河南话' },
    { code: 'shandong', name: '山东话' },
  ],

  // 旧版设置页/历史存储值 → 权威 code（后端 DIALECT_MAP 同样保留这些兼容键）
  _dialectAliases: {
    sichuanese: 'sichuan',
    shanghainese: 'mandarin',
    southwest: 'mandarin',
  },

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this._syncDialectLabel();
    this.loadTodayCount();
    this._refreshSkuCacheIfNeeded();
  },

  onHide: function () {
    this._clearTypingTimer();
    // 切后台/切 tab 时若仍在录音（含纠错卡「重说这一项」），立即停止并标记取消，
    // 避免 onStop 在隐藏态触发静默上传。
    if (this.data.state === 'listening' || this.data.state === 'respeaking') {
      this._cancelledFlag = true;
      try { recorder.stopRecording(); } catch (e) {}
      if (this.data.state === 'listening') this.setData({ state: 'idle' });
      else this._restoreAfterRespeak();
    }
  },

  onPullDownRefresh: function () {
    // 下拉刷新今日计数和最近记录；500ms 后停止动画，避免转圈无响应。
    this.loadTodayCount();
    setTimeout(function () { wx.stopPullDownRefresh(); }, 500);
  },

  _clearTypingTimer: function () {
    // streamText 返回 {cancel: function} 对象，必须调 .cancel()；
    // cancel() 内部会同时清理打字 interval 与停留 setTimeout。
    if (this._typingTimerId && typeof this._typingTimerId.cancel === 'function') {
      this._typingTimerId.cancel();
    }
    this._typingTimerId = null;
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
  },

  _syncDialectLabel: function () {
    var code = storage.getVoiceDialect();
    // 历史存储值（旧设置页的 sichuanese/shanghainese 等）先归一化再匹配
    var aliasCode = this._dialectAliases[code];
    if (aliasCode) code = aliasCode;
    var match = null;
    for (var i = 0; i < this._dialectOptions.length; i++) {
      if (this._dialectOptions[i].code === code) { match = this._dialectOptions[i]; break; }
    }
    this.setData({ dialectLabel: (match && match.name) || '普通话' });
  },

  chooseDialect: function () {
    var self = this;
    var current = storage.getVoiceDialect();
    var itemList = this._dialectOptions.map(function (it) {
      return it.code === current ? '✓ ' + it.name : it.name;
    });
    wx.showActionSheet({
      itemList: itemList,
      success: function (res) {
        var picked = self._dialectOptions[res.tapIndex];
        if (picked && picked.code !== current) {
          storage.setVoiceDialect(picked.code);
          self.setData({ dialectLabel: picked.name });
          wx.showToast({ title: '已切换：' + picked.name, icon: 'none' });
        }
      },
      fail: function () { /* 用户取消选择，无需处理 */ },
    });
  },

  // ── 模式切换 (草稿保护) ──────────────────
  // v3.2：切换时同步清掉多意图/纠错卡状态，避免确认卡跨模式残留。
  _clearRecordState: function (extra) {
    return Object.assign({
      state: 'idle', parsed: null, asrText: '', streamingText: '',
      parsedEvents: null, parseWarning: '', confirmedCount: 0,
      showRecords: false, correction: null,
    }, extra || {});
  },
  switchToVoice: function () {
    var self = this;
    if (this.data.textInput && this.data.textInput.trim()) {
      wx.showModal({
        title: '切换到语音', content: '文字输入的内容将会丢失，确定切换吗？',
        success: function (res) {
          if (res.confirm) { self._clearTypingTimer(); self.setData(self._clearRecordState({ mode: 'voice', textInput: '' })); }
        },
      });
    } else {
      this._clearTypingTimer(); this.setData(this._clearRecordState({ mode: 'voice' }));
    }
  },
  switchToText: function () { this._clearTypingTimer(); this.setData(this._clearRecordState({ mode: 'text' })); },

  onTextInput: function (e) { this.setData({ textInput: e.detail.value }); },

  submitText: function () {
    var text = this.data.textInput.trim();
    if (!text) { wx.showToast({ title: '请输入内容', icon: 'none' }); return; }
    this.setData({ state: 'processing', asrText: text });
    // 修复：立即发起 parseText（与语音路径对齐），streamReply 仅做视觉，避免打字停留 2.6s 延迟入账。
    this.parseText(text);
    this.streamReply(text);
  },

  // ── 流式字幕（逐字打印）────────────────────
  streamReply: function (text, done) {
    var self = this;
    this._clearTypingTimer();
    this._typingTimerId = streamText(text, function (display) {
      self.setData({ streamingText: display });
    }, done);
  },

  // ── 语音录制 (由 voice-button 组件触发) ─────────
  onStart: function () {
    if (this.data.state !== 'idle' || this.data.mode !== 'voice') return;
    this.setData({ state: 'listening' });
    var self = this;
    // 修复：每次按下都重置取消标志；录音 API 走 utils/recorder.js（顶层单次注册），
    // 不再在本页累积 wx.getRecorderManager().onStop/onError 监听器。
    this._cancelledFlag = false;
    recorder.startRecording().then(function (res) {
      // 已被 onCancel/onHide 标记取消的录音丢弃结果，避免"取消后仍入账"。
      if (self._cancelledFlag) return;
      self.setData({ state: 'uploading' });
      self.handleRecordingResult(res);
    }).catch(function (err) {
      console.error('Record error:', err);
      if (!self._cancelledFlag) self.setData({ state: 'error' });
    });
  },
  onEnd: function () { if (this.data.state === 'listening') recorder.stopRecording(); },
  onCancel: function () {
    if (this.data.state !== 'listening') return;
    // 修复：先置取消标志再 stop，确保 onStop 回调见到 _cancelledFlag=true 走丢弃分支。
    this._cancelledFlag = true;
    try { recorder.stopRecording(); } catch (e) {}
    this.setData({ state: 'idle' });
    wx.showToast({ title: '已取消', icon: 'none' });
  },

  // ── 处理录音结果 ─────────────────────────────
  handleRecordingResult: function (res) {
    var filePath = res && res.tempFilePath;
    if (!filePath) { this.setData({ state: 'error' }); return; }
    this._uploadRecording(filePath);
  },

  // 上传录音文件；uploadPath 可重用，便于失败后从暂存路径重传。
  // H1b：改走 app.uploadFile 统一上传层——不再硬取 owner accessToken，
  // 员工身份 401 由 app 层退出员工身份并提示，杜绝身份静默回退 owner；
  // 无员工身份的 401 由 app 层 ensureLogin 换 token 自动重试一次；
  // 上传进度经 onProgressUpdate 回调透传。
  _uploadRecording: function (filePath) {
    var self = this;
    this.setData({ uploadProgress: 0 });

    app.uploadFile({
      url: '/voice/upload',
      filePath: filePath,
      name: 'audio',
      formData: { dialect: storage.getVoiceDialect() },
      onProgressUpdate: function (res) { self.setData({ uploadProgress: res.progress }); },
    }).then(function (data) {
      if (!data) { self.setData({ state: 'error' }); return; }
      var asrText = data.asr_text || '';
      var parsed = data.parsed;
      var hasEvents = Array.isArray(data.events) && data.events.length > 0;
      if (asrText) {
        self.setData({ asrText: asrText, uploadProgress: 100 });
        if ((parsed && parsed.voice_log_id) || hasEvents) {
          // v3.2：统一走 _applyParsed（多意图 events / 单条 parsed 兼容），
          // 单条时状态判定与旧版一致（conf>=0.8 直确认）。
          self._applyParsed(data);
        } else {
          // 修复：fallback 路径立即发起 parseText，streamReply 仅做视觉。
          // 删除原 _parseResult 守卫（其从未被赋值，恒真会导致双重 parseText 落两条 VoiceLog）。
          self.streamReply(asrText);
          self.parseText(asrText);
        }
      } else {
        self.setData({ state: 'idle', mode: 'text' });
        wx.showToast({ title: '语音识别未成功，请使用文字输入', icon: 'none', duration: 2500 });
      }
    }).catch(function (err) {
      if (self._cancelledFlag) return;
      // 员工身份过期：app 层已退出员工身份并 toast，这里只回到错误态，避免误导性的「网络异常」文案
      if (err && err.type === 'staff_auth_expired') { self.setData({ state: 'error' }); return; }
      // 网络失败：wx.saveFile 持久化临时音频文件，避免断网即丢；提供重传入口。
      if (!err || err.type === 'network_error') {
        wx.saveFile({
          tempFilePath: filePath,
          success: function (saveRes) {
            self._pendingUploadPath = saveRes.savedFilePath;
            self.setData({ state: 'pending_retry', pendingUploadExists: true });
            wx.showToast({ title: '网络异常，已暂存录音，可点重新上传', icon: 'none', duration: 2500 });
          },
          fail: function () {
            self.setData({ state: 'error' });
            wx.showToast({ title: '上传失败，请重试', icon: 'none' });
          },
        });
        return;
      }
      // 业务/服务端错误：直接提示，不暂存
      self.setData({ state: 'error' });
      wx.showToast({ title: (err.body && err.body.detail) || '上传失败，请重试', icon: 'none' });
    });
  },

  // 错误面板"重试"按钮：从暂存路径重新上传（若有），否则回到 idle 让摊主重录。
  retryUpload: function () {
    if (this._pendingUploadPath) {
      var filePath = this._pendingUploadPath;
      this._pendingUploadPath = null;
      this.setData({ state: 'uploading', pendingUploadExists: false, uploadProgress: 0 });
      this._uploadRecording(filePath);
    } else {
      this.resetToIdle();
    }
  },

  // ── 文本解析 (核心) ──────────────────────────
  parseText: function (text) {
    var self = this;
    return app.request({ url: '/voice/parse-text', method: 'POST', data: { text: text } })
      .then(function (res) {
        // v3.2：兼容多意图契约（events 数组 + warning），单条时状态与旧版一致（'success'）。
        self._applyParsed(res, 'success');
      }).catch(function () { self.setData({ state: 'error' }); });
  },

  // ── 解析结果归一化（多意图兼容）──────────────────
  // 后端契约：多意图时 data.events 为数组（length>1，event=events[0] 兼容），
  // 单意图/旧版后端仅有 data.parsed。这里统一成事件数组：
  // 单条 → parsedEvents=null（渲染/逻辑与旧版完全一致）；多条 → parsedEvents 逐笔渲染。
  _extractEvents: function (data) {
    if (!data) return [];
    var raw = data.events;
    var list = [];
    var i;
    if (Array.isArray(raw) && raw.length > 0) {
      for (i = 0; i < raw.length; i++) {
        if (!raw[i]) continue;
        var ev = Object.assign({}, raw[i]);
        if (!ev.voice_log_id && data.voice_log_id) ev.voice_log_id = data.voice_log_id;
        ev._key = ev.voice_log_id || ('ev-' + i);
        ev._confirmed = false;
        list.push(ev);
      }
    } else if (data.parsed || data.event) {
      var one = Object.assign({}, data.parsed || data.event);
      if (!one.voice_log_id && data.voice_log_id) one.voice_log_id = data.voice_log_id;
      one._key = one.voice_log_id || 'ev-0';
      one._confirmed = false;
      list.push(one);
    }
    return list;
  },

  // 把 upload / parse-text 的响应落到页面：单条走旧 state 判定，多条按整体置信度。
  _applyParsed: function (data, defaultState) {
    var events = this._extractEvents(data);
    if (events.length === 0) { this.setData({ state: 'error' }); return; }
    var allConfident = true;
    for (var i = 0; i < events.length; i++) {
      if ((events[i].confidence || 0) < 0.8 || (events[i].missing_fields || []).length > 0) {
        allConfident = false; break;
      }
    }
    var state = events.length > 1
      ? (allConfident ? 'success' : 'confirm_needed')
      : (defaultState || (allConfident ? 'success' : 'confirm_needed'));
    if (events.length > 1) {
      this.setData({
        parsed: events[0],
        parsedEvents: events,
        parseWarning: data.warning || '',
        confirmedCount: 0,
        showRecords: true,
        state: state,
      });
    } else {
      this.setData({
        parsed: events[0],
        parsedEvents: null,
        parseWarning: data.warning || '',
        confirmedCount: 0,
        showRecords: true,
        state: state,
      });
    }
    this.loadTodayCount();
  },

  // ── 确认入账（单条 / 多意图逐条共用）────────────────
  confirmRecord: function (e) {
    // record-card 回传 {record}；多意图时据此定位对应一笔，单条时兜底 this.data.parsed。
    var record = (e && e.detail && e.detail.record) || this.data.parsed;
    if (!record || !record.voice_log_id) return;
    return this._confirmEvent(record);
  },

  _confirmEvent: function (record, opts) {
    opts = opts || {};
    // 修复：缺失必填字段不允许确认，避免后端落一条数量为 0 的脏账。
    var missing = record.missing_fields || [];
    if (missing.length > 0) {
      wx.showToast({ title: '请先补充缺失字段后再确认', icon: 'none' });
      return Promise.reject(new Error('missing_fields'));
    }
    var self = this;
    // 修复：antiDuplicate 防止快速双击产生两个并发 confirm 请求越过后端幂等检查。
    return app.request({
      url: '/voice/confirm',
      method: 'POST',
      data: { voice_log_id: record.voice_log_id },
      antiDuplicate: true,
      dupKey: 'voice:confirm:' + record.voice_log_id,
    }).then(function () {
      if (self.data.parsedEvents && self.data.parsedEvents.length > 1) {
        // 多意图：标记这一笔已入账，全部完成才收起页面。
        var events = self.data.parsedEvents.map(function (ev) {
          return ev.voice_log_id === record.voice_log_id
            ? Object.assign({}, ev, { _confirmed: true })
            : ev;
        });
        var done = events.filter(function (ev) { return ev._confirmed; }).length;
        self.setData({ parsedEvents: events, confirmedCount: done });
        self.loadTodayCount();
        if (done >= events.length) {
          wx.showToast({ title: events.length + ' 笔都记好了', icon: 'success' });
          self.resetToIdle();
        } else if (!opts.silent) {
          wx.showToast({ title: '已记 ' + done + '/' + events.length + ' 笔', icon: 'success' });
        }
      } else {
        wx.showToast({ title: '记账成功', icon: 'success' });
        self.resetToIdle();
      }
    }).catch(function (err) {
      // 修复：app.request 已在层弹后端 detail（如商品未在品类表中找到），此处不再覆盖 toast，
      // 让摊主看到真正失败原因。
      // 一键全部确认（silent）时失败即停：把错误抛回串行链，剩余笔留给逐条处理。
      if (opts.silent) throw err;
    });
  },

  // 多意图：一键全部确认（串行提交，失败即停在出错的那笔）。
  confirmAll: function () {
    var self = this;
    var pending = (this.data.parsedEvents || []).filter(function (ev) { return !ev._confirmed; });
    if (pending.length === 0 || this._confirmAllRunning) return;
    this._confirmAllRunning = true;
    var run = pending.reduce(function (chain, ev) {
      return chain.then(function () { return self._confirmEvent(ev, { silent: true }); });
    }, Promise.resolve());
    var settle = function () { self._confirmAllRunning = false; };
    run.then(settle, settle);
  },

  // ── 页内纠错卡（v3.2，替代串行 showModal）────────────
  // 提交协议与旧版一致：POST /voice/correct
  //   { voice_log_id, corrections: { product?, quantity?, total_amount? } }
  // （total_amount 属后端 VoiceCorrection 白名单字段。）
  correctAndConfirm: function (e) {
    var record = (e && e.detail && e.detail.record) || this.data.parsed;
    if (!record || !record.voice_log_id) return;
    this.openCorrection(record);
  },

  openCorrection: function (record) {
    this._stateBeforeRespeak = this.data.state;
    this.setData({
      correction: {
        voice_log_id: record.voice_log_id,
        record: record,
        product: record.product || '',
        quantity: record.quantity != null ? String(record.quantity) : '',
        unit: record.unit || '斤',
        amount: record.total_amount != null ? String(record.total_amount) : '',
        candidates: this._buildProductCandidates(record),
        submitting: false,
      },
    });
  },

  // 商品候选：本次解析商品名 + 后端解析候选（如有）+ 商户 SKU 缓存，去重取前 10。
  _buildProductCandidates: function (record) {
    var seen = {};
    var list = [];
    function add(name) {
      name = (name || '').trim();
      if (name && !seen[name]) { seen[name] = true; list.push(name); }
    }
    if (record && record.product) add(record.product);
    var parsedCands = (record && (record.candidates || record.product_candidates)) || [];
    for (var i = 0; i < parsedCands.length; i++) {
      add(typeof parsedCands[i] === 'string' ? parsedCands[i] : parsedCands[i] && parsedCands[i].name);
    }
    var skuNames = storage.getSkuNames();
    for (var j = 0; j < skuNames.length; j++) add(skuNames[j]);
    return list.slice(0, 10);
  },

  // SKU 名缓存：过期时后台静默刷新，失败不打扰（旧缓存仍可用）。
  _refreshSkuCacheIfNeeded: function () {
    if (!storage.isSkuCacheStale()) return;
    app.request({ url: '/catalog/skus' }).then(function (data) {
      var names = (data || []).map(function (s) { return s && s.name; });
      storage.setSkuNames(names);
    }).catch(function () { /* 静默：纠错 chips 还有解析商品名兜底 */ });
  },

  closeCorrection: function () {
    if (this.data.state === 'respeaking') {
      this._cancelledFlag = true;
      try { recorder.stopRecording(); } catch (e) {}
      this._restoreAfterRespeak();
    }
    this.setData({ correction: null });
  },

  pickProductChip: function (e) {
    var name = e.currentTarget.dataset.name;
    if (!name) return;
    this.setData({ correction: Object.assign({}, this.data.correction, { product: name }) });
  },
  onCorrectProduct: function (e) {
    this.setData({ correction: Object.assign({}, this.data.correction, { product: e.detail.value }) });
  },
  onCorrectQuantity: function (e) {
    this.setData({ correction: Object.assign({}, this.data.correction, { quantity: e.detail.value }) });
  },
  onCorrectAmount: function (e) {
    this.setData({ correction: Object.assign({}, this.data.correction, { amount: e.detail.value }) });
  },

  // ── 重说这一项：只重录纠错卡这一笔，识别结果自动回填 ──
  toggleRespeak: function () {
    var self = this;
    if (this.data.state === 'respeaking') {
      try { recorder.stopRecording(); } catch (e) {}
      return;
    }
    if (!this.data.correction) return;
    if (this.data.state !== 'success' && this.data.state !== 'confirm_needed') return;
    this._cancelledFlag = false;
    this._stateBeforeRespeak = this.data.state;
    this.setData({ state: 'respeaking' });
    recorder.startRecording().then(function (res) {
      if (self._cancelledFlag) { self._restoreAfterRespeak(); return; }
      self.setData({ state: 'uploading', uploadProgress: 0 });
      self._uploadRespeak(res && res.tempFilePath);
    }).catch(function () {
      if (!self._cancelledFlag) wx.showToast({ title: '没录上，再试一次', icon: 'none' });
      self._restoreAfterRespeak();
    });
  },

  _restoreAfterRespeak: function () {
    this.setData({ state: this._stateBeforeRespeak || 'confirm_needed' });
  },

  _uploadRespeak: function (filePath) {
    var self = this;
    if (!filePath) { this._restoreAfterRespeak(); return; }
    app.uploadFile({
      url: '/voice/upload',
      filePath: filePath,
      name: 'audio',
      formData: { dialect: storage.getVoiceDialect() },
      onProgressUpdate: function (res) { self.setData({ uploadProgress: res.progress }); },
    }).then(function (data) {
      self._restoreAfterRespeak();
      if (!self.data.correction) return;  // 卡片已被收起，丢弃本次回填
      var parsed = data && data.parsed;
      var asrText = (data && data.asr_text) || '';
      var patch = {};
      if (parsed && parsed.product) patch.product = parsed.product;
      if (parsed && parsed.quantity != null) patch.quantity = String(parsed.quantity);
      // 短句且不含数字 → 大概率是把商品名重说了一遍，直接当商品名回填。
      if (!patch.product && !patch.quantity && asrText && asrText.length <= 6 && !/[0-9.]/.test(asrText)) {
        patch.product = asrText.trim();
      }
      if (!Object.keys(patch).length) {
        wx.showToast({ title: '没听清这一项，请手动改或再试', icon: 'none' });
        return;
      }
      self.setData({ correction: Object.assign({}, self.data.correction, patch) });
      wx.showToast({ title: '已填入，请核对', icon: 'none' });
    }).catch(function () {
      self._restoreAfterRespeak();
      // app 层已 toast（网络/服务端），纠错卡保留已填内容继续手动改。
    });
  },

  // 保存纠错并立即确认（协议与旧版完全一致，仅采集方式升级）。
  submitCorrection: function () {
    var c = this.data.correction;
    if (!c || c.submitting) return;
    var record = c.record || {};
    var missing = record.missing_fields || [];

    var product = (c.product || '').trim();
    if (missing.indexOf('product') >= 0 && !product) {
      wx.showToast({ title: '请先选好商品', icon: 'none' }); return;
    }

    var qNum = parseFloat(c.quantity);
    if (c.quantity !== '' && isNaN(qNum)) {
      wx.showToast({ title: '数量请输入数字', icon: 'none' }); return;
    }
    if (missing.indexOf('quantity') >= 0 && c.quantity === '') {
      wx.showToast({ title: '请补充数量', icon: 'none' }); return;
    }

    var aNum = parseFloat(c.amount);
    if (c.amount !== '' && isNaN(aNum)) {
      wx.showToast({ title: '金额请输入数字', icon: 'none' }); return;
    }

    var corrections = {};
    if (product && product !== (record.product || '')) corrections.product = product;
    if (c.quantity !== '' && qNum !== record.quantity) corrections.quantity = qNum;
    if (c.amount !== '' && aNum !== record.total_amount) corrections.total_amount = aNum;

    if (Object.keys(corrections).length === 0) {
      wx.showToast({ title: '没有改动，直接点「确认入账」即可', icon: 'none' });
      return;
    }

    var self = this;
    this.setData({ correction: Object.assign({}, c, { submitting: true }) });
    app.request({
      url: '/voice/correct',
      method: 'POST',
      data: { voice_log_id: c.voice_log_id, corrections: corrections },
    }).then(function (res) {
      var newParsed = (res && res.parsed) || Object.assign({}, record, corrections);
      self.setData({ correction: null });
      if (self.data.parsedEvents && self.data.parsedEvents.length > 1) {
        // 多意图：只更新被改的那一笔，再对该笔走确认。
        self.setData({
          parsedEvents: self.data.parsedEvents.map(function (ev) {
            return ev.voice_log_id === c.voice_log_id
              ? Object.assign({}, ev, newParsed, { _key: ev._key, _confirmed: ev._confirmed })
              : ev;
          }),
        });
      } else {
        self.setData({ parsed: Object.assign({}, self.data.parsed, newParsed) });
      }
      var target = Object.assign({}, newParsed, { voice_log_id: c.voice_log_id });
      // 修正后立即尝试确认；若仍缺字段会被 _confirmEvent 防呆拦下。
      return self._confirmEvent(target);
    }).catch(function () {
      // app 层已弹后端 detail，不覆盖；卡片留在原地等摊主改。
      if (self.data.correction) {
        self.setData({ correction: Object.assign({}, self.data.correction, { submitting: false }) });
      }
    });
  },

  resetToIdle: function () {
    // 离开/重置时一并清理暂存路径、多意图/纠错卡状态与残留打字定时器。
    this._pendingUploadPath = null;
    this._confirmAllRunning = false;
    this._stateBeforeRespeak = null;
    this._clearTypingTimer();
    this.setData({
      state: 'idle', asrText: '', textInput: '', parsed: null, streamingText: '',
      pendingUploadExists: false,
      parsedEvents: null, parseWarning: '', confirmedCount: 0,
      showRecords: false, correction: null,
    });
  },
  sayAgain: function () { this.resetToIdle(); },

  loadTodayCount: function () {
    var self = this;
    app.request({ url: '/voice/today-count' })
      .then(function (res) { self.setData({ todayCount: (res && res.today_count) || 0 }); }).catch(function () {});
    this.loadRecentLogs();
  },

  loadRecentLogs: function () {
    var self = this;
    app.request({ url: '/voice/logs', data: { page: 1, limit: 20 } })
      .then(function (res) { self.setData({ recentLogs: (res || []).slice(0, 5) }); }).catch(function () {});
  },

  voidRecord: function (e) {
    var self = this;
    var logId = e.currentTarget.dataset.id;
    if (!logId) return;
    wx.showModal({
      title: '撤销确认', content: '撤销后库存和批次将自动回滚，确定撤销吗？', confirmColor: '#c8392b',
      success: function (res) {
        if (res.confirm) {
          app.request({ url: '/voice/' + logId + '/void', method: 'POST', data: { reason: '用户手动撤销' } })
            .then(function () { wx.showToast({ title: '已撤销', icon: 'success' }); self.loadTodayCount(); })
            .catch(function () {});
        }
      },
    });
  },
});
