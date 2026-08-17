import { useEffect, useState, useCallback } from 'react'
import { Table, Card, Input, Select, Space, Tag, Typography, Button, message, Badge } from 'antd'
import { SearchOutlined, PlusOutlined, ReloadOutlined, DownloadOutlined, ExportOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
import ConfirmWithReason from '../components/ConfirmWithReason'
import { PERMISSIONS } from '../permissions'
import PermissionGate from '../permissions/PermissionGate'

const statusColors = { trial: 'orange', active: 'green', suspended: 'red', expired: 'default' }
const statusLabels = { trial: '试用中', active: '正常', suspended: '已停用', expired: '已过期' }

// 健康度计算（简易版）
function getHealthBadge(tenant) {
  if (tenant.status === 'suspended') return { status: 'error', text: '停用' }
  if (tenant.status === 'expired') return { status: 'default', text: '过期' }
  if (tenant.usage_pct > 90) return { status: 'warning', text: '配额紧张' }
  if (tenant.usage_pct > 70) return { status: 'processing', text: '注意' }
  return { status: 'success', text: '健康' }
}

export default function Tenants() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(Number(searchParams.get('page')) || 1)
  const [pageSize, setPageSize] = useState(Number(searchParams.get('pageSize')) || 20)
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') || undefined)
  const [search, setSearch] = useState(searchParams.get('search') || '')
  const [selectedRowKeys, setSelectedRowKeys] = useState([])

  // Sync URL params
  const syncUrl = useCallback(
    (updates) => {
      const params = new URLSearchParams(searchParams)
      Object.entries(updates).forEach(([k, v]) => {
        if (v === undefined || v === null || v === '') params.delete(k)
        else params.set(k, String(v))
      })
      setSearchParams(params, { replace: true })
    },
    [searchParams, setSearchParams],
  )

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: pageSize }
      if (statusFilter) params.status = statusFilter
      if (search) params.search = search
      const res = await api.get('/tenants', { params })
      setData(res.items || [])
      setTotal(res.total || 0)
    } catch {
      // error handled globally
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, statusFilter, search])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleExport = () => {
    window.open('/api/admin/export/tenants', '_blank')
  }

  const handleBatchExport = async (_reason) => {
    try {
      const blob = await api.post(
        '/export/tenants/batch',
        {
          ids: selectedRowKeys,
          search: search || undefined,
          status: statusFilter || undefined,
        },
        { responseType: 'blob' },
      )
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `tenants-${Date.now()}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      message.success(`已导出 ${selectedRowKeys.length} 个租户`)
      setSelectedRowKeys([])
    } catch (err) {
      message.error(err?.response?.data?.detail || '批量导出失败')
    }
  }

  const columns = [
    {
      title: '租户名称',
      dataIndex: 'name',
      key: 'name',
      fixed: 'left',
      width: 160,
      render: (text, record) => (
        <a onClick={() => navigate(`/tenants/${record.id}`)} style={{ fontWeight: 500 }}>
          {text}
        </a>
      ),
    },
    { title: 'Slug', dataIndex: 'slug', key: 'slug', width: 140, ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s) => <Tag color={statusColors[s] || 'default'}>{statusLabels[s] || s}</Tag>,
    },
    {
      title: '套餐',
      dataIndex: 'plan_name',
      key: 'plan_name',
      width: 100,
      render: (v) => v || <Typography.Text type="secondary">-</Typography.Text>,
    },
    {
      title: '商户数',
      dataIndex: 'merchant_count',
      key: 'merchant_count',
      width: 80,
      align: 'right',
    },
    {
      title: '健康度',
      key: 'health',
      width: 100,
      render: (_, record) => {
        const h = getHealthBadge(record)
        return <Badge status={h.status} text={h.text} />
      },
    },
    {
      title: '联系邮箱',
      dataIndex: 'contact_email',
      key: 'contact_email',
      width: 180,
      ellipsis: true,
      render: (v) => v || '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
  ]

  return (
    <div>
      <PageHeader
        title="租户管理"
        subtitle={`共 ${total} 个租户，管理接入、套餐和状态`}
        extra={
          <Space>
            <PermissionGate permission={PERMISSIONS.TENANT_CREATE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/onboarding')}>
                新建租户
              </Button>
            </PermissionGate>
          </Space>
        }
      />

      <Card style={{ borderRadius: 10 }}>
        {/* 工具栏 */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
          <Input
            placeholder="搜索名称 / slug"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onPressEnter={() => {
              setPage(1)
              syncUrl({ search, page: 1 })
              fetchData()
            }}
            style={{ width: 240 }}
            allowClear
            onClear={() => {
              setSearch('')
              setPage(1)
              syncUrl({ search: undefined, page: 1 })
            }}
          />
          <Select
            placeholder="状态筛选"
            style={{ width: 120 }}
            allowClear
            value={statusFilter}
            onChange={(v) => {
              setStatusFilter(v)
              setPage(1)
              syncUrl({ status: v, page: 1 })
            }}
            options={Object.entries(statusLabels).map(([v, l]) => ({ value: v, label: l }))}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
          <PermissionGate permission={PERMISSIONS.EXPORT_DATA}>
            <ConfirmWithReason
              title="导出全部租户 CSV"
              description="将导出当前全部租户数据（不受列表筛选影响）"
              impact="包含租户名称、Slug、联系方式等敏感信息，请谨慎操作。"
              onSubmit={handleExport}
            >
              <Button icon={<DownloadOutlined />}>导出 CSV</Button>
            </ConfirmWithReason>
          </PermissionGate>
        </div>

        {/* 批量操作栏 */}
        {selectedRowKeys.length > 0 && (
          <div
            style={{
              marginBottom: 12,
              padding: '8px 12px',
              background: '#D8F7E9',
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
            }}
          >
            <span>
              已选 <strong>{selectedRowKeys.length}</strong> 项
            </span>
            <PermissionGate permission={PERMISSIONS.EXPORT_DATA}>
              <ConfirmWithReason
                title="批量导出租户"
                description={`导出已选中的 ${selectedRowKeys.length} 个租户；若未选择，则导出当前筛选结果。`}
                impact="包含租户敏感信息，将记录到审计日志。"
                onSubmit={handleBatchExport}
              >
                <Button size="small" icon={<ExportOutlined />}>
                  批量导出
                </Button>
              </ConfirmWithReason>
            </PermissionGate>
            <Button size="small" onClick={() => setSelectedRowKeys([])}>
              取消选择
            </Button>
          </div>
        )}

        <Table
          dataSource={data}
          columns={columns}
          rowKey="id"
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
          }}
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
              syncUrl({ page: p, pageSize: ps })
            },
          }}
        />
      </Card>
    </div>
  )
}
