import { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Card,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  Button,
  Modal,
  Form,
  InputNumber,
  message,
  Badge,
} from 'antd'
import { SearchOutlined, PlusOutlined, ReloadOutlined, DownloadOutlined, ExportOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
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
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [plans, setPlans] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState([])
  const [form] = Form.useForm()

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

  const fetchPlans = useCallback(async () => {
    try {
      const res = await api.get('/plans')
      setPlans(res || [])
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  useEffect(() => {
    if (createModalOpen && plans.length === 0) {
      fetchPlans()
    }
  }, [createModalOpen, plans, fetchPlans])

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)
      await api.post('/tenants', {
        name: values.name,
        slug: values.slug,
        plan_id: values.plan_id,
        merchant_name: values.merchant_name,
        contact_email: values.contact_email || undefined,
        contact_phone: values.contact_phone || undefined,
        trial_days: values.trial_days || 14,
      })
      message.success('租户创建成功')
      form.resetFields()
      setCreateModalOpen(false)
      fetchData()
    } catch (err) {
      if (err.errorFields) return
    } finally {
      setSubmitting(false)
    }
  }

  const handleExport = () => {
    window.open('/api/admin/export/tenants', '_blank')
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
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
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
            <Button icon={<DownloadOutlined />} onClick={handleExport}>
              导出 CSV
            </Button>
          </PermissionGate>
        </div>

        {/* 批量操作栏 */}
        {selectedRowKeys.length > 0 && (
          <div
            style={{
              marginBottom: 12,
              padding: '8px 12px',
              background: '#E8F5EF',
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
              <Button size="small" icon={<ExportOutlined />}>
                批量导出
              </Button>
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

      {/* 新建租户 Modal */}
      <Modal
        title="新建租户"
        open={createModalOpen}
        onCancel={() => {
          setCreateModalOpen(false)
          form.resetFields()
        }}
        onOk={handleCreate}
        confirmLoading={submitting}
        okText="创建"
        cancelText="取消"
        width={520}
      >
        <Form form={form} layout="vertical" initialValues={{ trial_days: 14 }}>
          <Form.Item name="name" label="租户名称" rules={[{ required: true, message: '请输入租户名称' }]}>
            <Input placeholder="如：上海XX市场" />
          </Form.Item>
          <Form.Item name="slug" label="Slug（URL标识）" rules={[{ required: true, message: '请输入 slug' }]}>
            <Input placeholder="如：sh-xx-market" />
          </Form.Item>
          <Form.Item name="plan_id" label="初始套餐" rules={[{ required: true, message: '请选择套餐' }]}>
            <Select
              placeholder="选择套餐"
              options={plans.map((p) => ({
                value: p.id,
                label: `${p.name} (${p.code}) - ¥${p.price_monthly}/月`,
              }))}
            />
          </Form.Item>
          <Form.Item name="merchant_name" label="首个商户名称" rules={[{ required: true, message: '请输入商户名称' }]}>
            <Input placeholder="如：老张菜摊" />
          </Form.Item>
          <Form.Item name="contact_email" label="联系邮箱">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="contact_phone" label="联系电话">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="trial_days" label="试用期天数">
            <InputNumber min={1} max={90} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
