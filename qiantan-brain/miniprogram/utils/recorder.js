/**
 * Voice recorder manager — wraps WeChat RecorderManager.
 * Provides simplified press-to-talk API for the voice accounting page.
 *
 * 关键修复:onStop/onError 在模块顶层只注册一次,用 _currentResolve/_currentReject
 * 保存当前 promise 的回调,避免每次 startRecording 都叠加新监听器导致泄漏。
 */

var recorderManager = wx.getRecorderManager();

// 保存当前录音的 promise 回调(模块级单例,不随 startRecording 叠加)
var _currentResolve = null;
var _currentReject = null;

// 顶层注册一次,避免重复绑定
recorderManager.onStop(function (res) {
  if (!_currentResolve) return;
  var resolve = _currentResolve;
  var reject = _currentReject;
  _currentResolve = null;
  _currentReject = null;
  if (res && res.tempFilePath) {
    resolve({ tempFilePath: res.tempFilePath, duration: res.duration });
  } else {
    reject(new Error('Recording produced no file'));
  }
});

recorderManager.onError(function (err) {
  if (!_currentReject) return;
  var reject = _currentReject;
  _currentReject = null;
  _currentResolve = null;
  reject(err);
});

/**
 * Start recording. Returns a promise that resolves with temp file path when stopped.
 * @returns {Promise<{tempFilePath: string, duration: number}>}
 */
function startRecording() {
  return new Promise(function (resolve, reject) {
    // 若上一次录音未结束就再次调用,先拒绝旧的
    if (_currentReject) {
      _currentReject(new Error('Recording interrupted by a new start'));
      _currentResolve = null;
      _currentReject = null;
    }
    _currentResolve = resolve;
    _currentReject = reject;

    recorderManager.start({
      duration: 60000,       // Max 60 seconds
      sampleRate: 16000,     // 16kHz for ASR
      numberOfChannels: 1,   // Mono
      encodeBitRate: 48000,
      format: 'mp3',         // iFlytek supports mp3
    });
  });
}

/**
 * Stop the current recording.
 */
function stopRecording() {
  recorderManager.stop();
}

module.exports = { startRecording: startRecording, stopRecording: stopRecording };
