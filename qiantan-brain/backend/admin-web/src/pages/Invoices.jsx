import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Button, Space, Select, Modal, Descriptions, message, Form, Input, InputNumber } from 'antd'
import { ReloadOutlined, DownloadOutlined, DollarOutlined, PlusOutlined, FileAddOutlined, StopOutlined } from '@ant-design/icons'
import api from '../api/client'
import dayjs from 'dayjs'
import PageHeader from '../components/PageHeader'
import ConfirmWithReason from '../components/ConfirmWithReason'
import { PERMISSIONS } from '../permissions'
import PermissionGate from '../permissions/PermissionGate'

const statusColors = { draft: 'default', sent: 'blue', paid: 'green', overdue: 'orange', void: 'red' }
const statusLabels = { draft: '草稿', sent: '已发送', paid: '已支付', overdue: '逾期', void: '已作废' }
const payMethods = { wechat_pay: '微信支付', alipay: '支付宝', bank_transfer: '银行转账', manual: '线下/手动' }

export default function Invoices() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState(undefined)
  const [detailVisible, setDetailVisible] = useState(false)
  const [detail, setDetail] = useState(null)
  const [createVisible, setCreateVisible] = useState(false)
  const [generateVisible, setGenerateVisible] = useState(false)
  const [tenants, setTenants] = useState([])
  const [subscriptions, setSubscriptions] = useState([])
  const [createForm] = Form.useForm()
  const [generateForm] = Form.useForm()

  const fetchOptions = useCallback(async () => {
    try {
      const [tenantRes, subRes] = await Promise.all([
        api.get('/tenants', { params: { page: 1, page_size: 100 } }),
        api.get('/subscriptions', { params: { page: 1, page_size: 100 } }),
      ])
      setTenants(tenantRes.items || [])
      setSubscriptions(subRes.items || [])
    } catch {
      /* error handled globally */
    }
  }, [])

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/invoices', { params: { page, page_size: pageSize, status: statusFilter } })
      setData(res)
    } catch {
      /* error handled globally */
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, statusFilter])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  useEffect(() => {
    fetchOptions()
  }, [fetchOptions])

  const showDetail = async (id) => {
    try {
      const res = await api.get(`/invoices/${id}`)
      setDetail(res)
      setDetailVisible(true)
    } catch {
      /* error handled globally */
    }
  }

  const handleMarkPaid = async (id, reason) => {
    try {
      await api.post(`/invoices/${id}/mark-paid`, { payment_method: 'manual', reason })
      message.success('已标记为已支付')
      fetchData()
    } catch {
      /* error handled globally */
    }
  }

  const handleVoid = async (id, _reason) => {
    try {
      await api.put(`/invoices/${id}`, { status: 'void' })
      message.success('发票已作废')
      fetchData()
    } catch {
      /* error handled globally */
    }
  }

  const handleCreate = async (_reason) => {
    try {
      const values = await createForm.validateFields()
      await api.post('/invoices', {
        tenant_id: values.tenant_id,
        subscription_id: values.subscription_id || undefined,
        amount: values.amount,
        currency: 'CNY',
        due_days: values.due_days || 30,
        notes: values.notes || undefined,
      })
      message.success('发票已生成')
      createForm.resetFields()
      setCreateVisible(false)
      fetchData()
    } catch (err) {
      if (err?.errorFields) return
      /* error handled globally */
    }
  }

  const handleGenerateFromSubscription = async (_reason) => {
    try {
      const values = await generateForm.validateFields()
      const res = await api.post(`/invoices/generate-from-subscription/${values.subscription_id}`)
      message.success(res?.message || '续期发票已生成')
      generateForm.resetFields()
      setGenerateVisible(false)
      fetchData()
    } catch (err) {
      if (err?.errorFields) return
      /* error handled globally */
    }
  }

  const columns = [
    { title: '发票号', dataIndex: 'invoice_no', key: 'invoice_no', width: 160 },
    { title: '租户', dataIndex: 'tenant_name', key: 'tenant_name', width: 140 },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 100,
      align: 'right',
      render: (v, r) => `${r.currency === 'CNY' ? '¥' : ''}${Number(v).toFixed(2)}`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v) => <Tag color={statusColors[v] || 'default'}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '到期日',
      dataIndex: 'due_date',
      key: 'due_date',
      width: 110,
      render: (v) => (v ? dayjs(v).format('YYYY-MM-DD') : '-'),
    },
    {
      title: '支付时间',
      dataIndex: 'paid_at',
      key: 'paid_at',
      width: 150,
      render: (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '-'),
    },
    {
      title: '支付方式',
      dataIndex: 'payment_method',
      key: 'payment_method',
      width: 100,
      render: (v) => (v ? payMethods[v] || v : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_, r) => (
        <Space size="small">
          <Button size="small" onClick={() => showDetail(r.id)}>
            详情
          </Button>
          <PermissionGate permission={PERMISSIONS.INVOICE_MARK_PAID}>
            {r.status !== 'paid' && r.status !== 'void' && (
              <ConfirmWithReason
                title="标记发票已支付"
                description={`将发票 ${r.invoice_no}（¥${Number(r.amount).toFixed(2)}）标记为已支付`}
                impact="立即生效，不可撤销。将记录到审计日志。"
                onSubmit={(reason) => handleMarkPaid(r.id, reason)}
              >
                <Button size="small" type="primary" danger icon={<DollarOutlined />}>
                  标记已付
                </Button>
              </ConfirmWithReason>
            )}
          </PermissionGate>
          <PermissionGate permission={PERMISSIONS.INVOICE_UPDATE}>
            {r.status !== 'void' && (
              <ConfirmWithReason
                title="作废发票"
                description={`将发票 ${r.invoice_no} 作废`}
                impact="作废后不可恢复，将记录到审计日志。"
                onSubmit={(reason) => handleVoid(r.id, reason)}
              >
                <Button size="small" danger icon={<StopOutlined />}>
                  作废
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
        title="发票管理"
        subtitle={`${data.total || 0} 张发票`}
        extra={
          <Space>
            <PermissionGate permission={PERMISSIONS.INVOICE_CREATE}>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateVisible(true)}>
                手工开票
              </Button>
              <Button icon={<FileAddOutlined />} onClick={() => setGenerateVisible(true)}>
                从订阅生成续期票
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
              <Button icon={<DownloadOutlined />} onClick={() => window.open('/api/admin/export/invoices', '_blank')}>
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
          scroll={{ x: 1050 }}
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
      <Modal title="发票详情" open={detailVisible} onCancel={() => setDetailVisible(false)} footer={null} width={600}>
        {detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="发票号">{detail.invoice_no}</Descriptions.Item>
            <Descriptions.Item label="租户">{detail.tenant_name}</Descriptions.Item>
            <Descriptions.Item label="金额">
              {detail.currency === 'CNY' ? '¥' : ''}
              {Number(detail.amount).toFixed(2)}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColors[detail.status]}>{statusLabels[detail.status]}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="计费周期">
              {detail.period_start ? dayjs(detail.period_start).format('YYYY-MM-DD') : '-'}
              {' ~ '}
              {detail.period_end ? dayjs(detail.period_end).format('YYYY-MM-DD') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="到期日">
              {detail.due_date ? dayjs(detail.due_date).format('YYYY-MM-DD') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="支付时间">
              {detail.paid_at ? dayjs(detail.paid_at).format('YYYY-MM-DD HH:mm') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="支付方式">
              {detail.payment_method ? payMethods[detail.payment_method] || detail.payment_method : '-'}
            </Descriptions.Item>
            {detail.line_items && (
              <Descriptions.Item label="明细">{JSON.stringify(detail.line_items)}</Descriptions.Item>
            )}
            {detail.notes && <Descriptions.Item label="备注">{detail.notes}</Descriptions.Item>}
          </Descriptions>
        )}
      </Modal>

      <Modal
        title="手工开票"
        open={createVisible}
        onCancel={() => {
          setCreateVisible(false)
          createForm.resetFields()
        }}
        footer={
          <Space>
            <Button
              onClick={() => {
                setCreateVisible(false)
                createForm.resetFields()
              }}
            >
              取消
            </Button>
            <ConfirmWithReason
              title="生成手工发票"
              description="将按表单内容创建一张新的发票"
              impact="创建后状态为草稿，可后续发送/标记已付；操作会记录审计日志。"
              onSubmit={handleCreate}
            >
              <Button
                type="primary"
                onClick={(event) => {
                  createForm.validateFields().catch(() => event.stopPropagation())
                }}
              >
                生成发票
              </Button>
            </ConfirmWithReason>
          </Space>
        }
        width={560}
      >
        <Form form={createForm} layout="vertical" initialValues={{ currency: 'CNY', due_days: 30 }}>
          <Form.Item name="tenant_id" label="租户" rules={[{ required: true, message: '请选择租户' }]}>
            <Select
              showSearch
              placeholder="选择租户"
              optionFilterProp="label"
              options={tenants.map((t) => ({ value: t.id, label: `${t.name} (${t.slug})` }))}
            />
          </Form.Item>
          <Form.Item name="subscription_id" label="关联订阅（可选）">
            <Select
              allowClear
              showSearch
              placeholder="选择订阅"
              optionFilterProp="label"
              options={subscriptions.map((s) => ({
                value: s.id,
                label: `${s.tenant_name} - ${s.plan_name} (${s.billing_cycle === 'yearly' ? '年付' : '月付'})`,
              }))}
            />
          </Form.Item>
          <Form.Item name="amount" label="金额" rules={[{ required: true, message: '请输入金额' }]}>
            <InputNumber min={0.01} precision={2} style={{ width: '100%' }} placeholder="0.00" />
          </Form.Item>
          <Form.Item name="due_days" label="账期（天）" rules={[{ required: true }]}>
            <InputNumber min={0} max={365} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={3} maxLength={2000} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="从订阅生成续期票"
        open={generateVisible}
        onCancel={() => {
          setGenerateVisible(false)
          generateForm.resetFields()
        }}
        footer={
          <Space>
            <Button
              onClick={() => {
                setGenerateVisible(false)
                generateForm.resetFields()
              }}
            >
              取消
            </Button>
            <ConfirmWithReason
              title="生成续期发票"
              description="根据所选订阅的当前周期生成下一计费周期发票"
              impact="可能生成新发票；若该周期已有发票则幂等返回已有发票。"
              onSubmit={handleGenerateFromSubscription}
            >
              <Button
                type="primary"
                onClick={(event) => {
                  generateForm.validateFields().catch(() => event.stopPropagation())
                }}
              >
                生成续期票
              </Button>
            </ConfirmWithReason>
          </Space>
        }
        width={520}
      >
        <Form form={generateForm} layout="vertical">
          <Form.Item name="subscription_id" label="订阅" rules={[{ required: true, message: '请选择订阅' }]}>
            <Select
              showSearch
              placeholder="选择订阅"
              optionFilterProp="label"
              options={subscriptions.map((s) => ({
                value: s.id,
                label: `${s.tenant_name} - ${s.plan_name} (${s.status})`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
