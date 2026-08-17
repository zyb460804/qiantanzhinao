import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Button, Space, Select, message, Modal, Descriptions, Form } from 'antd'
import { ReloadOutlined, DownloadOutlined, PlusOutlined, SwapOutlined } from '@ant-design/icons'
import api from '../api/client'
import dayjs from 'dayjs'
import PageHeader from '../components/PageHeader'
import ConfirmWithReason from '../components/ConfirmWithReason'
import { PERMISSIONS } from '../permissions'
import PermissionGate from '../permissions/PermissionGate'

const statusColors = { trialing: 'blue', active: 'green', past_due: 'orange', canceled: 'default', expired: 'red' }
const statusLabels = { trialing: '试用中', active: '活跃', past_due: '逾期', canceled: '已取消', expired: '已过期' }

export default function Subscriptions() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState(undefined)
  const [detailVisible, setDetailVisible] = useState(false)
  const [detail, setDetail] = useState(null)
  const [createVisible, setCreateVisible] = useState(false)
  const [upgradeTarget, setUpgradeTarget] = useState(null)
  const [tenants, setTenants] = useState([])
  const [plans, setPlans] = useState([])
  const [createForm] = Form.useForm()
  const [upgradeForm] = Form.useForm()

  const fetchTenants = useCallback(async () => {
    try {
      const res = await api.get('/tenants', { params: { page: 1, page_size: 100 } })
      setTenants(res.items || [])
    } catch {
      /* error handled globally */
    }
  }, [])

  const fetchPlans = useCallback(async () => {
    try {
      const res = await api.get('/plans')
      setPlans(res || [])
    } catch {
      /* error handled globally */
    }
  }, [])

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/subscriptions', { params: { page, page_size: pageSize, status: statusFilter } })
      setData(res)
    } catch {
      // error handled globally
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, statusFilter])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  useEffect(() => {
    fetchTenants()
    fetchPlans()
  }, [fetchTenants, fetchPlans])

  useEffect(() => {
    if (upgradeTarget) {
      upgradeForm.setFieldsValue({
        plan_id: upgradeTarget.plan_id,
        billing_cycle: upgradeTarget.billing_cycle,
        auto_renew: upgradeTarget.auto_renew,
      })
    }
  }, [upgradeTarget, upgradeForm])

  const handleCancel = async (id, reason) => {
    await api.post(`/subscriptions/${id}/cancel`, { reason })
    message.success('订阅已取消')
    fetchData()
  }

  const handleActivate = async (id) => {
    await api.post(`/subscriptions/${id}/activate`)
    message.success('订阅已激活')
    fetchData()
  }

  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields()
      await api.post('/subscriptions', {
        tenant_id: values.tenant_id,
        plan_id: values.plan_id,
        billing_cycle: values.billing_cycle,
        auto_renew: values.auto_renew ?? true,
      })
      message.success('订阅创建成功')
      createForm.resetFields()
      setCreateVisible(false)
      fetchData()
    } catch (err) {
      if (err?.errorFields) return
      /* error handled globally */
    }
  }

  const handleUpgrade = async () => {
    if (!upgradeTarget) return
    try {
      const values = await upgradeForm.validateFields()
      await api.put(`/subscriptions/${upgradeTarget.id}`, {
        plan_id: values.plan_id,
        billing_cycle: values.billing_cycle,
        auto_renew: values.auto_renew,
      })
      message.success('订阅套餐已更新')
      setUpgradeTarget(null)
      upgradeForm.resetFields()
      fetchData()
    } catch (err) {
      if (err?.errorFields) return
      /* error handled globally */
    }
  }

  const showDetail = async (id) => {
    try {
      const res = await api.get(`/subscriptions/${id}`)
      setDetail(res)
      setDetailVisible(true)
    } catch {
      /* error handled globally */
    }
  }

  const columns = [
    { title: '租户', dataIndex: 'tenant_name', key: 'tenant_name', width: 160 },
    { title: '套餐', key: 'plan', width: 160, render: (_, r) => `${r.plan_name} (${r.plan_code})` },
    {
      title: '周期',
      dataIndex: 'billing_cycle',
      key: 'billing_cycle',
      width: 60,
      render: (v) => (v === 'yearly' ? '年付' : '月付'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v) => <Tag color={statusColors[v] || 'default'}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '当前周期',
      key: 'period',
      width: 200,
      render: (_, r) =>
        r.current_period_start
          ? `${dayjs(r.current_period_start).format('YYYY-MM-DD')} ~ ${r.current_period_end ? dayjs(r.current_period_end).format('YYYY-MM-DD') : '-'}`
          : '-',
    },
    {
      title: '自动续费',
      dataIndex: 'auto_renew',
      key: 'auto_renew',
      width: 80,
      render: (v) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, r) => (
        <Space size="small">
          <Button size="small" onClick={() => showDetail(r.id)}>
            详情
          </Button>
          <PermissionGate permission={PERMISSIONS.SUBSCRIPTION_CHANGE}>
            {r.status !== 'active' && r.status !== 'canceled' && (
              <Button size="small" type="primary" onClick={() => handleActivate(r.id)}>
                激活
              </Button>
            )}
            {r.status !== 'canceled' && r.status !== 'expired' && (
              <Button size="small" icon={<SwapOutlined />} onClick={() => setUpgradeTarget(r)}>
                换套餐
              </Button>
            )}
            {r.status !== 'canceled' && r.status !== 'expired' && (
              <ConfirmWithReason
                title="取消订阅"
                description={`取消租户「${r.tenant_name}」的「${r.plan_name}」订阅`}
                impact={`当前周期截止 ${r.current_period_end ? dayjs(r.current_period_end).format('YYYY-MM-DD') : '-'}，周期内仍可使用。`}
                onSubmit={(reason) => handleCancel(r.id, reason)}
              >
                <Button size="small" danger>
                  取消
                </Button>
              </ConfirmWithReason>
            )}
          </PermissionGate>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="订阅管理"
        subtitle={`${data.total || 0} 条订阅记录`}
        extra={
          <Space>
            <PermissionGate permission={PERMISSIONS.SUBSCRIPTION_CREATE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateVisible(true)}>
                新建订阅
              </Button>
            </PermissionGate>
            <Select
              allowClear
              placeholder="状态筛选"
              style={{ width: 120 }}
              value={statusFilter}
              onChange={(v) => {
                setStatusFilter(v)
                setPage(1)
              }}
              options={Object.entries(statusLabels).map(([v, l]) => ({ value: v, label: l }))}
            />
            <Button icon={<ReloadOutlined />} onClick={fetchData}>
              刷新
            </Button>
            <PermissionGate permission={PERMISSIONS.EXPORT_DATA}>
              <Button
                icon={<DownloadOutlined />}
                onClick={() => window.open('/api/admin/export/subscriptions', '_blank')}
              >
                导出
              </Button>
            </PermissionGate>
          </Space>
        }
      />
      <Card style={{ borderRadius: 10 }}>
        <Table
          columns={columns}
          dataSource={data.items}
          rowKey="id"
          loading={loading}
          size="middle"
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize,
            total: data.total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
            },
          }}
        />
      </Card>
      <Modal title="订阅详情" open={detailVisible} onCancel={() => setDetailVisible(false)} footer={null} width={600}>
        {detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="ID">{String(detail.id)}</Descriptions.Item>
            <Descriptions.Item label="租户">{detail.tenant_name}</Descriptions.Item>
            <Descriptions.Item label="套餐">
              {detail.plan_name} ({detail.plan_code})
            </Descriptions.Item>
            <Descriptions.Item label="计费周期">
              {detail.billing_cycle === 'yearly' ? '年付' : '月付'}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColors[detail.status]}>{statusLabels[detail.status]}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="周期开始">
              {detail.current_period_start ? dayjs(detail.current_period_start).format('YYYY-MM-DD HH:mm') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="周期结束">
              {detail.current_period_end ? dayjs(detail.current_period_end).format('YYYY-MM-DD HH:mm') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="自动续费">{detail.auto_renew ? '是' : '否'}</Descriptions.Item>
            <Descriptions.Item label="取消时间">
              {detail.canceled_at ? dayjs(detail.canceled_at).format('YYYY-MM-DD HH:mm') : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      <Modal
        title="新建订阅"
        open={createVisible}
        onCancel={() => {
          setCreateVisible(false)
          createForm.resetFields()
        }}
        onOk={handleCreate}
        okText="创建"
        cancelText="取消"
        width={520}
      >
        <Form form={createForm} layout="vertical" initialValues={{ billing_cycle: 'monthly', auto_renew: true }}>
          <Form.Item name="tenant_id" label="租户" rules={[{ required: true, message: '请选择租户' }]}>
            <Select
              showSearch
              placeholder="选择租户"
              optionFilterProp="label"
              options={tenants.map((t) => ({ value: t.id, label: `${t.name} (${t.slug})` }))}
            />
          </Form.Item>
          <Form.Item name="plan_id" label="套餐" rules={[{ required: true, message: '请选择套餐' }]}>
            <Select
              placeholder="选择套餐"
              options={plans.map((p) => ({
                value: p.id,
                label: `${p.name} (${p.code}) - ¥${p.price_monthly}/月`,
              }))}
            />
          </Form.Item>
          <Form.Item name="billing_cycle" label="计费周期" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'monthly', label: '月付' },
                { value: 'yearly', label: '年付' },
              ]}
            />
          </Form.Item>
          <Form.Item name="auto_renew" label="自动续费" valuePropName="checked">
            <Select
              options={[
                { value: true, label: '开启' },
                { value: false, label: '关闭' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`升级/换套餐 — ${upgradeTarget?.tenant_name || ''}`}
        open={Boolean(upgradeTarget)}
        onCancel={() => {
          setUpgradeTarget(null)
          upgradeForm.resetFields()
        }}
        onOk={handleUpgrade}
        okText="保存"
        cancelText="取消"
        width={520}
      >
        <Form form={upgradeForm} layout="vertical" initialValues={{ billing_cycle: 'monthly', auto_renew: true }}>
          <Form.Item name="plan_id" label="新套餐" rules={[{ required: true, message: '请选择套餐' }]}>
            <Select
              placeholder="选择套餐"
              options={plans.map((p) => ({
                value: p.id,
                label: `${p.name} (${p.code}) - ¥${p.price_monthly}/月`,
              }))}
            />
          </Form.Item>
          <Form.Item name="billing_cycle" label="计费周期" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'monthly', label: '月付' },
                { value: 'yearly', label: '年付' },
              ]}
            />
          </Form.Item>
          <Form.Item name="auto_renew" label="自动续费" rules={[{ required: true }]}>
            <Select
              options={[
                { value: true, label: '开启' },
                { value: false, label: '关闭' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
