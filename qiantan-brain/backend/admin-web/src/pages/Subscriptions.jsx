import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Button, Space, Select, message, Modal, Descriptions } from 'antd'
import { ReloadOutlined, DownloadOutlined } from '@ant-design/icons'
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
    </div>
  )
}
