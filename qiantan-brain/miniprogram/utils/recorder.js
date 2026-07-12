/**
 * Voice recorder manager — wraps WeChat RecorderManager.
 * Provides simplified press-to-talk API for the voice accounting page.
 */

const recorderManager = wx.getRecorderManager();

/**
 * Start recording. Returns a promise that resolves with temp file path when stopped.
 * @returns {Promise<{tempFilePath: string, duration: number}>}
 */
function startRecording() {
  return new Promise((resolve, reject) => {
    recorderManager.onStop((res) => {
      if (res.tempFilePath) {
        resolve({ tempFilePath: res.tempFilePath, duration: res.duration });
      } else {
        reject(new Error('Recording produced no file'));
      }
    });

    recorderManager.onError((err) => {
      reject(err);
    });

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

module.exports = { startRecording, stopRecording };
