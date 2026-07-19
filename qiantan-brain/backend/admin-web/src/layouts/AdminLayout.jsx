import {
  LogoutOutlined,
  DashboardOutlined,
  TeamOutlined,
  AppstoreOutlined,
  TransactionOutlined,
  FileTextOutlined,
  BarChartOutlined,
  MoonOutlined,
  SunOutlined,
  AuditOutlined,
  RocketOutlined,
  BulbOutlined,
  CameraOutlined,
  SafetyOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
  FundProjectionScreenOutlined,
} from '@ant-design/icons'
import { Avatar, Button, ConfigProvider, Dropdown, Layout, Menu, Spin, Switch, Typography, theme } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { hasPermission, PERMISSIONS } from '../permissions'
import { antdTokens } from '../theme/tokens'

const { Header, Sider, Content } = Layout
const DARK_MODE_KEY = 'qiantan_admin_dark_mode'

function getInitialDarkMode() {
  try {
    const stored = localStorage.getItem(DARK_MODE_KEY)
    if (stored !== null) return stored === 'true'
  } catch {
    /* ignore */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

const menuItems = [
  { key: '/dashboard', label: '数据看板', icon: <DashboardOutlined />, permission: PERMISSIONS.DASHBOARD_READ },
  {
    key: '/monitoring',
    label: '运维监控',
    icon: <FundProjectionScreenOutlined />,
    permission: PERMISSIONS.DASHBOARD_READ,
  },
  { key: '/tenants', label: '租户管理', icon: <TeamOutlined />, permission: PERMISSIONS.TENANT_READ },
  { key: '/plans', label: '套餐管理', icon: <AppstoreOutlined />, permission: PERMISSIONS.PLAN_READ },
  {
    key: '/subscriptions',
    label: '订阅管理',
    icon: <TransactionOutlined />,
    permission: PERMISSIONS.SUBSCRIPTION_READ,
  },
  { key: '/invoices', label: '发票管理', icon: <FileTextOutlined />, permission: PERMISSIONS.INVOICE_READ },
  { key: '/usage', label: '用量监控', icon: <BarChartOutlined />, permission: PERMISSIONS.USAGE_READ },
  { key: '/audit', label: '审计日志', icon: <AuditOutlined />, permission: PERMISSIONS.AUDIT_READ },
  { key: '/onboarding', label: '接入向导', icon: <RocketOutlined />, permission: PERMISSIONS.TENANT_CREATE },
  { key: '/ai-ops', label: 'AI 运营', icon: <BulbOutlined />, permission: PERMISSIONS.AI_ACTION_READ },
  { key: '/devices', label: '设备监控', icon: <CameraOutlined />, permission: PERMISSIONS.DASHBOARD_READ },
  { key: '/admins', label: '管理员', icon: <SafetyOutlined />, permission: PERMISSIONS.ADMIN_MANAGE },
]

const brandTokens = antdTokens

function getSelectedMenuKey(pathname, items) {
  const matched = items.find((item) => pathname === item.key || pathname.startsWith(`${item.key}/`))
  return matched?.key || '/dashboard'
}

export default function AdminLayout() {
  const { admin, logout, loading } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [darkMode, setDarkMode] = useState(getInitialDarkMode)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(DARK_MODE_KEY, String(darkMode))
    } catch {
      /* ignore */
    }
  }, [darkMode])

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (event) => {
      try {
        if (localStorage.getItem(DARK_MODE_KEY) === null) {
          setDarkMode(event.matches)
        }
      } catch {
        /* ignore */
      }
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  if (loading) {
    return (
      <div className="app-loading">
        <Spin size="large" />
      </div>
    )
  }

  if (!admin) return null

  const visibleMenuItems = menuItems.filter((item) => hasPermission(item.permission, admin.role, admin.permissions))

  const avatarMenu = {
    items: [
      {
        key: 'darkMode',
        icon: darkMode ? <SunOutlined /> : <MoonOutlined />,
        label: (
          <div className="theme-switch-row">
            <span>暗黑模式</span>
            <Switch
              aria-label="切换暗黑模式"
              size="small"
              checked={darkMode}
              onChange={setDarkMode}
              onClick={(_, event) => event.stopPropagation()}
            />
          </div>
        ),
      },
      { type: 'divider' },
      {
        key: 'logout',
        icon: <LogoutOutlined />,
        label: '退出登录',
        onClick: logout,
      },
    ],
  }

  return (
    <ConfigProvider
      theme={{
        algorithm: darkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: brandTokens,
      }}
    >
      <Layout className="admin-shell">
        <Sider
          className="admin-sider"
          collapsible
          collapsed={collapsed}
          trigger={null}
          width={224}
          theme={darkMode ? 'dark' : 'light'}
        >
          <button
            type="button"
            className="admin-brand"
            aria-label="返回数据概览"
            onClick={() => navigate('/dashboard')}
          >
            <span className="admin-brand-mark">千</span>
            {!collapsed && <span className="admin-brand-name">千摊智脑</span>}
          </button>
          <Menu
            mode="inline"
            theme={darkMode ? 'dark' : 'light'}
            selectedKeys={[getSelectedMenuKey(location.pathname, visibleMenuItems)]}
            items={visibleMenuItems}
            onClick={({ key }) => navigate(key)}
          />
        </Sider>

        <Layout>
          <Header className="admin-header">
            <Button
              type="text"
              className="collapse-button"
              aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            />
            <Dropdown menu={avatarMenu} trigger={['click']} placement="bottomRight">
              <button type="button" className="admin-account" aria-label="打开管理员菜单">
                <Avatar size="small" icon={<UserOutlined />} />
                <Typography.Text strong>{admin.name || '管理员'}</Typography.Text>
              </button>
            </Dropdown>
          </Header>
          <Content className="admin-content">
            <Outlet />
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}
