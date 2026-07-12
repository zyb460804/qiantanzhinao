/**
 * voice-button 语音按钮组件
 *
 * 按住说话的大号圆形按钮,封装录音触摸逻辑和视觉状态。
 * 通过 triggerEvent 与父页面通信,不直接处理录音。
 *
 * 用法:
 *   <voice-button state="{{state}}" bind:start="onStart" bind:end="onEnd" bind:cancel="onCancel" />
 *
 * 事件:
 *   start   — 手指按下,父页面应开始录音
 *   end     — 手指松开,父页面应停止录音并处理
 *   cancel  — 上滑/触摸中断,父页面应取消录音
 */
Component({
  properties: {
    // 按钮状态: idle | listening | uploading | processing | success | error
    state: {
      type: String,
      value: 'idle',
    },
    // 是否显示副提示(默认显示)
    showHint: {
      type: Boolean,
      value: true,
    },
  },

  data: {
    // 记录触摸起点,用于判断上滑取消
    _startY: 0,
    _canceled: false,
  },

  methods: {
    onTouchStart: function (e) {
      if (this.data.state !== 'idle') return;
      this.setData({ _startY: e.touches[0].clientY, _canceled: false });
      this.triggerEvent('start');
    },

    onTouchMove: function (e) {
      // 上滑超过 60rpx 判定为取消
      var dy = e.touches[0].clientY - this.data._startY;
      if (dy < -60 && !this.data._canceled) {
        this.setData({ _canceled: true });
        wx.showToast({ title: '松开手指取消', icon: 'none' });
      } else if (dy >= -60 && this.data._canceled) {
        this.setData({ _canceled: false });
        wx.showToast({ title: '继续录音', icon: 'none' });
      }
    },

    onTouchEnd: function () {
      if (this.data.state !== 'listening') return;
      if (this.data._canceled) {
        this.triggerEvent('cancel');
      } else {
        this.triggerEvent('end');
      }
    },

    onTouchCancel: function () {
      if (this.data.state === 'listening') {
        this.triggerEvent('cancel');
      }
    },
  },
});
