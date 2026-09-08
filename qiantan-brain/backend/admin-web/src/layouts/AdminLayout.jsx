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
import { antdTokens, antdLightTokens } from '../theme/tokens'

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

const menuGroups = [
  {
    key: 'business',
    label: '经营',
    icon: <DashboardOutlined />,
    children: [
      { key: '/dashboard', label: '数据看板', icon: <DashboardOutlined />, permission: PERMISSIONS.DASHBOARD_READ },
      { key: '/usage', label: '用量监控', icon: <BarChartOutlined />, permission: PERMISSIONS.USAGE_READ },
    ],
  },
  {
    key: 'customers',
    label: '客户',
    icon: <TeamOutlined />,
    children: [
      { key: '/tenants', label: '租户管理', icon: <TeamOutlined />, permission: PERMISSIONS.TENANT_READ },
      { key: '/onboarding', label: '接入向导', icon: <RocketOutlined />, permission: PERMISSIONS.TENANT_CREATE },
    ],
  },
  {
    key: 'commercial',
    label: '商业化',
    icon: <TransactionOutlined />,
    children: [
      { key: '/plans', label: '套餐管理', icon: <AppstoreOutlined />, permission: PERMISSIONS.PLAN_READ },
      {
        key: '/subscriptions',
        label: '订阅管理',
        icon: <TransactionOutlined />,
        permission: PERMISSIONS.SUBSCRIPTION_READ,
      },
      { key: '/invoices', label: '发票管理', icon: <FileTextOutlined />, permission: PERMISSIONS.INVOICE_READ },
    ],
  },
  {
    key: 'ops',
    label: '运维',
    icon: <FundProjectionScreenOutlined />,
    children: [
      {
        key: '/monitoring',
        label: '运维监控',
        icon: <FundProjectionScreenOutlined />,
        permission: PERMISSIONS.DASHBOARD_READ,
      },
    ],
  },
  {
    key: 'ai',
    label: 'AI',
    icon: <BulbOutlined />,
    children: [{ key: '/ai-ops', label: 'AI 运营', icon: <BulbOutlined />, permission: PERMISSIONS.AI_ACTION_READ }],
  },
  {
    key: 'risk',
    label: '风控',
    icon: <AuditOutlined />,
    children: [{ key: '/audit', label: '审计日志', icon: <AuditOutlined />, permission: PERMISSIONS.AUDIT_READ }],
  },
  {
    key: 'system',
    label: '系统',
    icon: <SafetyOutlined />,
    children: [{ key: '/admins', label: '管理员', icon: <SafetyOutlined />, permission: PERMISSIONS.ADMIN_MANAGE }],
  },
]

const brandTokens = antdTokens

function getSelectedMenuKey(pathname, items) {
  for (const item of items) {
    if (item.children) {
      const matched = getSelectedMenuKey(pathname, item.children)
      if (matched) return matched
    } else if (pathname === item.key || pathname.startsWith(`${item.key}/`)) {
      return item.key
    }
  }
  return '/dashboard'
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

  const visibleMenuItems = menuGroups
    .map((group) => ({
      ...group,
      children: group.children.filter((item) =>
        hasPermission(item.permission, admin.role, admin.permissions),
      ),
    }))
    .filter((group) => group.children.length > 0)

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
        token: darkMode ? brandTokens : { ...brandTokens, ...antdLightTokens },
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
