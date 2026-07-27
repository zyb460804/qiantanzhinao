/**
 * 语音记账页面 v3.1 — 草稿保护 / 上传进度 / 流式对齐 / 离线暂存
 *
 * v3.1 修复要点：
 *   - 录音改走 utils/recorder.js（顶层单次注册 onStop/onError），消除监听器叠加泄漏。
 *   - 上滑取消录音增加 _cancelledFlag 标志位，确保 onStop 回调丢弃已取消的录音。
 *   - 流式打印只做视觉，parseText 立即并行触发，避免打字停留期延迟入账。
 *   - confirmRecord 增加缺失字段防呆与 antiDuplicate 防双击。
 *   - 上传 fail 时 wx.saveFile 暂存录音文件，pending_retry 状态下可手动重传。
 *   - wx.uploadFile 在 401 时调用 ensureLogin 重试一次，避免误导文案。
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
  },

  // 方言代码与展示名映射（后端按需扩展）
  _dialectOptions: [
    { code: 'mandarin', name: '普通话' },
    { code: 'cantonese', name: '粤语' },
    { code: 'sichuan', name: '四川话' },
    { code: 'henan', name: '河南话' },
    { code: 'shandong', name: '山东话' },
  ],

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this._syncDialectLabel();
    this.loadTodayCount();
  },

  onHide: function () {
    this._clearTypingTimer();
    // 切后台/切 tab 时若仍在录音，立即停止并标记取消，避免 onStop 在隐藏态触发静默上传。
    if (this.data.state === 'listening') {
      this._cancelledFlag = true;
      try { recorder.stopRecording(); } catch (e) {}
      this.setData({ state: 'idle' });
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
  switchToVoice: function () {
    var self = this;
    if (this.data.textInput && this.data.textInput.trim()) {
      wx.showModal({
        title: '切换到语音', content: '文字输入的内容将会丢失，确定切换吗？',
        success: function (res) {
          if (res.confirm) { self._clearTypingTimer(); self.setData({ mode: 'voice', textInput: '', state: 'idle', parsed: null, asrText: '', streamingText: '' }); }
        },
      });
    } else {
      this._clearTypingTimer(); this.setData({ mode: 'voice', state: 'idle', parsed: null, asrText: '', streamingText: '' });
    }
  },
  switchToText: function () { this._clearTypingTimer(); this.setData({ mode: 'text', state: 'idle', parsed: null, asrText: '', streamingText: '' }); },

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
  _uploadRecording: function (filePath) {
    var self = this;
    this.setData({ uploadProgress: 0 });

    function attempt(retried) {
      var uploadTask = wx.uploadFile({
        url: app.globalData.apiBase + '/voice/upload',
        filePath: filePath,
        name: 'audio',
        header: { 'Authorization': 'Bearer ' + (app.globalData.accessToken || '') },
        formData: { dialect: storage.getVoiceDialect() },
        success: function (res) {
          // 修复：401 时先 ensureLogin 刷新 token 再重试一次，避免 token 过期落入"这次没有听清"误导文案。
          if (res.statusCode === 401 && !retried) {
            app.ensureLogin(true).then(function () { attempt(true); }).catch(function () {
              self.setData({ state: 'error' });
              wx.showToast({ title: '登录已过期，请重试', icon: 'none' });
            });
            return;
          }
          var body;
          try { body = JSON.parse(res.data); } catch (e) { self.setData({ state: 'error' }); return; }
          if (!body || body.code !== 0 || !body.data) { self.setData({ state: 'error' }); return; }
          var data = body.data;
          var asrText = data.asr_text || '';
          var parsed = data.parsed;
          if (asrText) {
            self.setData({ asrText: asrText, uploadProgress: 100 });
            if (parsed && parsed.voice_log_id) {
              var conf = parsed.confidence || 0;
              self.setData({ parsed: parsed, state: conf >= 0.8 ? 'success' : 'confirm_needed' });
              self.loadTodayCount();
            } else {
              // 修复：fallback 路径立即发起 parseText，streamReply 仅做视觉。
              // 删除原 _parseResult 守卫（其从未被赋值，恒真会导致双重 parseText 落两条 VoiceLog）。
              self.setData({ state: 'processing' });
              self.streamReply(asrText);
              self.parseText(asrText);
            }
          } else {
            self.setData({ state: 'idle', mode: 'text' });
            wx.showToast({ title: '语音识别未成功，请使用文字输入', icon: 'none', duration: 2500 });
          }
        },
        fail: function (err) {
          // 修复：上传失败时用 wx.saveFile 持久化临时音频文件，避免断网即丢；提供重传入口。
          // 注：offline-media.js 是完整离线队列但场景为通用媒体，未对接 /voice/upload；
          // 在文件边界内采用最小化暂存策略：保存到本地、暴露 pending_retry 状态。
          if (self._cancelledFlag) return;
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
        },
      });

      if (uploadTask && uploadTask.onProgressUpdate) {
        uploadTask.onProgressUpdate(function (res) {
          self.setData({ uploadProgress: res.progress });
        });
      }
    }

    attempt(false);
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
        var parsed = res.parsed;
        self.setData({ state: 'success', parsed: parsed });
        self.loadTodayCount();
      }).catch(function () { self.setData({ state: 'error' }); });
  },

  confirmRecord: function () {
    var parsed = this.data.parsed;
    if (!parsed || !parsed.voice_log_id) return;
    // 修复：缺失必填字段不允许确认，避免后端落一条数量为 0 的脏账。
    var missing = parsed.missing_fields || [];
    if (missing.length > 0) {
      wx.showToast({ title: '请先补充缺失字段后再确认', icon: 'none' });
      return;
    }
    var self = this;
    // 修复：antiDuplicate 防止快速双击产生两个并发 confirm 请求越过后端幂等检查。
    app.request({
      url: '/voice/confirm',
      method: 'POST',
      data: { voice_log_id: parsed.voice_log_id },
      antiDuplicate: true,
      dupKey: 'voice:confirm:' + parsed.voice_log_id,
    }).then(function () {
      wx.showToast({ title: '记账成功', icon: 'success' });
      self.resetToIdle();
    }).catch(function () {
      // 修复：app.request 已在层弹后端 detail（如商品未在品类表中找到），此处不再覆盖 toast，
      // 让摊主看到真正失败原因。
    });
  },

  // 修正并确认：依次补充缺失字段，最后统一提交 /voice/correct 再 confirm。
  correctAndConfirm: function () {
    var self = this;
    var parsed = this.data.parsed || {};
    var missing = parsed.missing_fields || [];
    var corrections = {};
    // 第一步：商品名（始终可改，缺失时强制问）。
    wx.showModal({
      title: '修改商品',
      editable: true,
      placeholderText: '输入正确的商品名',
      content: parsed.product || '',
      success: function (res) {
        if (!res.confirm) return;
        if (res.content) corrections.product = res.content;
        // 第二步：若缺数量，依次弹窗补充。
        if (missing.indexOf('quantity') >= 0) {
          self._promptQuantity(parsed, corrections);
        } else {
          self._submitCorrections(parsed, corrections);
        }
      },
    });
  },

  _promptQuantity: function (parsed, corrections) {
    var self = this;
    wx.showModal({
      title: '补充数量',
      editable: true,
      placeholderText: '如 50（单位默认斤）',
      content: parsed.quantity ? String(parsed.quantity) : '',
      success: function (res) {
        if (!res.confirm) return;
        if (res.content) {
          var num = parseFloat(res.content);
          if (!isNaN(num)) corrections.quantity = num;
        }
        self._submitCorrections(parsed, corrections);
      },
    });
  },

  _submitCorrections: function (parsed, corrections) {
    var self = this;
    var keys = Object.keys(corrections);
    if (keys.length === 0) {
      wx.showToast({ title: '未输入修改内容', icon: 'none' });
      return;
    }
    app.request({
      url: '/voice/correct',
      method: 'POST',
      data: { voice_log_id: parsed.voice_log_id, corrections: corrections },
    }).then(function (res) {
      var newParsed = (res && res.parsed) || self.data.parsed;
      self.setData({ parsed: newParsed });
      // 修正后立即尝试确认；若仍缺字段会被 confirmRecord 防呆拦下。
      return self.confirmRecord();
    }).catch(function () {
      // app 层已弹后端 detail，不覆盖。
    });
  },

  resetToIdle: function () {
    // 离开/重置时一并清理暂存路径与残留打字定时器。
    this._pendingUploadPath = null;
    this._clearTypingTimer();
    this.setData({ state: 'idle', asrText: '', textInput: '', parsed: null, streamingText: '', pendingUploadExists: false });
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
