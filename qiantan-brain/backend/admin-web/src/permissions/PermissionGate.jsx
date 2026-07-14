import { useAuth } from '../context/AuthContext'
import { hasPermission, ROLES } from './index'

/**
 * 权限守卫组件 — 无权限时隐藏子元素。
 *
 * 用法:
 *   <PermissionGate permission={PERMISSIONS.TENANT_CREATE}>
 *     <Button>新建租户</Button>
 *   </PermissionGate>
 */
export default function PermissionGate({ permission, children, fallback = null }) {
  const { admin } = useAuth()
  const role = admin?.role || ''
  const perms = admin?.permissions || []

  if (hasPermission(permission, role, perms)) {
    return children
  }
  return fallback
}

/**
 * 高风险操作守卫 — 仅 super_admin/billing_admin 可操作。
 */
export function HighRiskGate({ permission, children, fallback = null }) {
  const { admin } = useAuth()
  const role = admin?.role || ''
  const perms = admin?.permissions || []

  // 高风险操作 + 超级管理员检查
  if (role === ROLES.SUPER_ADMIN) return children
  if (!hasPermission(permission, role, perms)) return fallback
  return children
}
