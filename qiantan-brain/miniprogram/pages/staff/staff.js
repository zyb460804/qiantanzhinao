/** 员工管理 — 添加/编辑/停用/角色权限
 *
 *  标签映射逻辑在 WXML 的 WXS 模块中，此处仅处理数据与交互。
 */
var app = getApp();

/** 角色中文标签映射（与后端 ROLE_PERMISSIONS key 对应）。
 * 后端 /staff/roles 返回角色清单后，roleOptions 会动态生成；
 * 此处仅作为角色中文显示的兜底来源，避免 WXML picker 拿不到标签。
 * 不含 'market_admin'：单摊贩业务上下文不该出现该角色（参见审计 P1-添加员工）。
 */
var ROLE_LABELS = {
  owner: '摊主', manager: '经理', cashier: '收银员',
  purchaser: '采购员', stocker: '理货员', market_admin: '市场管理'
};
/** 摊贩员工表单可选的角色（剔除 owner 仅自用、market_admin 与单摊贩业务无关） */
var FORM_ROLES = ['manager', 'cashier', 'purchaser', 'stocker'];

Page({
  stopMaskTap: function () {},

  data: {
    skinClass: '', loading: true, loadError: false,
    staffList: [], roles: [],
    // 停用员工列表（后端 ?include_inactive=true 返回 is_active=false 的员工）
    inactiveList: [], showInactive: false, loadingInactive: false,
    // 表单可选角色：动态从后端 /staff/roles 派生，剔除 market_admin
    roleOptions: [], roleOptionsReady: false,
    formVisible: false, formMode: 'create', formStaffId: '',
    form: { name: '', phone: '', role: 'cashier', pin_code: '' },
    // 编辑模式下显式标记"清空已有 PIN"（区别于"不修改 PIN"）
    formPinCleared: false,
    formSubmitting: false,
    expandedId: ''
  },

  onLoad: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
  },

  onShow: function () {
    this.setData({ skinClass: 'skin-' + app.resolveSkin() });
    this.loadAll();
    // 若上次展开过"已停用"列表，刷新一下保持同步
    if (this.data.showInactive) this.loadInactive();
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
      var roles = Array.isArray(rolesData) ? rolesData : [];
      // 由后端角色清单派生 picker 选项：过滤掉 market_admin（与单摊贩业务无关）
      // 后端未返回 owner 时也排除 owner（摊主本人不该出现在"添加员工"下拉里）
      var formRoleSet = {};
      FORM_ROLES.forEach(function (r) { formRoleSet[r] = true; });
      var roleOptions = roles
        .filter(function (r) { return formRoleSet[r.role]; })
        .map(function (r) { return { role: r.role, label: ROLE_LABELS[r.role] || r.role }; });
      // 后端未返回任何可选项时回退到 FORM_ROLES 默认集，避免 picker 为空
      if (roleOptions.length === 0) {
        roleOptions = FORM_ROLES.map(function (r) { return { role: r, label: ROLE_LABELS[r] || r }; });
      }
      self.setData({
        staffList: Array.isArray(staffData) ? staffData : [],
        roles: roles,
        roleOptions: roleOptions,
        roleOptionsReady: true,
        loading: false
      });
    });
  },

  /** 加载已停用员工（懒加载，切换显示时触发） */
  loadInactive: function () {
    var self = this;
    this.setData({ loadingInactive: true });
    app.request({ url: '/staff?include_inactive=true' })
      .then(function (data) {
        var all = Array.isArray(data) ? data : [];
        self.setData({
          inactiveList: all.filter(function (s) { return s.is_active === false; }),
          loadingInactive: false
        });
      })
      .catch(function () { self.setData({ loadingInactive: false }); });
  },

  /** 切换"已停用员工"列表显示 */
  toggleInactive: function () {
    var next = !this.data.showInactive;
    this.setData({ showInactive: next });
    if (next) this.loadInactive();
  },

  /** 恢复已停用员工 */
  reactivate: function (e) {
    var self = this;
    var sid = e.currentTarget.dataset.id;
    var staff = this.data.inactiveList.find(function (s) { return s.staff_id === sid; });
    var name = staff ? staff.name : '该员工';
    wx.showModal({
      title: '确认恢复',
      content: '恢复后 ' + name + ' 可重新进行操作。确定吗？',
      success: function (res) {
        if (!res.confirm) return;
        app.request({ url: '/staff/' + sid, method: 'PUT', data: { is_active: true } })
          .then(function () {
            wx.showToast({ title: '已恢复 ' + name, icon: 'success' });
            self.loadInactive();
            self.loadAll();
          })
          .catch(function () { /* app.request 已弹 toast，无需重复 */ });
      }
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
        form: { name: staff.name, phone: staff.phone || '', role: staff.role, pin_code: '' },
        formPinCleared: false
      });
    } else {
      this.setData({
        formVisible: true, formMode: 'create', formStaffId: '',
        form: { name: '', phone: '', role: 'cashier', pin_code: '' },
        formPinCleared: false
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
    // picker 组件: value 是索引，需要映射到 roleOptions 里对应的角色值
    if (field === 'role' && e.detail && e.detail.value !== undefined && typeof e.detail.value === 'number') {
      var opts = this.data.roleOptions;
      val = (opts[e.detail.value] && opts[e.detail.value].role) || 'cashier';
    } else {
      val = e.detail.value !== undefined ? e.detail.value : e.currentTarget.dataset.val;
    }
    var up = {};
    up['form.' + field] = val;
    // 编辑模式下用户改了 PIN 输入框，重置 formPinCleared 标记
    if (field === 'pin_code' && this.data.formMode === 'edit') {
      up.formPinCleared = false;
    }
    this.setData(up);
  },

  /** 编辑模式下显式清空已有 PIN：标记 formPinCleared=true 并清空输入框 */
  clearPin: function () {
    if (this.data.formMode !== 'edit') return;
    this.setData({ 'form.pin_code': '', formPinCleared: true });
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
    var body = { name: form.name.trim(), phone: form.phone || undefined, role: form.role };
    // 编辑模式下：
    //  - 用户输入了新 PIN：传新值；
    //  - 用户点了"清空 PIN"按钮：传 pin_code='' 显式清空（区别于"不改"）；
    //  - 用户没动 PIN：不传字段。
    // 创建模式下：空 PIN 不传字段。
    if (form.pin_code) {
      body.pin_code = form.pin_code;
    } else if (this.data.formMode === 'edit' && this.data.formPinCleared) {
      body.pin_code = '';
    }
    if (!body.phone) delete body.phone;

    app.request({ url: url, method: method, data: body })
      .then(function () {
        wx.showToast({ title: self.data.formMode === 'create' ? '已添加' : '已更新', icon: 'success' });
        self.setData({ formVisible: false, formSubmitting: false });
        self.loadAll();
      })
      .catch(function () {
        // app.request 在 business_error 时已弹后端 msg toast（app.js:273），
        // 这里再读 err.message 也是 undefined，避免双 toast，仅恢复按钮状态。
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
      content: '停用后 ' + name + ' 将无法进行任何操作。可在"已停用"列表恢复。确定吗？',
      confirmColor: '#c8392b',
      success: function (res) {
        if (!res.confirm) return;
        app.request({ url: '/staff/' + sid, method: 'DELETE' })
          .then(function () {
            wx.showToast({ title: '已停用 ' + name, icon: 'success' });
            self.loadAll();
            if (self.data.showInactive) self.loadInactive();
          })
          .catch(function () { /* app.request 已弹 toast */ });
      }
    });
  },

  /** 展开/折叠权限列表 */
  toggleExpand: function (e) {
    var sid = e.currentTarget.dataset.id;
    this.setData({ expandedId: this.data.expandedId === sid ? '' : sid });
  }
});
