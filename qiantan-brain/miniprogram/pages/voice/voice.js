/**
 * 语音记账页面 v3.0 — 草稿保护 / 上传进度 / 流式对齐
 */
var app = getApp();
var streamText = require('../../utils/stream-text').streamText;
var storage = require('../../utils/storage');

Page({
  data: {
    state: 'idle', mode: 'voice', skin: 'noon',
    asrText: '', streamingText: '', textInput: '',
    parsed: null, todayCount: 0, recentLogs: [],
    waveBars: [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17],
    uploadProgress: 0,
    debugMode: app.globalData && app.globalData.debugMode,
  },

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this.loadTodayCount();
  },

  onHide: function () { this._clearTypingTimer(); },

  _clearTypingTimer: function () {
    // streamText 返回 {cancel: function} 对象,不是 timer id,必须调 .cancel()
    if (this._typingTimerId && typeof this._typingTimerId.cancel === 'function') {
      this._typingTimerId.cancel();
    }
    this._typingTimerId = null;
    if (this._typingDoneTimerId) { clearTimeout(this._typingDoneTimerId); this._typingDoneTimerId = null; }
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
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
    var self = this;
    this.streamReply(text, function () { self.parseText(text); });
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
    var rm = wx.getRecorderManager();
    this._recorder = rm;
    rm.onStop((function (res) { this.setData({ state: 'uploading' }); this.handleRecordingResult(res); }).bind(this));
    rm.onError((function (err) { console.error('Record error:', err); this.setData({ state: 'error' }); }).bind(this));
    rm.start({ duration: 60000, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: 'mp3' });
  },
  onEnd: function () { if (this.data.state === 'listening' && this._recorder) this._recorder.stop(); },
  onCancel: function () {
    if (this.data.state === 'listening' && this._recorder) this._recorder.stop();
    this.setData({ state: 'idle' });
    wx.showToast({ title: '已取消', icon: 'none' });
  },

  // ── 处理录音结果 ─────────────────────────────
  handleRecordingResult: function (res) {
    var filePath = res.tempFilePath;
    var self = this;
    this.setData({ uploadProgress: 0 });

    var uploadTask = wx.uploadFile({
      url: app.globalData.apiBase + '/voice/upload',
      filePath: filePath,
      name: 'audio',
      header: { 'Authorization': 'Bearer ' + app.globalData.accessToken },
      formData: { dialect: storage.getVoiceDialect() },
      success: function (res) {
        var body;
        try { body = JSON.parse(res.data); } catch (e) { self.setData({ state: 'error' }); return; }
        if (!body || body.code !== 0 || !body.data) { self.setData({ state: 'error' }); return; }
        var data = body.data;
        var asrText = (data.asr_text) || '';
        var parsed = data.parsed;
        if (asrText) {
          self.setData({ asrText: asrText, uploadProgress: 100 });
          if (parsed && parsed.voice_log_id) {
            var conf = parsed.confidence || 0;
            self.setData({ parsed: parsed, state: conf >= 0.8 ? 'success' : 'confirm_needed' });
            self.loadTodayCount();
          } else {
            // 流式打字与 API 对齐：打字直到 parseText 返回
            self.setData({ state: 'processing' });
            self._parseResult = null;
            self.streamReply(asrText, function () { if (!self._parseResult) self.parseText(asrText); });
            self.parseText(asrText);
          }
        } else {
          self.setData({ state: 'idle', mode: 'text' });
          wx.showToast({ title: '语音识别未成功，请使用文字输入', icon: 'none', duration: 2500 });
        }
      },
      fail: function () { self.setData({ state: 'error' }); wx.showToast({ title: '上传失败，请重试', icon: 'none' }); },
    });

    if (uploadTask && uploadTask.onProgressUpdate) {
      uploadTask.onProgressUpdate(function (res) {
        self.setData({ uploadProgress: res.progress });
      });
    }
  },

  // ── 文本解析 (核心) ──────────────────────────
  parseText: function (text) {
    var self = this;
    app.request({ url: '/voice/parse-text', method: 'POST', data: { text: text } })
      .then(function (res) {
        var parsed = res.parsed;
        var conf = parsed.confidence || 0;
        self.setData({ state: conf >= 0.8 ? 'success' : 'confirm_needed', parsed: parsed });
        self.loadTodayCount();
      }).catch(function () { self.setData({ state: 'error' }); });
  },

  confirmRecord: function () {
    var parsed = this.data.parsed;
    if (!parsed || !parsed.voice_log_id) return;
    app.request({ url: '/voice/confirm', method: 'POST', data: { voice_log_id: parsed.voice_log_id } })
      .then((function () { wx.showToast({ title: '记账成功', icon: 'success' }); this.resetToIdle(); }).bind(this))
      .catch(function () { wx.showToast({ title: '确认失败，请重试', icon: 'none' }); });
  },

  correctAndConfirm: function () {
    var self = this;
    var parsed = this.data.parsed;
    wx.showModal({
      title: '修改商品', editable: true, placeholderText: '输入正确的商品名', content: parsed.product || '',
      success: function (res) {
        if (res.confirm && res.content) {
          app.request({ url: '/voice/correct', method: 'POST', data: { voice_log_id: parsed.voice_log_id, corrections: { product: res.content } } })
            .then(function () { return self.confirmRecord(); })
            .catch(function () { wx.showToast({ title: '修正失败', icon: 'none' }); });
        }
      },
    });
  },

  resetToIdle: function () { this.setData({ state: 'idle', asrText: '', textInput: '', parsed: null, streamingText: '' }); },
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
      title: '撤销确认', content: '撤销后库存和批次将自动回滚，确定撤销吗？', confirmColor: '#d9524a',
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
