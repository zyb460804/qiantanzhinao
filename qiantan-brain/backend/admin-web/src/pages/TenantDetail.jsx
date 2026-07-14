import { useEffect, useState, useCallback } from 'react'
import {
  Card,
  Descriptions,
  Tag,
  Button,
  Space,
  Tabs,
  Table,
  Typography,
  Statistic,
  Row,
  Col,
  Progress,
  message,
  Form,
  Select,
  Input,
} from 'antd'
import { ArrowLeftOutlined, EditOutlined } from '@ant-design/icons'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../api/client'
import EmptyState from '../components/EmptyState'
import { ErrorState } from '../components/EmptyState'
import { SkeletonCard } from '../components/SkeletonCard'

const statusColors = { trial: 'orange', active: 'green', suspended: 'red', expired: 'default' }

// ── 顶部摘要 ──────────────────────────────────────────

function TenantSummary({ tenant, loading }) {
  if (loading) return <SkeletonCard rows={2} />
  if (!tenant) return null
  return (
    <Card style={{ borderRadius: 10, marginBottom: 24 }}>
      <Row gutter={[24, 16]} align="middle">
        <Col flex="auto">
          <Space size="large" wrap>
            <div>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {tenant.name}
              </Typography.Title>
              <Typography.Text type="secondary">{tenant.slug}</Typography.Text>
            </div>
            <Tag color={statusColors[tenant.status]} style={{ fontSize: 13 }}>
              {{ trial: '试用中', active: '正常', suspended: '已停用', expired: '已过期' }[tenant.status] ||
                tenant.status}
            </Tag>
            {tenant.plan_name && <Tag color="blue">{tenant.plan_name}</Tag>}
            <Typography.Text type="secondary">
              商户数: <strong>{tenant.merchant_count || 0}</strong>
            </Typography.Text>
            <Typography.Text type="secondary">
              创建于 {tenant.created_at ? new Date(tenant.created_at).toLocaleDateString('zh-CN') : '-'}
            </Typography.Text>
          </Space>
        </Col>
        <Col>
          <Space>
            <Button icon={<EditOutlined />} onClick={() => document.getElementById('tab-org')?.click()}>
              编辑资料
            </Button>
          </Space>
        </Col>
      </Row>
    </Card>
  )
}

// ── 概览 Tab ──────────────────────────────────────────

function OverviewTab({ subscriptions, invoices, usage }) {
  const sub = subscriptions?.[0]
  const overdueInvoices = invoices?.filter((i) => i.status === 'overdue') || []
  const availableTabs = [
    { key: 'org', label: '组织资料', desc: '基本信息、联系人、备注' },
    { key: 'sub', label: '订阅与账单', desc: `${sub?.status || '无'} · ${invoices?.length || 0} 张账单` },
    { key: 'usage', label: '用量与配额', desc: `${usage?.quotas?.length || 0} 项指标` },
    { key: 'device', label: '设备与同步', desc: '设备状态与同步记录' },
    { key: 'ai', label: 'AI 使用', desc: 'AI 识别与建议统计' },
    { key: 'risk', label: '风险与审计', desc: '审计与安全摘要' },
  ]

  return (
    <Row gutter={[16, 16]}>
      {/* 快速导航 */}
      <Col span={24}>
        <Card title="快速导航" style={{ borderRadius: 10 }} size="small">
          <Row gutter={[12, 12]}>
            {availableTabs.map((tab) => (
              <Col xs={12} sm={8} md={4} key={tab.key}>
                <Card
                  size="small"
                  hoverable={!tab.disabled}
                  style={{ borderRadius: 8, textAlign: 'center', opacity: tab.disabled ? 0.5 : 1 }}
                  onClick={tab.disabled ? undefined : () => document.getElementById(`tab-${tab.key}`)?.click()}
                >
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{tab.label}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {tab.desc}
                  </Typography.Text>
                </Card>
              </Col>
            ))}
          </Row>
        </Card>
      </Col>

      {/* 关键指标 */}
      <Col xs={24} md={8}>
        <Card title="订阅状态" style={{ borderRadius: 10 }}>
          {sub ? (
            <Descriptions column={1} size="small">
              <Descriptions.Item label="套餐">{sub.plan_name}</Descriptions.Item>
              <Descriptions.Item label="计费周期">{sub.billing_cycle === 'yearly' ? '年付' : '月付'}</Descriptions.Item>
              <Descriptions.Item label="状态">
                {(() => {
                  const c = {
                    trialing: 'blue',
                    active: 'green',
                    past_due: 'orange',
                    canceled: 'default',
                    expired: 'red',
                  }
                  const l = {
                    trialing: '试用中',
                    active: '活跃',
                    past_due: '逾期',
                    canceled: '已取消',
                    expired: '已过期',
                  }
                  return <Tag color={c[sub.status] || 'default'}>{l[sub.status] || sub.status}</Tag>
                })()}
              </Descriptions.Item>
              {sub.current_period_end && (
                <Descriptions.Item label="周期截止">
                  {new Date(sub.current_period_end).toLocaleDateString('zh-CN')}
                </Descriptions.Item>
              )}
            </Descriptions>
          ) : (
            <EmptyState description="暂无活跃订阅" />
          )}
        </Card>
      </Col>

      <Col xs={24} md={8}>
        <Card title="账单概况" style={{ borderRadius: 10 }}>
          <Statistic title="总账单" value={invoices?.length || 0} />
          {overdueInvoices.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <Tag color="red">{overdueInvoices.length} 张逾期</Tag>
              <span style={{ fontSize: 13, color: '#DC2626' }}>
                合计 ¥{overdueInvoices.reduce((sum, i) => sum + Number(i.amount || 0), 0).toFixed(2)}
              </span>
            </div>
          )}
        </Card>
      </Col>

      <Col xs={24} md={8}>
        <Card title="用量概览" style={{ borderRadius: 10 }}>
          {usage?.quotas?.slice(0, 3).map((q) => (
            <div key={q.metric} style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                <span>
                  {{ api_calls: 'API调用', storage_mb: '存储', merchant_count: '商户' }[q.metric] || q.metric}
                </span>
                <span>
                  {q.current} / {q.limit}
                </span>
              </div>
              <Progress
                percent={q.limit > 0 ? Math.round((q.current / q.limit) * 100) : 0}
                size="small"
                status={q.exceeded ? 'exception' : q.current / q.limit > 0.8 ? 'active' : 'normal'}
              />
            </div>
          )) || <EmptyState description="暂无用量数据" />}
        </Card>
      </Col>
    </Row>
  )
}

// ── 组织资料 Tab ──────────────────────────────────────

function OrgTab({ tenant, plans, onSave }) {
  const [editing, setEditing] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    if (tenant) {
      form.setFieldsValue({
        name: tenant.name,
        status: tenant.status,
        contact_email: tenant.contact_email,
        contact_phone: tenant.contact_phone,
        admin_notes: tenant.admin_notes,
        plan_id: tenant.plan_id,
      })
    }
  }, [tenant, form])

  const handleSave = async (values) => {
    await onSave(values)
    setEditing(false)
    message.success('保存成功')
  }

  if (editing) {
    return (
      <Card title="编辑租户资料" id="tab-org" style={{ borderRadius: 10 }}>
        <Form form={form} layout="vertical" onFinish={handleSave} style={{ maxWidth: 600 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { value: 'trial', label: '试用中' },
                { value: 'active', label: '正常' },
                { value: 'suspended', label: '已停用' },
                { value: 'expired', label: '已过期' },
              ]}
            />
          </Form.Item>
          <Form.Item name="plan_id" label="套餐">
            <Select
              allowClear
              placeholder="选择套餐"
              options={plans.map((p) => ({ value: p.id, label: `${p.name} (${p.code})` }))}
            />
          </Form.Item>
          <Form.Item name="contact_email" label="联系邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="contact_phone" label="联系电话">
            <Input />
          </Form.Item>
          <Form.Item name="admin_notes" label="平台备注">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                保存
              </Button>
              <Button onClick={() => setEditing(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    )
  }

  return (
    <Card
      title="组织资料"
      id="tab-org"
      style={{ borderRadius: 10 }}
      extra={
        <Button icon={<EditOutlined />} onClick={() => setEditing(true)}>
          编辑
        </Button>
      }
    >
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="名称">{tenant?.name}</Descriptions.Item>
        <Descriptions.Item label="Slug">{tenant?.slug}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={statusColors[tenant?.status]}>{tenant?.status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="套餐">{tenant?.plan_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="商户数">{tenant?.merchant_count || 0}</Descriptions.Item>
        <Descriptions.Item label="订阅状态">{tenant?.subscription_status || '-'}</Descriptions.Item>
        <Descriptions.Item label="联系邮箱">{tenant?.contact_email || '-'}</Descriptions.Item>
        <Descriptions.Item label="联系电话">{tenant?.contact_phone || '-'}</Descriptions.Item>
        <Descriptions.Item label="试用到期">
          {tenant?.trial_ends_at ? new Date(tenant.trial_ends_at).toLocaleString('zh-CN') : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {tenant?.created_at ? new Date(tenant.created_at).toLocaleString('zh-CN') : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="备注" span={2}>
          {tenant?.admin_notes || '-'}
        </Descriptions.Item>
      </Descriptions>
    </Card>
  )
}

// ── 订阅与账单 Tab ────────────────────────────────────

function BillingTab({ subscriptions, invoices, loading }) {
  if (loading) return <SkeletonCard rows={5} />
  return (
    <div id="tab-sub">
      <Card title="订阅记录" style={{ borderRadius: 10, marginBottom: 16 }}>
        {subscriptions?.length > 0 ? (
          <Table
            size="small"
            dataSource={subscriptions}
            rowKey="id"
            pagination={false}
            columns={[
              { title: '套餐', dataIndex: 'plan_name' },
              { title: '周期', dataIndex: 'billing_cycle', render: (v) => (v === 'yearly' ? '年付' : '月付') },
              {
                title: '状态',
                dataIndex: 'status',
                render: (s) => {
                  const c = { active: 'green', trialing: 'blue', past_due: 'orange', canceled: 'default' }
                  return <Tag color={c[s] || 'default'}>{s}</Tag>
                },
              },
              {
                title: '周期截止',
                dataIndex: 'current_period_end',
                render: (v) => (v ? new Date(v).toLocaleDateString('zh-CN') : '-'),
              },
            ]}
          />
        ) : (
          <EmptyState description="暂无订阅记录" />
        )}
      </Card>

      <Card title="账单记录" style={{ borderRadius: 10 }}>
        {invoices?.length > 0 ? (
          <Table
            size="small"
            dataSource={invoices}
            rowKey="id"
            pagination={false}
            columns={[
              { title: '发票号', dataIndex: 'invoice_no' },
              { title: '金额', dataIndex: 'amount', render: (v) => `¥${Number(v).toFixed(2)}`, align: 'right' },
              {
                title: '状态',
                dataIndex: 'status',
                render: (s) => {
                  const c = { paid: 'green', overdue: 'red', draft: 'default', sent: 'blue', void: 'default' }
                  return <Tag color={c[s] || 'default'}>{s}</Tag>
                },
              },
              {
                title: '到期日',
                dataIndex: 'due_date',
                render: (v) => (v ? new Date(v).toLocaleDateString('zh-CN') : '-'),
              },
              {
                title: '创建',
                dataIndex: 'created_at',
                render: (v) => (v ? new Date(v).toLocaleDateString('zh-CN') : '-'),
              },
            ]}
          />
        ) : (
          <EmptyState description="暂无账单记录" />
        )}
      </Card>
    </div>
  )
}

// ── 用量与配额 Tab ────────────────────────────────────

function UsageTab({ usage, loading }) {
  if (loading) return <SkeletonCard rows={5} />
  return (
    <Card title="用量与配额" id="tab-usage" style={{ borderRadius: 10 }}>
      {usage?.quotas?.length > 0 ? (
        <Row gutter={[16, 16]}>
          {usage.quotas.map((q) => (
            <Col xs={24} sm={12} md={8} key={q.metric}>
              <Card size="small" style={{ borderRadius: 8 }}>
                <Statistic
                  title={
                    {
                      api_calls: 'API 调用数',
                      storage_mb: '存储 (MB)',
                      merchant_count: '商户数',
                      voice_seconds: '语音 (秒)',
                    }[q.metric] || q.metric
                  }
                  value={q.current}
                  suffix={`/ ${q.limit}`}
                  valueStyle={{ fontSize: 20 }}
                />
                <Progress
                  percent={q.limit > 0 ? Math.round((q.current / q.limit) * 100) : 0}
                  size="small"
                  status={q.exceeded ? 'exception' : 'active'}
                />
                {q.exceeded && (
                  <Tag color="red" style={{ marginTop: 4 }}>
                    已超限
                  </Tag>
                )}
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
        <EmptyState description="暂无用量数据" />
      )}
    </Card>
  )
}

// ── 设备与同步 Tab ────────────────────────────────────

function DevicesTab({ tenantId: _tenantId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchDevices = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get(`/tenants/${_tenantId}/devices`)
      setData(res)
    } catch (err) {
      setError(err?.response?.data?.detail || '加载设备数据失败')
    } finally {
      setLoading(false)
    }
  }, [_tenantId])

  useEffect(() => {
    fetchDevices()
  }, [fetchDevices])

  if (loading) return <SkeletonCard rows={5} />
  if (error) return <ErrorState message={error} onRetry={fetchDevices} />

  const devices = data?.items || []
  if (!devices.length) {
    return (
      <Card title="设备与同步" id="tab-device" style={{ borderRadius: 10 }}>
        <EmptyState description="暂无设备数据" />
      </Card>
    )
  }

  const onlineCount = devices.filter((d) => d.online_status).length

  return (
    <div id="tab-device">
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic title="设备总数" value={data.total} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic title="在线设备" value={onlineCount} valueStyle={{ color: '#3f8600' }} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="离线设备"
              value={data.total - onlineCount}
              valueStyle={{ color: data.total - onlineCount > 0 ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
      </Row>
      <Card title="设备列表" size="small" style={{ borderRadius: 10 }}>
        <Table
          size="small"
          dataSource={devices}
          rowKey="device_id"
          pagination={false}
          columns={[
            { title: '设备名称', dataIndex: 'device_name' },
            { title: '所属商户', dataIndex: 'merchant_name' },
            {
              title: '在线状态',
              dataIndex: 'online_status',
              render: (v) => <Tag color={v ? 'green' : 'default'}>{v ? '在线' : '离线'}</Tag>,
            },
            {
              title: '最后心跳',
              dataIndex: 'last_heartbeat',
              render: (v) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
            },
            { title: '模型版本', dataIndex: 'model_version', render: (v) => v || '-' },
            { title: '同步次数', dataIndex: 'sync_count', align: 'right' },
          ]}
        />
      </Card>
    </div>
  )
}

// ── AI 使用 Tab ────────────────────────────────────────

function AIUsageTab({ tenantId: _tenantId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchUsage = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get(`/tenants/${_tenantId}/ai-usage`)
      setData(res)
    } catch (err) {
      setError(err?.response?.data?.detail || '加载 AI 使用数据失败')
    } finally {
      setLoading(false)
    }
  }, [_tenantId])

  useEffect(() => {
    fetchUsage()
  }, [fetchUsage])

  if (loading) return <SkeletonCard rows={5} />
  if (error) return <ErrorState message={error} onRetry={fetchUsage} />

  const totalVision = data?.vision_counts?.reduce((a, b) => a + b, 0) || 0
  const totalVoice = data?.voice_counts?.reduce((a, b) => a + b, 0) || 0
  const totalAdvice = data?.advice_counts?.reduce((a, b) => a + b, 0) || 0

  const tableData = (data?.dates || [])
    .map((date, i) => ({
      key: date,
      date,
      vision: data.vision_counts?.[i] || 0,
      voice: data.voice_counts?.[i] || 0,
      advice: data.advice_counts?.[i] || 0,
    }))
    .filter((row) => row.vision > 0 || row.voice > 0 || row.advice > 0)

  return (
    <div id="tab-ai">
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic title="视觉识别 (30天)" value={totalVision} suffix="次" />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic title="语音识别 (30天)" value={totalVoice} suffix="次" />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic title="AI 建议 (30天)" value={totalAdvice} suffix="条" />
          </Card>
        </Col>
      </Row>
      <Card title="按日统计" size="small" style={{ borderRadius: 10 }}>
        {tableData.length > 0 ? (
          <Table
            size="small"
            dataSource={tableData}
            rowKey="key"
            pagination={{ pageSize: 15, showSizeChanger: false }}
            columns={[
              { title: '日期', dataIndex: 'date' },
              { title: '视觉识别', dataIndex: 'vision', align: 'right' },
              { title: '语音识别', dataIndex: 'voice', align: 'right' },
              { title: 'AI 建议', dataIndex: 'advice', align: 'right' },
            ]}
          />
        ) : (
          <EmptyState description="近 30 天无 AI 使用记录" />
        )}
      </Card>
    </div>
  )
}

// ── 风险与审计 Tab ──────────────────────────────────────

function RiskAuditTab({ tenantId: _tenantId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchRisk = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get(`/tenants/${_tenantId}/risk-audit`)
      setData(res)
    } catch (err) {
      setError(err?.response?.data?.detail || '加载风险数据失败')
    } finally {
      setLoading(false)
    }
  }, [_tenantId])

  useEffect(() => {
    fetchRisk()
  }, [fetchRisk])

  if (loading) return <SkeletonCard rows={5} />
  if (error) return <ErrorState message={error} onRetry={fetchRisk} />

  const hasAbnormal = (data?.abnormal_patterns?.length || 0) > 0
  const highAudit = (data?.total_audit_events_last_30d || 0) > 100

  return (
    <div id="tab-risk">
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic title="商户数" value={data?.merchant_count || 0} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="审计事件 (30天)"
              value={data?.total_audit_events_last_30d || 0}
              valueStyle={{ color: highAudit ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="异常模式"
              value={data?.abnormal_patterns?.length || 0}
              valueStyle={{ color: hasAbnormal ? '#cf1322' : '#3f8600' }}
              suffix="项"
            />
          </Card>
        </Col>
      </Row>

      <Card title="审计摘要" size="small" style={{ borderRadius: 10 }}>
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="租户商户数">{data?.merchant_count || 0}</Descriptions.Item>
          <Descriptions.Item label="近30天审计事件">{data?.total_audit_events_last_30d || 0}</Descriptions.Item>
          <Descriptions.Item label="异常模式" span={2}>
            {hasAbnormal ? (
              data.abnormal_patterns.map((p, i) => (
                <Tag key={i} color="red">
                  {p}
                </Tag>
              ))
            ) : (
              <Typography.Text type="secondary">未检测到异常模式</Typography.Text>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  )
}

// ── 主组件 ────────────────────────────────────────────

export default function TenantDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [tenant, setTenant] = useState(null)
  const [plans, setPlans] = useState([])
  const [subscriptions, setSubscriptions] = useState([])
  const [invoices, setInvoices] = useState([])
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')

  const fetchAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [t, p] = await Promise.all([api.get(`/tenants/${id}`), api.get('/plans')])
      setTenant(t)
      setPlans(p || [])

      // 并行加载子数据
      const [subs, invs, usageRes] = await Promise.allSettled([
        api.get('/subscriptions', { params: { tenant_id: id, page_size: 50 } }),
        api.get('/invoices', { params: { tenant_id: id, page_size: 50 } }),
        api.get(`/usage/${id}/overview`),
      ])
      setSubscriptions(subs.status === 'fulfilled' ? subs.value?.items || [] : [])
      setInvoices(invs.status === 'fulfilled' ? invs.value?.items || [] : [])
      setUsage(usageRes.status === 'fulfilled' ? usageRes.value : null)
    } catch (err) {
      setError(err?.response?.data?.detail || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  const handleSave = async (values) => {
    await api.put(`/tenants/${id}`, values)
    await fetchAll()
  }

  if (error) {
    return (
      <div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tenants')} style={{ marginBottom: 16 }}>
          返回列表
        </Button>
        <ErrorState message={error} onRetry={fetchAll} />
      </div>
    )
  }

  const tabItems = [
    {
      key: 'overview',
      label: '概览',
      children: <OverviewTab subscriptions={subscriptions} invoices={invoices} usage={usage} />,
    },
    {
      key: 'org',
      label: '组织资料',
      children: <OrgTab tenant={tenant} plans={plans} onSave={handleSave} />,
    },
    {
      key: 'sub',
      label: '订阅与账单',
      children: <BillingTab subscriptions={subscriptions} invoices={invoices} loading={loading} />,
    },
    {
      key: 'usage',
      label: '用量与配额',
      children: <UsageTab usage={usage} loading={loading} />,
    },
    {
      key: 'device',
      label: '设备与同步',
      children: <DevicesTab tenantId={id} />,
    },
    {
      key: 'ai',
      label: 'AI 使用',
      children: <AIUsageTab tenantId={id} />,
    },
    {
      key: 'risk',
      label: '风险与审计',
      children: <RiskAuditTab tenantId={id} />,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tenants')}>
          返回列表
        </Button>
      </div>

      <TenantSummary tenant={tenant} loading={loading} />

      <Card style={{ borderRadius: 10 }} bodyStyle={{ padding: '0 24px' }}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          style={{ marginTop: 0 }}
          tabBarStyle={{ marginBottom: 0 }}
        />
      </Card>
    </div>
  )
}
