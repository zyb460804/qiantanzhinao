/** 市场通知 — 查看市场公告/警告/紧急通知 */
var app = getApp();

/** 通知类型配置 */
var TYPE_CONFIG = {
  info: { label: '公告', icon: '📢', color: 'var(--green-700)', bg: 'var(--green-50)' },
  warning: { label: '警告', icon: '⚠️', color: 'var(--corn)', bg: 'var(--corn-soft)' },
  urgent: { label: '紧急', icon: '🚨', color: 'var(--tomato)', bg: 'var(--tomato-soft)' }
};

Page({
  data: {
    skinClass: '', loading: true, loadError: false,
    notices: [], expandedId: ''
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadNotices();
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadNotices(function () { wx.stopPullDownRefresh(); });
  },

  /** 加载通知列表 — 调用市场管理 API 获取通知 */
  loadNotices: function (callback) {
    var self = this;
    this.setData({ loading: true, loadError: false });

    // 先获取商户所属的市场列表，再查每个市场的通知
    // 简化方案：调用 GET /market-admin/notices 需要 market_id，
    // 我们通过 GET /market-admin/markets 获取关联市场
    app.request({ url: '/market-admin/markets' })
      .then(function (res) {
        // app.request 已解包 {code:0, data:[...]} → data
        var markets = Array.isArray(res) ? res : [];
        if (markets.length === 0) {
          self.setData({ loading: false, notices: [] });
          if (callback) callback();
          return;
        }
        var fetches = markets.map(function (m) {
          return app.request({
            url: '/market-admin/notices?market_id=' + (m.market_id || m.id),
          }).catch(function () { return []; });
        });
        return Promise.all(fetches).then(function (results) {
          var allNotices = [];
          results.forEach(function (r) {
            var items = Array.isArray(r) ? r : [];
            allNotices = allNotices.concat(items);
          });
          // 去重 + 按时间倒序
          var seen = {};
          allNotices = allNotices.filter(function (n) {
            var key = n.id;
            if (seen[key]) return false;
            seen[key] = true;
            return true;
          });
          allNotices.sort(function (a, b) {
            return (b.created_at || '').localeCompare(a.created_at || '');
          });
          self.setData({ loading: false, notices: allNotices });
          if (callback) callback();
        });
      })
      .catch(function () {
        self.setData({ loading: false, loadError: true });
        if (callback) callback();
      });
  },

  /** 展开/折叠通知详情 */
  toggleExpand: function (e) {
    var id = e.currentTarget.dataset.id;
    this.setData({ expandedId: this.data.expandedId === id ? '' : id });
  },

  /** 获取通知类型配置 */
  getTypeConfig: function (type) {
    return TYPE_CONFIG[type] || TYPE_CONFIG.info;
  },

  /** 格式化时间 */
  formatTime: function (isoStr) {
    if (!isoStr) return '';
    return isoStr.replace('T', ' ').substring(0, 16);
  }
});
