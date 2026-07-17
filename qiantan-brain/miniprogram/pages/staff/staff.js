/** 员工管理 — 添加/编辑/停用/角色权限
 *
 *  标签映射逻辑在 WXML 的 WXS 模块中，此处仅处理数据与交互。
 */
var app = getApp();

Page({
  stopMaskTap: function () {},

  data: {
    skinClass: '', loading: true, loadError: false,
    staffList: [], roles: [],
    formVisible: false, formMode: 'create', formStaffId: '',
    form: { name: '', phone: '', role: 'cashier', pin_code: '' },
    formSubmitting: false,
    expandedId: ''
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadAll();
  },

  /** 加载员工列表 + 角色定义 */
  loadAll: function () {
    var self = this;
    this.setData({ loading: true, loadError: false });
    Promise.all([
      app.request({ url: '/staff' }).catch(function () { return null; }),
      app.request({ url: '/staff/roles' }).catch(function () { return null; })
    ]).then(function (results) {
      var staffData = results[0];
      var rolesData = results[1];
      if (!staffData && !rolesData) {
        self.setData({ loading: false, loadError: true });
        return;
      }
      // app.request 已解包 {code:0, data:...} → data
      self.setData({
        staffList: Array.isArray(staffData) ? staffData : [],
        roles: Array.isArray(rolesData) ? rolesData : [],
        loading: false
      });
    });
  },

  /** 打开表单 */
  openForm: function (e) {
    // stopMaskTap 防止穿透关闭
    this.setData({ expandedId: '' });
    if (this.data.formSubmitting) return;
    var mode = e ? e.currentTarget.dataset.mode : 'create';
    if (mode === 'edit') {
      var sid = e.currentTarget.dataset.id;
      var staff = this.data.staffList.find(function (s) { return s.staff_id === sid; });
      if (!staff) return;
      this.setData({
        formVisible: true, formMode: 'edit', formStaffId: sid,
        form: { name: staff.name, phone: staff.phone || '', role: staff.role, pin_code: staff.pin_code || '' }
      });
    } else {
      this.setData({
        formVisible: true, formMode: 'create', formStaffId: '',
        form: { name: '', phone: '', role: 'cashier', pin_code: '' }
      });
    }
  },

  closeForm: function () {
    if (this.data.formSubmitting) return;
    this.setData({ formVisible: false });
  },

  onFormField: function (e) {
    var field = e.currentTarget.dataset.field;
    var val;
    // picker 组件: value 是索引，需要映射到正确的角色值
    if (field === 'role' && e.detail && e.detail.value !== undefined && typeof e.detail.value === 'number') {
      var roles = ['owner', 'manager', 'cashier', 'purchaser', 'stocker', 'market_admin'];
      val = roles[e.detail.value] || 'cashier';
    } else {
      val = e.detail.value !== undefined ? e.detail.value : e.currentTarget.dataset.val;
    }
    var up = {};
    up['form.' + field] = val;
    this.setData(up);
  },

  /** 提交表单 */
  submitForm: function () {
    var self = this;
    var form = this.data.form;
    if (!form.name || !form.name.trim()) {
      wx.showToast({ title: '请输入员工姓名', icon: 'none' });
      return;
    }
    this.setData({ formSubmitting: true });

    var url = this.data.formMode === 'create' ? '/staff' : '/staff/' + this.data.formStaffId;
    var method = this.data.formMode === 'create' ? 'POST' : 'PUT';
    var body = { name: form.name.trim(), phone: form.phone || undefined, role: form.role, pin_code: form.pin_code || undefined };
    // 清理空值
    if (!body.phone) delete body.phone;
    if (!body.pin_code) delete body.pin_code;

    app.request({ url: url, method: method, data: body })
      .then(function () {
        wx.showToast({ title: self.data.formMode === 'create' ? '已添加' : '已更新', icon: 'success' });
        self.setData({ formVisible: false, formSubmitting: false });
        self.loadAll();
      })
      .catch(function (err) {
        wx.showToast({ title: (err && err.message) || '操作失败', icon: 'none' });
        self.setData({ formSubmitting: false });
      });
  },

  /** 停用员工 */
  deactivate: function (e) {
    var self = this;
    var sid = e.currentTarget.dataset.id;
    var staff = this.data.staffList.find(function (s) { return s.staff_id === sid; });
    var name = staff ? staff.name : '该员工';
    wx.showModal({
      title: '确认停用',
      content: '停用后 ' + name + ' 将无法进行任何操作。确定吗？',
      confirmColor: '#e2503e',
      success: function (res) {
        if (!res.confirm) return;
        app.request({ url: '/staff/' + sid, method: 'DELETE' })
          .then(function () {
            wx.showToast({ title: '已停用 ' + name, icon: 'success' });
            self.loadAll();
          })
          .catch(function (err) {
            wx.showToast({ title: (err && err.message) || '停用失败', icon: 'none' });
          });
      }
    });
  },

  /** 展开/折叠权限列表 */
  toggleExpand: function (e) {
    var sid = e.currentTarget.dataset.id;
    this.setData({ expandedId: this.data.expandedId === sid ? '' : sid });
  }
});
