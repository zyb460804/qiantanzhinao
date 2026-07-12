/**
 * 语音记账页面 — 核心交互页 (v2.2 生鲜元气)
 * 新增：时段皮肤 / 声波纹 / 流式字幕；保留录音→ASR→解析全流程
 */
var app = getApp();

Page({
  data: {
    // State: idle | listening | uploading | processing | success | confirm_needed | error
    state: 'idle',
    mode: 'voice',         // 'voice' | 'text'
    skin: 'noon',
    asrText: '',           // 识别/输入的文本
    streamingText: '',      // 流式字幕
    textInput: '',         // 文本框内容
    parsed: null,          // 解析结果对象
    todayCount: 0,
    recentLogs: [],
    waveBars: [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17],
    demoMode: false,
  },

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this.loadTodayCount();
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
  },

  // ── 模式切换 ──────────────────────────────────
  switchToVoice: function () { this.setData({ mode: 'voice', state: 'idle', parsed: null, asrText: '', streamingText: '' }); },
  switchToText: function () { this.setData({ mode: 'text', state: 'idle', parsed: null, asrText: '', streamingText: '' }); },

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
    if (app.globalData.reduceMotion) { this.setData({ streamingText: text }); if (done) done(); return; }
    this.setData({ streamingText: '' });
    var i = 0;
    var t = setInterval(function () {
      i++;
      self.setData({ streamingText: text.slice(0, i) });
      if (i >= text.length) {
        clearInterval(t);
        setTimeout(function () { self.setData({ streamingText: '' }); if (done) done(); }, 520);
      }
    }, 38);
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

    if (this.data.demoMode) {
      var mockTexts = ['今天进了白菜50斤，三毛钱一斤', '进了土豆30斤，一块二一斤', '卖了西瓜20斤，两块钱一斤，一共卖了40块', '进了猪肉15斤，十二块钱一斤', '进了豆腐10斤，一块五一斤', '扔了烂白菜3斤'];
      var text = mockTexts[Math.floor(Math.random() * mockTexts.length)];
      this.setData({ asrText: text });
      this.streamReply(text, function () { self.parseText(text); });
      return;
    }

    var mid = app.getMerchantId();
    app.uploadFile({ url: '/voice/upload', filePath: filePath, name: 'audio', formData: { merchant_id: mid, dialect: 'mandarin' } })
      .then(function (data) {
        var asrText = (data && data.asr_text) || '';
        var parsed = data && data.parsed;
        if (asrText) {
          self.setData({ asrText: asrText });
          if (parsed && parsed.voice_log_id) {
            var conf = parsed.confidence || 0;
            self.setData({ parsed: parsed, state: conf >= 0.8 ? 'success' : 'confirm_needed' });
            self.loadTodayCount();
          } else {
            self.streamReply(asrText, function () { self.parseText(asrText); });
          }
        } else {
          self.setData({ state: 'idle', mode: 'text' });
          wx.showToast({ title: '语音识别未成功，请使用文字输入', icon: 'none', duration: 2500 });
        }
      }).catch(function () { self.setData({ state: 'error' }); wx.showToast({ title: '上传失败，请重试或使用文字输入', icon: 'none' }); });
  },

  // ── 文本解析 (核心) ──────────────────────────
  parseText: function (text) {
    var self = this;
    var mid = app.getMerchantId();
    app.request({ url: '/voice/parse-text', method: 'POST', data: { merchant_id: mid, text: text } })
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
    var mid = app.getMerchantId();
    app.request({ url: '/voice/logs', data: { merchant_id: mid, page: 1, limit: 20 } })
      .then((function (res) { this.setData({ recentLogs: (res || []).slice(0, 5) }); }).bind(this)).catch(function () {});
    app.request({ url: '/voice/today-count', data: { merchant_id: mid } })
      .then((function (res) { this.setData({ todayCount: (res && res.today_count) || 0 }); }).bind(this)).catch(function () {});
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
