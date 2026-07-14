import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Spin } from 'antd'
import { useAuth } from './context/AuthContext'
import ErrorBoundary from './components/ErrorBoundary'
import AdminLayout from './layouts/AdminLayout'
import Login from './pages/Login'
import Forbidden from './components/Forbidden'
import NotFound from './components/NotFound'
import { hasPermission, PERMISSIONS } from './permissions'

// Route-level lazy loading
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Monitoring = lazy(() => import('./pages/Monitoring'))
const Tenants = lazy(() => import('./pages/Tenants'))
const TenantDetail = lazy(() => import('./pages/TenantDetail'))
const Plans = lazy(() => import('./pages/Plans'))
const Subscriptions = lazy(() => import('./pages/Subscriptions'))
const Invoices = lazy(() => import('./pages/Invoices'))
const Usage = lazy(() => import('./pages/Usage'))
const AuditLog = lazy(() => import('./pages/AuditLog'))
const Onboarding = lazy(() => import('./pages/Onboarding'))
const AiOps = lazy(() => import('./pages/AiOps'))
const Devices = lazy(() => import('./pages/Devices'))
const Admins = lazy(() => import('./pages/Admins'))

const PageLoader = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 300 }}>
    <Spin size="large" />
  </div>
)

function ProtectedRoute({ children }) {
  const { admin, loading } = useAuth()
  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
  if (!admin) return <Navigate to="/login" replace />
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoader />}>{children}</Suspense>
    </ErrorBoundary>
  )
}

function PermissionRoute({ permission, children }) {
  const { admin } = useAuth()
  if (!admin || !hasPermission(permission, admin.role, admin.permissions)) {
    return <Forbidden />
  }
  return children
}

const permitted = (permission, element) => (
  <PermissionRoute permission={permission}>{element}</PermissionRoute>
)

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AdminLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={permitted(PERMISSIONS.DASHBOARD_READ, <Dashboard />)} />
        <Route path="monitoring" element={permitted(PERMISSIONS.DASHBOARD_READ, <Monitoring />)} />
        <Route path="tenants" element={permitted(PERMISSIONS.TENANT_READ, <Tenants />)} />
        <Route path="tenants/:id" element={permitted(PERMISSIONS.TENANT_READ, <TenantDetail />)} />
        <Route path="plans" element={permitted(PERMISSIONS.PLAN_READ, <Plans />)} />
        <Route
          path="subscriptions"
          element={permitted(PERMISSIONS.SUBSCRIPTION_READ, <Subscriptions />)}
        />
        <Route path="invoices" element={permitted(PERMISSIONS.INVOICE_READ, <Invoices />)} />
        <Route path="usage" element={permitted(PERMISSIONS.USAGE_READ, <Usage />)} />
        <Route path="audit" element={permitted(PERMISSIONS.AUDIT_READ, <AuditLog />)} />
        <Route path="onboarding" element={permitted(PERMISSIONS.TENANT_CREATE, <Onboarding />)} />
        <Route path="ai-ops" element={permitted(PERMISSIONS.AI_ACTION_READ, <AiOps />)} />
        <Route path="devices" element={permitted(PERMISSIONS.DASHBOARD_READ, <Devices />)} />
        <Route path="admins" element={permitted(PERMISSIONS.ADMIN_MANAGE, <Admins />)} />
      </Route>

      <Route path="/403" element={<Forbidden />} />
      <Route path="/404" element={<NotFound />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
