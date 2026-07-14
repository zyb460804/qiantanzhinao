import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Col, Progress, Row, Select, Space, Switch, Tag, Typography } from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ReloadOutlined,
  RobotOutlined,
  TeamOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Area, Bar, CartesianGrid, ComposedChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import api from '../api/client'
import EmptyState, { ErrorState } from '../components/EmptyState'
import PageHeader from '../components/PageHeader'

const compact = (value) =>
  new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0)

const statusConfig = {
  healthy: { text: '运行正常', color: '#16803C', tag: 'green' },
  warning: { text: '需要关注', color: '#D97706', tag: 'orange' },
  critical: { text: '存在故障', color: '#DC2626', tag: 'red' },
}

const checkStatus = {
  normal: { text: '正常', color: 'green' },
  warning: { text: '告警', color: 'orange' },
  critical: { text: '异常', color: 'red' },
}

function MonitorMetric({ title, value, hint, icon, tone }) {
  return (
    <div className={`monitor-metric monitor-metric--${tone}`}>
      <div className="monitor-metric__icon">{icon}</div>
      <div>
        <Typography.Text type="secondary">{title}</Typography.Text>
        <strong>{value}</strong>
        <small>{hint}</small>
      </div>
    </div>
  )
}

export default function Monitoring() {
  const [range, setRange] = useState(1)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.get(`/monitoring/overview?days=${range}`))
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || '运维数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  useEffect(() => {
    if (!autoRefresh) return undefined
    const timer = window.setInterval(fetchData, 60000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, fetchData])

  if (error && !data) {
    return (
      <div>
        <PageHeader title="运维监控" subtitle="平台健康与故障监测" />
        <ErrorState message={error} onRetry={fetchData} />
      </div>
    )
  }

  const status = statusConfig[data?.status] || statusConfig.warning
  const metrics = data
    ? [
        [
          '统计期请求',
          compact(data.request_total),
          `日均 ${compact(data.average_daily_requests)}`,
          <ApiOutlined />,
          'blue',
        ],
        ['今日请求', compact(data.today_requests), 'API 调用计量', <ClockCircleOutlined />, 'cyan'],
        ['活跃租户', data.active_tenants, '当前正常租户', <TeamOutlined />, 'green'],
        [
          '在线设备',
          `${data.device_online}/${data.device_total}`,
          `${data.device_stale} 台心跳超时`,
          <CloudServerOutlined />,
          'purple',
        ],
        ['AI 任务成功率', `${data.ai_success_rate}%`, `${data.ai_action_failed} 个失败`, <RobotOutlined />, 'orange'],
        ['设备错误', data.device_errors, '有错误记录的设备', <WarningOutlined />, 'red'],
      ]
    : []

  return (
    <div className="monitoring-page">
      <PageHeader
        title="运维监控"
        subtitle="平台健康、设备心跳与任务运行状态"
        extra={
          <Space wrap>
            <Select
              aria-label="监控时间范围"
              value={range}
              onChange={setRange}
              options={[
                { value: 1, label: '近 24 小时' },
                { value: 7, label: '近 7 天' },
                { value: 30, label: '近 30 天' },
              ]}
            />
            <span className="auto-refresh-control">
              自动刷新 <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
            </span>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={fetchData}>
              刷新
            </Button>
          </Space>
        }
      />

      <Card className="monitor-overview">
        <div className="monitor-health">
          <Progress
            type="circle"
            percent={data?.health_score || 0}
            size={124}
            strokeColor={status.color}
            format={(percent) => (
              <span className="health-progress-label">
                <strong>{percent}</strong>
                <small>健康度</small>
              </span>
            )}
          />
          <div className="monitor-health__copy">
            <Space>
              <span className="live-dot" style={{ background: status.color }} />
              <Typography.Title level={4}>{status.text}</Typography.Title>
              <Tag color={status.tag}>实时</Tag>
            </Space>
            <Typography.Text type="secondary">
              最近刷新 {data?.refreshed_at ? new Date(data.refreshed_at).toLocaleString('zh-CN') : '-'}
            </Typography.Text>
          </div>
        </div>
        <div className="monitor-metrics-grid">
          {metrics.map(([title, value, hint, icon, tone]) => (
            <MonitorMetric key={title} title={title} value={value} hint={hint} icon={icon} tone={tone} />
          ))}
        </div>
      </Card>

      <Typography.Title level={5} className="section-heading">
        核心服务
      </Typography.Title>
      <Row gutter={[16, 16]}>
        {(data?.checks || []).map((check) => {
          const current = checkStatus[check.status] || checkStatus.warning
          const icon =
            check.key === 'database' ? (
              <DatabaseOutlined />
            ) : check.key === 'ai_actions' ? (
              <RobotOutlined />
            ) : (
              <CheckCircleOutlined />
            )
          return (
            <Col xs={12} xl={6} key={check.key}>
              <Card className="service-check">
                <div className="service-check__top">
                  <span className="service-check__icon">{icon}</span>
                  <Tag color={current.color}>{current.text}</Tag>
                </div>
                <Typography.Text type="secondary">{check.name}</Typography.Text>
                <strong>{check.value}</strong>
                <small>{check.detail}</small>
              </Card>
            </Col>
          )
        })}
      </Row>

      <Row gutter={[16, 16]} className="monitor-lower-grid">
        <Col xs={24} xl={16}>
          <Card title="调用与任务趋势" className="dashboard-panel">
            <div className="chart-frame">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={data?.trend || []} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="monitorApiFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#2563EB" stopOpacity={0.24} />
                      <stop offset="100%" stopColor="#2563EB" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="#E6EBE8" />
                  <XAxis dataKey="date" tickFormatter={(value) => value.slice(5)} />
                  <YAxis yAxisId="request" tickFormatter={compact} width={48} />
                  <YAxis yAxisId="task" orientation="right" allowDecimals={false} width={28} />
                  <Tooltip />
                  <Legend />
                  <Area
                    yAxisId="request"
                    type="monotone"
                    dataKey="api_calls"
                    name="API 请求"
                    stroke="#2563EB"
                    fill="url(#monitorApiFill)"
                    strokeWidth={2.5}
                  />
                  <Bar yAxisId="task" dataKey="ai_actions" name="AI 任务" fill="#22A06B" radius={[4, 4, 0, 0]} />
                  <Bar yAxisId="task" dataKey="ai_failures" name="失败任务" fill="#DC2626" radius={[4, 4, 0, 0]} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="当前异常" className="dashboard-panel issue-panel">
            {(data?.checks || []).filter((check) => check.status !== 'normal').length ? (
              data.checks
                .filter((check) => check.status !== 'normal')
                .map((check) => (
                  <div className="issue-row" key={check.key}>
                    <WarningOutlined />
                    <div>
                      <strong>{check.name}</strong>
                      <small>{check.detail}</small>
                    </div>
                  </div>
                ))
            ) : (
              <EmptyState description="当前没有需要处理的异常" />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
