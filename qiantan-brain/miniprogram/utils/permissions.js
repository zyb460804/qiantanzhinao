/**
 * 前端权限门控映射。
 *
 * 权限名以后端实际定义的员工权限为准（backend/app/models/staff.py
 * ROLE_PERMISSIONS）：view_profit / change_price / purchase_confirm /
 * supplier_payment / credit_sale / order_refund / inventory_adjust /
 * record_waste / daily_settle / void_record / export_data /
 * manage_staff / batch_lock / batch_destroy。
 *
 * can(page, permissions) 供「我的」页工具网格等场景过滤无权限入口。
 * 老板身份（非员工模式）不走此函数，直接全部放行。
 */

var PAGE_PERMISSIONS = {
  // 看数据、做决策
  dashboard: ['view_profit'],
  report: ['view_profit'],
  sandbox: ['view_profit'],
  calendar: ['view_profit'],
  insight: ['view_profit'],

  // 进货与货
  purchase: ['purchase_confirm'],
  stocktake: ['inventory_adjust'],
  catalog: ['inventory_adjust'],
  supplier: ['supplier_payment'],
  vision: null, // 未映射到明确权限，视为员工可用的通用工具

  // 钱与安全
  pos: ['credit_sale', 'order_refund'],
  finance: ['view_profit', 'daily_settle', 'export_data'],
  ops: ['record_waste', 'export_data', 'credit_sale', 'daily_settle'],
  trace: ['batch_lock', 'batch_destroy'],

  // 团队与设置
  staff: ['manage_staff'],
  devices: null, // 未映射到明确权限，保留通用入口
  notices: null, // 未映射到明确权限，保留通用入口
};

function can(page, permissions) {
  var perms = permissions || [];
  if (!Array.isArray(perms)) perms = [];
  var required = PAGE_PERMISSIONS[page];
  // 未登记页面视为普通入口，不额外拦截。
  if (!required) return true;
  if (required.length === 0) return false;
  return required.some(function (permission) {
    return perms.indexOf(permission) >= 0;
  });
}

module.exports = {
  can: can,
  PAGE_PERMISSIONS: PAGE_PERMISSIONS,
};
