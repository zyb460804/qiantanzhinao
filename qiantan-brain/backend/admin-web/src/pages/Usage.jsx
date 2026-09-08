import { useState, useEffect, useCallback, useRef } from 'react'
import { Card, Row, Col, Statistic, Progress, Select, Button, Table, Space, Tag } from 'antd'
import { ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'

const metricLabels = {
  api_calls: 'API 调用数',
  storage_mb: '存储 (MB)',
  merchant_count: '商户数',
}
const metricUnits = { api_calls: '次', storage_mb: 'MB', merchant_count: '个' }

export default function Usage() {
  const [tenants, setTenants] = useState([])
  const [tenantTotal, setTenantTotal] = useState(0)
  const [tenantPage, setTenantPage] = useState(1)
  const [tenantLoading, setTenantLoading] = useState(false)
  const [tenantSearch, setTenantSearch] = useState('')
  const [selectedTenant, setSelectedTenant] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('tenant_id') || undefined
  })
  const [urlTenantId] = useState(() => new URLSearchParams(window.location.search).get('tenant_id') || null)
  const urlTenantLoaded = useRef(false)
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)
  const [trendMetric, setTrendMetric] = useState('api_calls')
  const [trendData, setTrendData] = useState([])
  const [loading, setLoading] = useState(false)

  const fetchTenants = useCallback(async (page = 1, search = '', append = false) => {
    setTenantLoading(true)
    try {
      const params = { page, page_size: 20 }
      if (search) params.search = search
      const res = await api.get('/tenants', { params })
      const items = res.items || []
      setTenants((prev) => (append ? [...prev, ...items.filter((item) => !prev.some((p) => p.id === item.id))] : items))
      setTenantTotal(res.total || 0)
      setTenantPage(page)
    } catch {
      /* error handled globally */
    } finally {
      setTenantLoading(false)
    }
  }, [])

  const fetchOverview = useCallback(async () => {
    if (!selectedTenant) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.get(`/usage/${selectedTenant}/overview`)
      setOverview(res)
      const trend = await api.get(`/usage/${selectedTenant}/trend/${trendMetric}`, { params: { days: 30 } })
      setTrendData(trend || [])
    } catch (err) {
      setError(err?.response?.data?.detail || '加载用量数据失败')
    } finally {
      setLoading(false)
    }
  }, [selectedTenant, trendMetric])

  useEffect(() => {
    fetchTenants(1, '', false)
  }, [fetchTenants])

  useEffect(() => {
    if (!urlTenantId || urlTenantLoaded.current) return
    const found = tenants.some((t) => t.id === urlTenantId)
    if (found) {
      if (!selectedTenant) setSelectedTenant(urlTenantId)
      urlTenantLoaded.current = true
      return
    }
    if (!tenantLoading) {
      urlTenantLoaded.current = true
      api
        .get(`/tenants/${urlTenantId}`)
        .then((t) => {
          setTenants((prev) => [t, ...prev.filter((p) => p.id !== t.id)])
          if (!selectedTenant || selectedTenant === urlTenantId) setSelectedTenant(urlTenantId)
        })
        .catch(() => {
          /* URL 参数对应的租户不存在，保留手动选择 */
        })
    }
  }, [urlTenantId, selectedTenant, tenants, tenantLoading])

  useEffect(() => {
    fetchOverview()
  }, [fetchOverview])

  const columns = [
    { title: '指标', dataIndex: 'metric', key: 'metric', render: (v) => metricLabels[v] || v },
    {
      title: '当前用量',
      dataIndex: 'current',
      key: 'current',
      align: 'right',
      render: (v, r) => `${v?.toLocaleString()} ${metricUnits[r.metric] || ''}`,
    },
    {
      title: '配额上限',
      dataIndex: 'limit',
      key: 'limit',
      align: 'right',
      render: (v, r) => `${v?.toLocaleString()} ${metricUnits[r.metric] || ''}`,
    },
    {
      title: '剩余',
      dataIndex: 'remaining',
      key: 'remaining',
      align: 'right',
      render: (v, r) => `${v?.toLocaleString()} ${metricUnits[r.metric] || ''}`,
    },
    {
      title: '使用率',
      key: 'usage_rate',
      width: 160,
      render: (_, r) => {
        const pct = r.limit > 0 ? Math.round((r.current / r.limit) * 100) : 0
        return (
          <Progress percent={pct} status={pct >= 100 ? 'exception' : pct >= 80 ? 'active' : 'normal'} size="small" />
        )
      },
    },
    {
      title: '状态',
      key: 'status',
      width: 90,
      render: (_, r) =>
        r.exceeded ? (
          <Tag icon={<WarningOutlined />} color="red">
            已超限
          </Tag>
        ) : (
          <Tag color="green">正常</Tag>
        ),
    },
  ]

  return (
    <div>
      <PageHeader title="用量监控" subtitle="实时查看租户的资源使用情况" />

      <Card style={{ borderRadius: 10 }}>
        <Space style={{ marginBottom: 24 }} size="middle" wrap>
          <Select
            showSearch
            placeholder="搜索/选择租户"
            style={{ width: 280 }}
            value={selectedTenant}
            onChange={setSelectedTenant}
            filterOption={false}
            loading={tenantLoading}
            onSearch={(value) => {
              setTenantSearch(value)
              fetchTenants(1, value, false)
            }}
            onPopupScroll={(event) => {
              const target = event.target
              if (
                target.scrollTop + target.clientHeight >= target.scrollHeight - 20 &&
                tenants.length < tenantTotal &&
                !tenantLoading
              ) {
                fetchTenants(tenantPage + 1, tenantSearch, true)
              }
            }}
            notFoundContent={tenantLoading ? '加载中...' : '未找到租户'}
            options={tenants.map((t) => ({ value: t.id, label: `${t.name} (${t.slug})` }))}
          />
          <Select
            style={{ width: 130 }}
            value={trendMetric}
            onChange={setTrendMetric}
            options={Object.entries(metricLabels).map(([v, l]) => ({ value: v, label: l }))}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchOverview}>
            刷新
          </Button>
        </Space>

        {!selectedTenant && <EmptyState description="请选择一个租户查看用量数据" />}

        {selectedTenant && error && (
          <EmptyState
            description={error}
            action={
              <Button type="primary" onClick={fetchOverview}>
                重试
              </Button>
            }
          />
        )}

        {overview && !error && (
          <>
            <Row gutter={16} style={{ marginBottom: 24 }}>
              <Col xs={24} sm={8}>
                <Card size="small" style={{ borderRadius: 8 }}>
                  <Statistic title="租户" value={overview.tenant_name} />
                </Card>
              </Col>
              <Col xs={24} sm={8}>
                <Card size="small" style={{ borderRadius: 8 }}>
                  <Statistic
                    title="套餐"
                    value={overview.plan_name || '-'}
                    suffix={overview.plan_code ? `(${overview.plan_code})` : ''}
                  />
                </Card>
              </Col>
              <Col xs={24} sm={8}>
                <Card size="small" style={{ borderRadius: 8 }}>
                  <Statistic title="指标数" value={overview.quotas?.length || 0} />
                </Card>
              </Col>
            </Row>

            <Table
              columns={columns}
              dataSource={overview.quotas}
              rowKey="metric"
              loading={loading}
              pagination={false}
              size="middle"
              style={{ marginBottom: 24 }}
            />

            <Card
              title={`${metricLabels[trendMetric] || trendMetric} 趋势（最近 30 天）`}
              size="small"
              style={{ borderRadius: 8 }}
            >
              {trendData.length > 0 ? (
                <Table
                  size="small"
                  pagination={{ pageSize: 10 }}
                  dataSource={trendData}
                  rowKey="date"
                  columns={[
                    { title: '日期', dataIndex: 'date', key: 'date' },
                    {
                      title: '用量',
                      dataIndex: 'value',
                      key: 'value',
                      align: 'right',
                      render: (v) => `${v?.toLocaleString()} ${metricUnits[trendMetric] || ''}`,
                    },
                  ]}
                />
              ) : (
                <EmptyState description="暂无趋势数据" />
              )}
            </Card>
          </>
        )}
      </Card>
    </div>
  )
}
