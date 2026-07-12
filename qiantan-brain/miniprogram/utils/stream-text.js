/**
 * 流式逐字打印工具 — stream-text.js
 *
 * 消除 voice.js (streamReply) 和 advisor.js (saysReply) 的重复逐字定时器逻辑。
 *
 * 使用方式：
 *   var streamText = require('../../utils/stream-text');
 *   streamText.type('你好，老板！', function (displayText) {
 *     this.setData({ streamingText: displayText });
 *   }.bind(this), function () {
 *     // 打完后回调
 *   });
 */

var REDUCE_MOTION = false;
var DEFAULT_SPEED = 42;   // ms/字符
var DEFAULT_PAUSE = 2600; // 打完后停留 ms (0 = 不自动隐藏)

/**
 * 流式逐字打印。
 *
 * @param {string}   text     - 要打印的文本
 * @param {function} onUpdate - 每帧回调 (displayText) => void
 * @param {function} onDone   - 打完后的回调 (可选)
 * @param {Object}   opts     - 配置项 (可选)
 * @param {number}   opts.speed    - 每字符间隔 ms (默认 42)
 * @param {number}   opts.pause    - 打完后显示停留 ms, 超时后 onUpdate('') 并触发 onDone (默认 2600, 传 0 不隐藏)
 * @param {boolean}  opts.noPause  - 不自动隐藏/清除文本, onDone 在打字完成后立即触发
 * @returns {Object}  { cancel: function } — 用于中止打印
 */
function streamText(text, onUpdate, onDone, opts) {
  opts = opts || {};
  var speed = opts.speed || DEFAULT_SPEED;
  var pause = opts.noPause ? 0 : (opts.pause !== undefined ? opts.pause : DEFAULT_PAUSE);

  if (REDUCE_MOTION) {
    onUpdate(text);
    if (onDone) setTimeout(onDone, speed);
    return { cancel: function () {} };
  }

  onUpdate('');
  var i = 0;
  var timer = setInterval(function () {
    i++;
    onUpdate(text.slice(0, i));
    if (i >= text.length) {
      clearInterval(timer);
      if (pause > 0) {
        setTimeout(function () {
          onUpdate('');
          if (onDone) onDone();
        }, pause);
      } else {
        if (onDone) onDone();
      }
    }
  }, speed);

  return {
    cancel: function () {
      clearInterval(timer);
      onUpdate(text); // 打完剩余内容
    },
  };
}

/**
 * 设置减少动效模式 (从 app.globalData.reduceMotion 同步)。
 */
function setReduceMotion(value) {
  REDUCE_MOTION = !!value;
}

module.exports = {
  streamText: streamText,
  setReduceMotion: setReduceMotion,
};
