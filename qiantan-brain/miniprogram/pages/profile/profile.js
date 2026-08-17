/**
 * 「我的」页面 v3.2 — 经营快照 + 快捷操作 + 设备同步 + 工具直达/折叠 + 设置入口 + 帮助
 * 摊位设置已拆为独立页面 /pages/stall-settings/stall-settings
 * v3.2 工具区收敛：17 职能宫格 → 常用 6 直达 + 更多工具折叠；租户入口移除；
 * v3.2.1 员工管理保留在「更多工具」折叠组（顶栏切换只管切身份，增删员工/配权限仍需入口）
 */
var app = getApp();
var Theme = require('../../utils/theme');
var Permissions = require('../../utils/permissions');

Page({
  data: {
    skinClass: '',
    // ① 经营快照
    snapshotLoading: true,
    snapshotError: false,
    todayRevenue: 0, todayRevenueDisplay: '0.00',
    todayOrders: 0, todayAov: '0.00', trendLabel: '—',
    weekTotal: 0, dayChangePct: null, dayChangeDir: '',

    // ② 快捷操作
    voiceLabel: '今天还没记',
    purchasePending: 0,

    // 员工身份切换（权限体系）
    staffMode: false,
    currentStaff: null,
    // 快捷操作可见性（老板全开，员工按权限过滤）
    canDashboard: true,
    canPos: true,
    canPurchase: true,

    // ③ 设备与同步
    devices: [], offlineQueueCount: 0, deviceError: false,

    // ④ 经营工具（v3.2 收敛）— 常用 6 直达 + 更多工具折叠
    //    原 17 宫格按开发者职能域分组，摊主视角改为「高频直达 + 低频折叠」。
    //    员工管理入口删除（顶栏身份切换已覆盖员工场景）；租户中心整页已下线。
    quickTools: [
      { page: 'purchase', name: '采购', glyph: '购', tone: 'corn' },
      { page: 'stocktake', name: '盘点', glyph: '盘', tone: 'blue' },
      { page: 'pos', name: '收银', glyph: '收', tone: 'green' },
      { page: 'ops', url: '/pages/ops/ops?tab=waste', name: '报损', glyph: '损', tone: 'corn' },
      { page: 'finance', name: '财务', glyph: '财', tone: 'green' },
      { page: 'catalog', name: '商品目录', glyph: '录', tone: 'blue' },
    ],
    moreTools: [
      { page: 'dashboard', name: '经营镜像', glyph: '镜', tone: 'green' },
      { page: 'report', name: '经营报告', glyph: '报', tone: 'blue' },
      { page: 'sandbox', name: '决策沙盘', glyph: '算', tone: 'corn' },
      { page: 'calendar', name: '经营日历', glyph: '历', tone: 'blue' },
      { page: 'vision', name: '拍照识货', glyph: '识', tone: 'blue' },
      { page: 'supplier', name: '供应商', glyph: '供', tone: 'corn' },
      { page: 'trace', name: '安全追溯', glyph: '溯', tone: 'green' },
      { page: 'ops', url: '/pages/ops/ops?tab=clearance', name: '临期清货', glyph: '清', tone: 'corn' },
      { page: 'ops', url: '/pages/ops/ops?tab=export', name: '数据导出', glyph: '出', tone: 'blue' },
      { page: 'devices', name: '设备管理', glyph: '设', tone: 'blue' },
      { page: 'staff', name: '员工管理', glyph: '员', tone: 'corn' },
      { page: 'notices', name: '市场通知', glyph: '告', tone: 'green' },
    ],
    moreOpen: false,

    // ⑤ 摊位设置入口（详情页：/pages/stall-settings/stall-settings）
    merchantName: '',

    // ⑦ 关于
    appVersion: '1.0.0',
  },

  onShow: function () {
    this.applySkin();
    this._syncStaffMode();
    this._loadMerchantName();
    this.loadSnapshot();
    this.loadDevices();
    this.refreshVoiceLabel();
    this.refreshPurchasePending();
  },

  // ── 员工身份切换（权限体系）──────────────────────────────
  _ROLE_LABELS: { owner: '老板', manager: '店长', cashier: '收银员', market_admin: '市场管理' },

  _syncStaffMode: function () {
    var staff = app.globalData.currentStaff;
    if (staff) {
      staff.roleLabel = this._ROLE_LABELS[staff.role] || staff.role;
    }
    this.setData({ staffMode: app.isStaffMode(), currentStaff: staff });
    this._applyToolGroups();
    this._applyQuickActions();
  },

  /** 按当前身份过滤工具入口：老板全量，员工只保留有权限页面（常用+更多同规则） */
  _applyToolGroups: function () {
    if (!this._allQuickTools) {
      this._allQuickTools = this.data.quickTools;
      this._allMoreTools = this.data.moreTools;
    }
    if (!app.isStaffMode()) {
      this.setData({ quickTools: this._allQuickTools, moreTools: this._allMoreTools });
      return;
    }
    var permissions = (app.globalData.currentStaff && app.globalData.currentStaff.permissions) || [];
    var filterByPerm = function (list) {
      return (list || []).filter(function (item) {
        return Permissions.can(item.page, permissions);
      });
    };
    this.setData({
      quickTools: filterByPerm(this._allQuickTools),
      moreTools: filterByPerm(this._allMoreTools),
    });
  },

  /** 更多工具折叠/展开 */
  toggleMoreTools: function () {
    this.setData({ moreOpen: !this.data.moreOpen });
  },

  /** 快捷操作同样按权限收敛，避免员工看到无权限入口 */
  _applyQuickActions: function () {
    if (!app.isStaffMode()) {
      this.setData({ canDashboard: true, canPos: true, canPurchase: true });
      return;
    }
    var permissions = (app.globalData.currentStaff && app.globalData.currentStaff.permissions) || [];
    this.setData({
      canDashboard: Permissions.can('dashboard', permissions),
      canPos: Permissions.can('pos', permissions),
      canPurchase: Permissions.can('purchase', permissions),
    });
  },

  onIdentityTap: function () {
    if (app.isStaffMode()) {
      // 当前是员工身份 → 确认退出
      wx.showModal({
        title: '退出员工身份',
        content: '退出后恢复为老板（全部权限），确定？',
        confirmText: '退出',
        success: function (res) {
          if (res.confirm) {
            app.exitStaff();
            wx.showToast({ title: '已恢复老板身份', icon: 'success' });
          }
        },
      });
    } else {
      // 老板身份 → 选员工 + 输 PIN 切换
      this._switchStaff();
    }
  },

  _switchStaff: function () {
    var self = this;
    app.request({ url: '/staff' }).then(function (list) {
      list = list || [];
      var active = list.filter(function (s) { return s.is_active; });
      if (!active.length) {
        wx.showToast({ title: '暂无员工，请先在员工管理添加', icon: 'none' });
        return;
      }
      // 1. 选员工
      wx.showActionSheet({
        itemList: active.map(function (s) {
          return s.name + '（' + (self._ROLE_LABELS[s.role] || s.role) + '）';
        }),
        success: function (res) {
          var chosen = active[res.tapIndex];
          // 2. 输 PIN
          wx.showModal({
            title: '验证 ' + chosen.name + ' 的 PIN',
            editable: true,
            placeholderText: '请输入 4-6 位 PIN 码',
            confirmText: '切换',
            success: function (r) {
              if (!r.confirm) return;
              var pin = (r.content || '').trim();
              if (!pin) {
                wx.showToast({ title: 'PIN 不能为空', icon: 'none' });
                return;
              }
              // 3. 调登录
              app.switchToStaff(chosen.staff_id, pin).then(function () {
                self._syncStaffMode();
                wx.showToast({ title: '已切换为 ' + chosen.name, icon: 'success' });
              }).catch(function (err) {
                var msg = (err && err.body && (err.body.detail || err.body.message)) || 'PIN 错误或网络异常';
                wx.showToast({ title: String(msg), icon: 'none' });
              });
            },
          });
        },
      });
    }).catch(function () {
      wx.showToast({ title: '员工列表加载失败', icon: 'none' });
    });
  },

  onPullDownRefresh: function () {
    var self = this;
    this.loadSnapshot(function () { wx.stopPullDownRefresh(); },
                      function () { wx.stopPullDownRefresh(); });
    this.loadDevices();
  },

  applySkin: function () {
    // 用 Theme.apply 尊重手动皮肤设置(skinManual),而非强制按小时
    // 皮肤切换 UI 已迁至摊位设置页，此处仅负责让本页跟随当前皮肤
    Theme.apply(this);
  },

  // ── ① 经营快照 ──────────────────────────────
  loadSnapshot: function (onSuccess, onError) {
    var self = this;
    this.setData({ snapshotLoading: true });

    Promise.all([
      app.request({ url: '/twin/dashboard' }).catch(function () { return null; }),
      app.request({ url: '/reports/daily' }).catch(function () { return null; }),
      // /reports/daily 不返回 week_total_revenue，改从 /reports/weekly 取 week_revenue
      app.request({ url: '/reports/weekly' }).catch(function () { return null; }),
    ]).then(function (results) {
      var dash = results[0];
      var daily = results[1];
      var weekly = results[2];

      if (!dash && !daily) {
        self.setData({ snapshotLoading: false, snapshotError: true });
        if (onError) onError();
        return;
      }

      var rev = dash ? (Number(dash.today_revenue) || 0) : 0;
      // 后端 /reports/daily 不返回 order_count（详见 app/routers/reports.py:268-292），
      // 只能用 sale_qty（销售件数）兜底，导致客单价被低估；TODO 后端补充 order_count 字段。
      var saleQty = daily ? (Number(daily.sale_qty) || 0) : 0;
      var orderCount = daily ? (Number(daily.order_count) || 0) : 0;
      var txnCount = orderCount > 0 ? orderCount : saleQty;
      var aov = txnCount > 0 ? (rev / txnCount) : 0;
      var yesterdayRev = daily ? (Number(daily.yesterday_revenue) || 0) : 0;
      var changePct = yesterdayRev > 0 ? ((rev - yesterdayRev) / yesterdayRev * 100) : null;
      var trendLabel = '— 待观察';
      if (daily && daily.revenue_change_pct !== undefined && daily.revenue_change_pct !== null) {
        var pct = Number(daily.revenue_change_pct);
        trendLabel = pct > 0 ? '↗ 向好' : (pct < 0 ? '↘ 走弱' : '▸ 持平');
      }
      // 本周累计：/reports/daily 不返回该字段，改从 /reports/weekly.week_revenue 读取。
      var weekRev = weekly ? (Number(weekly.week_revenue) || 0) : 0;

      self.setData({
        snapshotLoading: false, snapshotError: false,
        todayRevenue: rev, todayRevenueDisplay: rev.toFixed(2),
        todayOrders: txnCount, todayAov: aov.toFixed(2),
        trendLabel: trendLabel,
        dayChangePct: changePct,
        dayChangeDir: changePct === null ? '' : (changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'flat')),
        weekTotal: weekRev,
      });
      if (onSuccess) onSuccess();
    }).catch(function () {
      self.setData({ snapshotLoading: false, snapshotError: true });
      if (onError) onError();
    });
  },

  // ── ② 快捷操作 ──────────────────────────────
  refreshVoiceLabel: function () {
    var self = this;
    app.request({ url: '/voice/today-count' }).then(function (res) {
      var count = (res && res.today_count) || 0;
      self.setData({ voiceLabel: count > 0 ? '再记一笔' : '今天还没记' });
    }).catch(function () {});
  },

  refreshPurchasePending: function () {
    var draft = wx.getStorageSync('purchaseDraft') || [];
    this.setData({ purchasePending: draft.length });
  },

  // ── ③ 设备与同步 ────────────────────────────
  loadDevices: function () {
    var self = this;
    // 离线队列
    try {
      var queue = JSON.parse(wx.getStorageSync('qt_offline_queue') || '[]');
      var pending = 0;
      queue.forEach(function (item) { if (!item.synced) pending++; });
      this.setData({ offlineQueueCount: pending });
    } catch (e) {}

    // 设备状态
    app.request({ url: '/devices' }).catch(function () { return null; }).then(function (data) {
      if (!data || !Array.isArray(data)) { self.setData({ deviceError: true }); return; }
      var devices = data.slice(0, 2).map(function (d) {
        var lastBeat = d.last_heartbeat ? new Date(d.last_heartbeat) : null;
        var minsAgo = lastBeat ? Math.floor((Date.now() - lastBeat.getTime()) / 60000) : null;
        var status = !lastBeat ? 'offline' : (minsAgo < 5 ? 'online' : (minsAgo < 30 ? 'unstable' : 'offline'));
        // 后端 /devices 返回字段为 device_name / device_type (app/routers/device.py:42-43)，
        // 同时兼容旧字段 name / type 以防历史调用方破坏。
        var typeLabelMap = { scale: '智能秤', camera: '摄像头', esl: '价签', printer: '打印机' };
        var rawType = d.device_type || d.type || 'device';
        return {
          name: d.device_name || d.name || rawType || '设备',
          type: typeLabelMap[rawType] || rawType,
          status: status,
          heartbeat: minsAgo !== null ? (minsAgo < 1 ? '刚刚' : minsAgo + ' 分钟前') : '—',
        };
      });
      self.setData({ devices: devices, deviceError: false });
    });
  },

  triggerSync: function () {
    // 同步是 Promise，必须链式 catch；try/catch 只能捕获 sync() 同步抛错。
    try {
      require('../../utils/offline-sync').getQueue().sync()
        .then(function () { wx.showToast({ title: '同步成功', icon: 'success' }); })
        .catch(function () { wx.showToast({ title: '同步失败，请稍后重试', icon: 'none' }); });
    } catch (e) {
      wx.showToast({ title: '同步组件加载失败', icon: 'none' });
    }
  },

  // ── ⑤ 摊位设置入口 ──────────────────────────
  /** 仅读取摊位名称用于入口卡片展示；完整设置在独立页面 */
  _loadMerchantName: function () {
    this.setData({
      merchantName: app.globalData.merchantName || wx.getStorageSync('merchantName') || '',
    });
  },

  /** 退出登录 — 调用后端 logout 吊销 token + 清理本地状态 */
  logout: function () {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出当前账号吗？退出后需要重新登录。',
      confirmColor: '#c8392b',
      success: function (res) {
        if (!res.confirm) return;
        // 先调后端吊销 token（失败也继续清理本地）
        app.request({ url: '/auth/logout', method: 'POST' })
          .catch(function () { /* token 已无效或网络错误，仍继续清理本地状态 */ })
          .then(function () {
            app.clearLogin();
            wx.showToast({ title: '已退出登录', icon: 'success' });
            // 重启到首页，触发 ensureLogin 重新登录
            wx.reLaunch({ url: '/pages/index/index' });
          });
      },
    });
  },

  // ── 导航 ─────────────────────────────────────
  goQuick: function (e) {
    var page = e.currentTarget.dataset.page;
    if (!page) return;
    if (page === 'voice' || page === 'inventory' || page === 'advisor') wx.switchTab({ url: '/pages/' + page + '/' + page });
    else wx.navigateTo({ url: '/pages/' + page + '/' + page });
  },

  goDeep: function (e) {
    var page = e.currentTarget.dataset.page;
    var url = e.currentTarget.dataset.url;
    if (!page && !url) return;
    // 带 url 的入口（如报损/清货/导出直达 ops 对应 tab）直接使用完整地址
    if (url) { wx.navigateTo({ url: url }); return; }
    // tabBar 页必须用 switchTab，否则 navigateTo 会失败
    if (page === 'voice' || page === 'inventory' || page === 'advisor') {
      wx.switchTab({ url: '/pages/' + page + '/' + page });
      return;
    }
    wx.navigateTo({ url: '/pages/' + page + '/' + page });
  },

  goDevices: function () { wx.navigateTo({ url: '/pages/devices/devices' }); },
  goDashboard: function () { wx.navigateTo({ url: '/pages/dashboard/dashboard' }); },
  goStallSettings: function () { wx.navigateTo({ url: '/pages/stall-settings/stall-settings' }); },

  // ── ⑥ 帮助与反馈 ─────────────────────────────
  showFeedback: function () {
    var self = this;
    wx.showModal({
      title: '意见反馈', editable: true, placeholderText: '描述你的建议或遇到的问题...', content: '',
      success: function (res) {
        if (res.confirm && res.content && res.content.trim()) {
          app.request({
            url: '/feedback', method: 'POST',
            data: { content: res.content.trim(), page: 'pages/profile/profile', app_version: self.data.appVersion },
          }).then(function () { wx.showToast({ title: '感谢反馈！', icon: 'success' }); })
            .catch(function () { wx.showToast({ title: '提交失败，请稍后重试', icon: 'none' }); });
        }
      },
    });
  },

  showGuide: function () { wx.navigateTo({ url: '/pages/doc/doc?type=guide' }); },
  showFAQ: function () { wx.navigateTo({ url: '/pages/doc/doc?type=faq' }); },
  contactService: function () {
    var phone = (app.globalData && app.globalData.servicePhone) || '';
    if (!phone) {
      wx.showModal({
        title: '联系客服',
        content: '客服电话尚未配置，请在千摊智脑微信群反馈，或稍后再试。',
        confirmText: '知道了', showCancel: false,
      });
      return;
    }
    wx.showModal({
      title: '联系客服',
      content: '客服电话：' + phone + '\n工作时间 9:00-21:00',
      confirmText: '拨打',
      cancelText: '取消',
      success: function (r) {
        if (!r.confirm) return;
        var digits = phone.replace(/[^\d]/g, '');
        if (!digits) return;
        wx.makePhoneCall({ phoneNumber: digits }).catch(function () {});
      },
    });
  },
  showPrivacy: function () { wx.navigateTo({ url: '/pages/doc/doc?type=privacy' }); },
  showTerms: function () { wx.navigateTo({ url: '/pages/doc/doc?type=terms' }); },
});
