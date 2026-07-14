import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Row, Select, Space, Table, Tag, Typography } from 'antd'
import {
  ApiOutlined,
  AppstoreOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DollarOutlined,
  KeyOutlined,
  ReloadOutlined,
  ShopOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import {
  Area,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import EmptyState, { ErrorState } from '../components/EmptyState'
import PageHeader from '../components/PageHeader'
import { StatSkeleton } from '../components/SkeletonCard'

const COLORS = ['#167A5A', '#2563EB', '#D97706', '#7C3AED', '#0891B2', '#DC2626']

const TODO_LABELS = {
  expiring_subscription: '订阅到期',
  overdue_invoice: '账单逾期',
  trial_expiring: '试用到期',
  quota_warning: '配额预警',
  device_offline: '设备离线',
  device_warning: '设备异常',
}

const compact = (value) =>
  new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0)

const currency = (value) =>
  new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 0,
  }).format(Number(value || 0))

function MetricCard({ title, value, hint, icon, tone = 'green', onClick }) {
  return (
    <Card className={`metric-card metric-card--${tone}`} hoverable={Boolean(onClick)} onClick={onClick}>
      <div className="metric-card__icon">{icon}</div>
      <div className="metric-card__body">
        <Typography.Text type="secondary">{title}</Typography.Text>
        <div className="metric-card__value">{value}</div>
        <Typography.Text className="metric-card__hint" type="secondary">
          {hint}
        </Typography.Text>
      </div>
    </Card>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [range, setRange] = useState(7)
  const [stats, setStats] = useState(null)
  const [analytics, setAnalytics] = useState(null)
  const [planDist, setPlanDist] = useState([])
  const [activity, setActivity] = useState({ activities: [], todos: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [summary, detail, plans, activities] = await Promise.all([
        api.get('/dashboard'),
        api.get(`/dashboard/analytics?days=${range}`),
        api.get('/dashboard/plan-distribution'),
        api.get('/dashboard/activities').catch(() => ({ activities: [], todos: [] })),
      ])
      setStats(summary)
      setAnalytics(detail)
      setPlanDist(plans || [])
      setActivity(activities || { activities: [], todos: [] })
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || '加载数据失败')
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const metrics = useMemo(() => {
    if (!stats || !analytics) return []
    return [
      {
        title: '租户',
        value: stats.tenant_total,
        hint: `${stats.tenant_active} 个活跃 · ${analytics.active_tenant_rate}% 活跃率`,
        icon: <TeamOutlined />,
        tone: 'green',
        onClick: () => navigate('/tenants'),
      },
      {
        title: '商户',
        value: stats.merchant_total,
        hint: `分布于 ${stats.tenant_total} 个租户`,
        icon: <ShopOutlined />,
        tone: 'blue',
      },
      {
        title: '今日 API 请求',
        value: compact(stats.today_api_calls),
        hint: `本月 ${compact(analytics.month_api_calls)} 次`,
        icon: <ApiOutlined />,
        tone: 'cyan',
        onClick: () => navigate('/usage'),
      },
      {
        title: '活跃订阅',
        value: stats.subscription_active,
        hint: `${stats.plan_total} 个在售套餐`,
        icon: <AppstoreOutlined />,
        tone: 'purple',
        onClick: () => navigate('/subscriptions'),
      },
      {
        title: 'API 密钥',
        value: analytics.active_api_keys,
        hint: '当前启用',
        icon: <KeyOutlined />,
        tone: 'blue',
      },
      {
        title: '存储用量',
        value: `${compact(stats.today_storage_mb)} MB`,
        hint: '今日计量值',
        icon: <CloudServerOutlined />,
        tone: 'cyan',
      },
      {
        title: '语音处理',
        value: `${compact(analytics.month_voice_seconds)} 秒`,
        hint: '本月累计',
        icon: <CheckCircleOutlined />,
        tone: 'orange',
      },
      {
        title: '本月回款',
        value: currency(analytics.month_paid_revenue),
        hint: '已支付账单',
        icon: <DollarOutlined />,
        tone: 'red',
        onClick: () => navigate('/invoices'),
      },
    ]
  }, [analytics, navigate, stats])

  if (loading && !stats) {
    return (
      <div>
        <PageHeader title="数据看板" subtitle="平台经营与用量数据" />
        <Row gutter={[16, 16]}>
          {Array.from({ length: 8 }).map((_, index) => (
            <Col xs={12} md={6} key={index}>
              <StatSkeleton />
            </Col>
          ))}
        </Row>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <PageHeader title="数据看板" subtitle="平台经营与用量数据" />
        <ErrorState message={error} onRetry={fetchData} />
      </div>
    )
  }

  const pieData = planDist.map((item) => ({ name: item.plan_name, value: item.tenant_count }))
  const hasPlanData = pieData.some((item) => item.value > 0)
  const trend = analytics?.trend || []

  return (
    <div className="dashboard-page">
      <PageHeader
        title="数据看板"
        subtitle="系统概况与经营统计数据"
        extra={
          <Space>
            <Select
              aria-label="统计时间范围"
              value={range}
              onChange={setRange}
              options={[
                { value: 7, label: '近 7 天' },
                { value: 14, label: '近 14 天' },
                { value: 30, label: '近 30 天' },
              ]}
            />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={fetchData}>
              刷新
            </Button>
          </Space>
        }
      />

      <Row gutter={[16, 16]} className="dashboard-metrics">
        {metrics.map((metric) => (
          <Col xs={12} md={6} key={metric.title}>
            <MetricCard {...metric} />
          </Col>
        ))}
      </Row>

      <section className="quick-actions" aria-label="快捷操作">
        <Typography.Title level={5}>快捷操作</Typography.Title>
        <div className="quick-actions__items">
          {[
            ['租户管理', '查看接入、状态与套餐', '/tenants'],
            ['用量监控', '检查租户配额与趋势', '/usage'],
            ['运维监控', '查看平台健康与异常', '/monitoring'],
          ].map(([title, description, path]) => (
            <button type="button" key={path} onClick={() => navigate(path)}>
              <span>
                <strong>{title}</strong>
                <small>{description}</small>
              </span>
              <ArrowRightOutlined />
            </button>
          ))}
        </div>
      </section>

      <Row gutter={[16, 16]} className="dashboard-section">
        <Col xs={24} xl={15}>
          <Card title="平台用量趋势" className="dashboard-panel">
            <div className="chart-frame">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={trend} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="apiFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#2563EB" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#2563EB" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="#E6EBE8" />
                  <XAxis dataKey="date" tickFormatter={(value) => value.slice(5)} />
                  <YAxis yAxisId="usage" tickFormatter={compact} width={48} />
                  <YAxis yAxisId="tenant" orientation="right" allowDecimals={false} width={28} />
                  <Tooltip labelFormatter={(value) => `日期 ${value}`} />
                  <Legend />
                  <Area
                    yAxisId="usage"
                    type="monotone"
                    dataKey="api_calls"
                    name="API 请求"
                    stroke="#2563EB"
                    fill="url(#apiFill)"
                    strokeWidth={2.5}
                  />
                  <Bar
                    yAxisId="tenant"
                    dataKey="new_tenants"
                    name="新增租户"
                    fill="#22A06B"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={18}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="套餐分布" className="dashboard-panel">
            {hasPlanData ? (
              <div className="plan-distribution">
                <div className="plan-distribution__chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={pieData} innerRadius={52} outerRadius={78} paddingAngle={3} dataKey="value">
                        {pieData.map((item, index) => (
                          <Cell key={item.name} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <Table
                  size="small"
                  dataSource={planDist}
                  rowKey="plan_code"
                  pagination={false}
                  columns={[
                    { title: '套餐', dataIndex: 'plan_name' },
                    { title: '租户', dataIndex: 'tenant_count', align: 'right' },
                  ]}
                />
              </div>
            ) : (
              <EmptyState description="暂无套餐数据" />
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="待办提醒" className="dashboard-panel dashboard-list-panel">
            {(activity.todos || []).length ? (
              activity.todos.slice(0, 6).map((item) => (
                <button
                  type="button"
                  className="dashboard-list-row dashboard-list-action"
                  key={item.id}
                  onClick={() => item.target_path && navigate(item.target_path)}
                >
                  <Tag color={item.priority === 'high' ? 'red' : 'orange'}>
                    {TODO_LABELS[item.type] || item.title}
                  </Tag>
                  <span>{item.description}</span>
                  {item.due_at && (
                    <Typography.Text type="secondary">{item.due_at.slice(0, 10)}</Typography.Text>
                  )}
                </button>
              ))
            ) : (
              <EmptyState description="暂无待办事项" />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="最近动态" className="dashboard-panel dashboard-list-panel">
            {(activity.activities || []).length ? (
              activity.activities.slice(0, 6).map((item) => (
                <button
                  type="button"
                  className="dashboard-list-row dashboard-list-action"
                  key={item.id}
                  onClick={() => item.target_path && navigate(item.target_path)}
                >
                  <span className="activity-dot" />
                  <span>{item.title}</span>
                  <Typography.Text type="secondary">{item.time?.slice(0, 16).replace('T', ' ')}</Typography.Text>
                </button>
              ))
            ) : (
              <EmptyState description="暂无平台动态" />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
