const app = getApp();

Page({
  data: {
    skin: app.resolveSkin(),            // 初始：手动 > 按时段
    dark: app.globalData.theme === 'dark',
    reduce: app.globalData.reduceMotion,
    skinList: ['morning', 'noon', 'evening'],
    skinLabel: { morning: '早市', noon: '午市', evening: '晚市' },
    greens: ['green-950', 'green-800', 'green-700', 'green-600', 'green-500', 'green-200', 'green-100', 'green-50'],
    neutrals: ['paper', 'canvas', 'ink', 'ink-2', 'muted', 'line'],
  },

  // 时段皮肤：写入 globalData + 持久化，所有页面 onShow 时通过 Theme.apply 拾取
  setSkin(e) {
    var skin = e.currentTarget.dataset.s;
    app.setSkinManual(skin);
    this.setData({ skin: skin });
  },
  // 深色模式：写入 globalData.theme + 持久化，避免「演示开关」误导
  toggleDark() {
    var next = !this.data.dark;
    app.setTheme(next ? 'dark' : 'light');
    this.setData({ dark: next });
  },
  // 减少动效：通过 app.setReduceMotion 同步 globalData + 持久化 + stream-text
  toggleReduce() {
    var next = !this.data.reduce;
    app.setReduceMotion(next);
    this.setData({ reduce: next });
  },
});
