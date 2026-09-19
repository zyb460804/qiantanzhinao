const app = getApp();

Page({
  data: {
    skin: app.resolveSkin(),            // 初始：手动 > 按时段
    dark: app.globalData.theme === 'dark',
    reduce: app.globalData.reduceMotion,
    skinList: ['morning', 'noon', 'evening'],
    skinLabel: { morning: '早市', noon: '午市', evening: '晚市' },
    greens: ['green-950', 'green-800', 'green-700', 'green-600', 'green-500', 'green-400', 'green-200', 'green-100', 'green-50'],
    neutrals: ['paper', 'canvas', 'ink', 'ink-2', 'muted', 'line'],
    // v5.0 组件演示状态
    segIdx: 1,
    switchA: true,
    switchB: false,
    sheetOpen: false,
    barsA: [38, 52, 44, 66, 58, 78, 96],   // 近 7 日营业额趋势（最后一个高亮）
    barsB: [70, 58, 64, 46, 52, 40, 34],   // 损耗占比趋势（递减为好）
    // v5.1 扩展组件演示状态
    accOpen: 0,                // 折叠面板展开项
    checkA: true,
    checkB: false,
    radioVal: 'wx',
    qty: 3,                    // 步进器数量
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
  // v5.0 组件演示
  setSeg(e) {
    this.setData({ segIdx: Number(e.currentTarget.dataset.i) });
  },
  toggleSwitch(e) {
    var k = e.currentTarget.dataset.k;
    this.setData({ [k]: !this.data[k] });
  },
  openSheet() { this.setData({ sheetOpen: true }); },
  closeSheet() { this.setData({ sheetOpen: false }); },
  noop() {},
  // v5.1 组件演示
  toggleAcc(e) {
    var i = Number(e.currentTarget.dataset.i);
    this.setData({ accOpen: this.data.accOpen === i ? -1 : i });
  },
  toggleCheck(e) {
    var k = e.currentTarget.dataset.k;
    this.setData({ [k]: !this.data[k] });
  },
  setRadio(e) {
    this.setData({ radioVal: e.currentTarget.dataset.v });
  },
  qtyMinus() { this.setData({ qty: Math.max(0, this.data.qty - 1) }); },
  qtyPlus() { this.setData({ qty: this.data.qty + 1 }); },
  demoScroll(y) {
    wx.pageScrollTo({ scrollTop: y, duration: 200 });
  },
});
