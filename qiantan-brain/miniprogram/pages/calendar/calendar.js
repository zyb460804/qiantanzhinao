/**
 * 经营日历 — 时令 + 星期策略 (v2.2 生鲜元气)
 * [P2 占位] 时令与策略为本地 mock；后续可接 /env/solar-term 与 /advice/seasonal 接口。
 * 当前无后端接口对接，数据全部硬编码。
 */
var app = getApp();

var WEEK_LABELS = ['一', '二', '三', '四', '五', '六', '日'];
var WEEK_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

Page({
  data: {
    skin: 'noon',
    solarTerm: '小暑',
    termRange: '7/7 – 7/22',
    inSeason: '西瓜·番茄·黄瓜·毛豆',
    weekdayText: '',
    week: [],
    termAdvice: [
      { label: '备货', icon: 'cart', tone: 'info', text: '西瓜、番茄进入当令旺销，西瓜备货量上调 30%。' },
      { label: '营销', icon: 'spark', tone: 'hot', text: '“消暑套餐”组合卖：西瓜+黄瓜+毛豆，客单更高。' },
      { label: '时段', icon: 'leaf', tone: 'warn', text: '午后高温，叶菜易蔫，早市多摆、晚市转清货。' },
    ],
    plans: [
      { day: '12', week: '周六', weather: '晴 34°', temp: '34°', hot: true, tip: '周末家庭采购，备足西瓜与水果，早市加人。' },
      { day: '13', week: '周日', weather: '晴 33°', temp: '33°', hot: true, tip: '延续旺销，留意午后叶菜损耗，晚市打折清。' },
      { day: '14', week: '周一', weather: '雷阵雨 29°', temp: '29°', hot: false, tip: '工作日+降雨，客流降，叶菜减量 15%。' },
      { day: '15', week: '周二', weather: '多云 30°', temp: '30°', hot: false, tip: '平稳日，按常规补货，主推根茎耐储类。' },
    ],
  },

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this.buildWeek();
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
  },

  buildWeek: function () {
    var now = new Date();
    var jsDay = now.getDay();              // 0=周日
    var mondayOffset = (jsDay + 6) % 7;   // 距本周一的天数
    var monday = new Date(now); monday.setDate(now.getDate() - mondayOffset);
    var cells = [];
    for (var i = 0; i < 7; i++) {
      var d = new Date(monday); d.setDate(monday.getDate() + i);
      var isWeekend = (i === 5 || i === 6);
      var isToday = (d.getDate() === now.getDate() && d.getMonth() === now.getMonth());
      cells.push({
        d: WEEK_LABELS[i],
        tag: isWeekend ? '休' : '',
        today: isToday,
        hot: isWeekend,
      });
    }
    this.setData({
      week: cells,
      weekdayText: WEEK_NAMES[(jsDay + 6) % 7],
    });
  },
});
