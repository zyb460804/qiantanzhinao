const app = getApp();

Page({
  data: {
    skin: app.globalData.skin,          // 初始按时段
    dark: app.globalData.theme === 'dark',
    reduce: app.globalData.reduceMotion,
    skinList: ['morning', 'noon', 'evening'],
    skinLabel: { morning: '早市', noon: '午市', evening: '晚市' },
    greens: ['green-950', 'green-800', 'green-700', 'green-600', 'green-500', 'green-200', 'green-100', 'green-50'],
    neutrals: ['paper', 'canvas', 'ink', 'ink-2', 'muted', 'line'],
  },

  setSkin(e) {
    this.setData({ skin: e.currentTarget.dataset.s });
  },
  toggleDark() {
    this.setData({ dark: !this.data.dark });
  },
  toggleReduce() {
    this.setData({ reduce: !this.data.reduce });
  },
});
