/**
 * 管理后台前端权限模块
 *
 * 权限点常量与后端 app/core/admin_permissions.py 保持同步。
 * 前端权限仅用于 UI 隐藏/禁用，后端权限是最终安全边界。
 */

// ── 权限点常量 ───────────────────────────────────────────

export const PERMISSIONS = {
  DASHBOARD_READ: 'dashboard.read',
  TENANT_READ: 'tenant.read',
  TENANT_CREATE: 'tenant.create',
  TENANT_UPDATE: 'tenant.update',
  TENANT_SUSPEND: 'tenant.suspend',
  PLAN_READ: 'plan.read',
  PLAN_CREATE: 'plan.create',
  PLAN_UPDATE: 'plan.update',
  PLAN_DELETE: 'plan.delete',
  SUBSCRIPTION_READ: 'subscription.read',
  SUBSCRIPTION_CREATE: 'subscription.create',
  SUBSCRIPTION_CHANGE: 'subscription.change',
  INVOICE_READ: 'invoice.read',
  INVOICE_CREATE: 'invoice.create',
  INVOICE_UPDATE: 'invoice.update',
  INVOICE_MARK_PAID: 'invoice.mark_paid',
  USAGE_READ: 'usage.read',
  USAGE_ADJUST: 'usage.adjust',
  EXPORT_DATA: 'export.data',
  AI_ACTION_READ: 'ai_action.read',
  AI_ACTION_APPROVE: 'ai_action.approve',
  ADMIN_MANAGE: 'admin.manage',
  AUDIT_READ: 'audit.read',
}

// ── 角色定义 ─────────────────────────────────────────────

export const ROLES = {
  SUPER_ADMIN: 'super_admin',
  OPS_ADMIN: 'ops_admin',
  BILLING_ADMIN: 'billing_admin',
  SUPPORT_ADMIN: 'support_admin',
  AUDITOR: 'auditor',
}

// ── 角色显示名 ───────────────────────────────────────────

export const ROLE_LABELS = {
  [ROLES.SUPER_ADMIN]: '超级管理员',
  [ROLES.OPS_ADMIN]: '运营管理员',
  [ROLES.BILLING_ADMIN]: '计费管理员',
  [ROLES.SUPPORT_ADMIN]: '技术支持',
  [ROLES.AUDITOR]: '审计员',
}

// ── 高风险操作 ───────────────────────────────────────────

export const HIGH_RISK_PERMISSIONS = new Set([
  PERMISSIONS.INVOICE_MARK_PAID,
  PERMISSIONS.USAGE_ADJUST,
  PERMISSIONS.TENANT_SUSPEND,
  PERMISSIONS.PLAN_DELETE,
  PERMISSIONS.EXPORT_DATA,
  PERMISSIONS.ADMIN_MANAGE,
])

/**
 * 检查当前用户是否拥有指定权限。
 * super_admin 自动拥有所有权限。
 *
 * @param {string} permission - 权限点（如 PERMISSIONS.TENANT_CREATE）
 * @param {string} role - 当前管理员角色
 * @param {Set<string>|string[]} userPermissions - 当前用户的权限集合
 * @returns {boolean}
 */
export function hasPermission(permission, role, userPermissions) {
  if (role === ROLES.SUPER_ADMIN) return true
  if (!userPermissions) return false
  const perms = Array.isArray(userPermissions) ? new Set(userPermissions) : userPermissions
  return perms.has(permission)
}

/**
 * 获取所有需要审计确认的高风险操作列表。
 * @returns {string[]}
 */
export function getHighRiskPermissions() {
  return Array.from(HIGH_RISK_PERMISSIONS)
}
