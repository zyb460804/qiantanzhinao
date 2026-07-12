/**
 * 经营日历 — 时令 + 星期策略 (v2.3 生产版本)
 * 对接 /env/solar-term 节气接口 + /env/forecast 天气预报, 数据实时渲染。
 */
var app = getApp();

var WEEK_LABELS = ['一', '二', '三', '四', '五', '六', '日'];
var WEEK_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

Page({
  data: {
    skin: 'noon',
    loading: true,
    solarTerm: '--',
    termRange: '',
    inSeason: '',
    weekdayText: '',
    week: [],
    termAdvice: [],
    plans: [],
    hasError: false,
  },

  onShow: function () {
    this.applySkin(app.resolveSkin());
    this.buildWeek();
    this.loadData();
  },

  onPullDownRefresh: function () {
    this.loadData().then(function () {
      wx.stopPullDownRefresh();
    }).catch(function () {
      wx.stopPullDownRefresh();
    });
  },

  applySkin: function (skin) {
    if (skin !== 'morning' && skin !== 'evening') skin = 'noon';
    this.setData({ skin: skin });
  },

  loadData: function () {
    var self = this;
    this.setData({ loading: true, hasError: false });

    // 并行请求: 节气数据 + 天气预报
    return Promise.all([
      app.request({ url: '/env/solar-term', auth: false }).catch(function () { return null; }),
      app.request({ url: '/env/forecast?days=4' }).catch(function () { return null; }),
    ]).then(function (results) {
      var term = results[0];
      var forecast = results[1];

      // 节气数据
      if (term) {
        self.setData({
          solarTerm: term.solar_term || '--',
          termRange: term.term_range || '',
          inSeason: term.in_season_products || '',
          termAdvice: (term.advice || []).map(function (a) {
            return {
              label: a.label || '',
              icon: a.icon || 'leaf',
              tone: a.tone || 'info',
              text: a.text || '',
            };
          }),
        });
      }

      // 天气预报 → plans
      var plans = [];
      if (forecast && Array.isArray(forecast) && forecast.length > 0) {
        plans = forecast.slice(0, 4).map(function (fc) {
          var d = new Date(fc.date);
          var weekLabel = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
          var temp = Math.round(fc.temp_high || 25);
          var isHot = temp >= 32;
          var weatherType = fc.weather_type || '晴';
          var rain = fc.rainfall_prob || 0;

          var tip = '';
          if (rain > 50) {
            tip = '降雨概率高，客流减少，叶菜减量 15%，多备耐储品。';
          } else if (isHot && (d.getDay() === 0 || d.getDay() === 6)) {
            tip = '周末高温，家庭采购旺，备足水果和叶菜，早市加人。';
          } else if (isHot) {
            tip = '高温天，午后叶菜易损，早市多摆、晚市清货。';
          } else if (d.getDay() === 0 || d.getDay() === 6) {
            tip = '周末家庭采购，按常规量上浮 20%，主推套餐组合。';
          } else {
            tip = '平稳日，按常规补货，主推根茎耐储类。';
          }

          return {
            day: d.getDate(),
            week: '周' + weekLabel,
            weather: weatherType + ' ' + temp + '°',
            temp: temp + '°',
            hot: isHot,
            tip: tip,
            rain: rain > 50,
          };
        });
      }

      var bothFailed = !term && !forecast;
      self.setData({ plans: plans, loading: false, hasError: bothFailed });
      if (bothFailed) {
        app.logError('calendar/loadData', '加载节气/天气数据失败', { silent: true });
        return null;
      }
      return results;
    }).catch(function (err) {
      self.setData({ loading: false, hasError: true });
      app.logError('calendar/loadData', (err && err.message) || '加载节气/天气数据失败', { silent: true });
      return null;
    });
  },

  buildWeek: function () {
    var now = new Date();
    var jsDay = now.getDay();
    var mondayOffset = (jsDay + 6) % 7;
    var monday = new Date(now);
    monday.setDate(now.getDate() - mondayOffset);
    var cells = [];
    for (var i = 0; i < 7; i++) {
      var d = new Date(monday);
      d.setDate(monday.getDate() + i);
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

  // 重试加载
  retry: function () {
    this.loadData();
  },

  // 一键生成采购清单
  addToPurchase: function () {
    var products = (this.data.inSeason || '').split('·').filter(Boolean);
    if (!products.length) { wx.showToast({ title: '暂无当令商品', icon: 'none' }); return; }

    var draft = wx.getStorageSync('purchaseDraft') || [];
    var added = 0;
    products.forEach(function (name) {
      name = name.trim();
      var exists = draft.some(function (d) { return d.name === name; });
      if (!exists) {
        // 默认建议量 5 斤,用户可在采购页调整
        draft.push({ name: name, qty: 5, unit: '斤', from: '时令建议' });
        added++;
      }
    });

    wx.setStorageSync('purchaseDraft', draft);
    wx.showModal({
      title: '采购清单', content: '已添加 ' + added + ' 个当令商品到采购清单。',
      confirmText: '去查看', cancelText: '稍后',
      success: function (r) { if (r.confirm) wx.navigateTo({ url: '/pages/purchase/purchase' }); },
    });
  },
});
