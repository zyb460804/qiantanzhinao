/** 市场通知 — 查看市场公告/警告/紧急通知 */
var app = getApp();

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

  /** 加载通知列表 — 调用市场管理 API 获取通知

   * 优化（审计 P1-加载通知列表）：
   *  - 后端 /market-admin/markets 现在只返回当前商户已入场的市场（按
   *    MarketMerchant 关联表过滤），避免看到全平台无关市场的通知。
   *  - 单摊贩通常只属于一个市场，N+1 退化为 1-2 个请求；同时限制最多并发
   *    5 个市场，避免极端情况下请求扇出过大。
   */
  loadNotices: function (callback) {
    var self = this;
    this.setData({ loading: true, loadError: false });

    app.request({ url: '/market-admin/markets' })
      .then(function (res) {
        // app.request 已解包 {code:0, data:[...]} → data
        var markets = Array.isArray(res) ? res : [];
        if (markets.length === 0) {
          self.setData({ loading: false, notices: [] });
          if (callback) callback();
          return;
        }
        // 限制扇出，避免极端情况 100+ 市场导致请求洪水
        var targetMarkets = markets.slice(0, 5);
        var fetches = targetMarkets.map(function (m) {
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
  }
});
