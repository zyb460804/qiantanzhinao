import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Select, Space, Button, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
import { TableSkeleton } from '../components/SkeletonCard'
import EmptyState from '../components/EmptyState'
import { ErrorState } from '../components/EmptyState'

const actionColors = {
  login: 'blue',
  logout: 'default',
  create: 'green',
  update: 'orange',
  delete: 'red',
  cancel: 'red',
  activate: 'green',
  mark_paid: 'purple',
  manual_record_usage: 'red',
  export: 'gold',
  generate_invoice: 'cyan',
}
const actionLabels = {
  login: '登录',
  logout: '登出',
  create: '创建',
  update: '更新',
  delete: '删除',
  cancel: '取消',
  activate: '激活',
  mark_paid: '标记支付',
  manual_record_usage: '人工记账',
  export: '导出',
  generate_invoice: '生成账单',
}

export default function AuditLog() {
  const [data, setData] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [actionFilter, setActionFilter] = useState(undefined)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { page, page_size: pageSize }
      if (actionFilter) params.action = actionFilter
      const res = await api.get('/audit-logs', { params })
      setData(res.items || [])
      setTotal(res.total || 0)
    } catch (err) {
      setError(err?.response?.data?.detail || '加载审计日志失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, actionFilter])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const columns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作人',
      dataIndex: 'admin_email',
      key: 'admin_email',
      width: 180,
    },
    {
      title: '操作',
      dataIndex: 'action',
      key: 'action',
      width: 110,
      render: (a) => <Tag color={actionColors[a] || 'default'}>{actionLabels[a] || a}</Tag>,
    },
    {
      title: '资源类型',
      dataIndex: 'resource_type',
      key: 'resource_type',
      width: 100,
      render: (v) => v || '-',
    },
    {
      title: '资源 ID',
      dataIndex: 'resource_id',
      key: 'resource_id',
      width: 320,
      ellipsis: true,
      render: (v) =>
        v ? (
          <Typography.Text code style={{ fontSize: 11 }}>
            {v}
          </Typography.Text>
        ) : (
          '-'
        ),
    },
    {
      title: '详情',
      dataIndex: 'detail',
      key: 'detail',
      width: 200,
      ellipsis: true,
      render: (v) => {
        if (!v) return '-'
        try {
          const parsed = typeof v === 'string' ? JSON.parse(v) : v
          return <Typography.Text style={{ fontSize: 12 }}>{JSON.stringify(parsed)}</Typography.Text>
        } catch {
          return <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text>
        }
      },
    },
    {
      title: 'IP',
      dataIndex: 'ip_address',
      key: 'ip_address',
      width: 130,
      render: (v) => v || '-',
    },
  ]

  return (
    <div>
      <PageHeader title="审计日志" subtitle="记录所有管理员的关键操作，不可删除或修改" />

      <Card style={{ borderRadius: 10 }}>
        <Space style={{ marginBottom: 16 }}>
          <Select
            placeholder="操作类型筛选"
            style={{ width: 160 }}
            allowClear
            value={actionFilter}
            onChange={(v) => {
              setActionFilter(v)
              setPage(1)
            }}
            options={Object.entries(actionLabels).map(([v, l]) => ({ value: v, label: l }))}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
        </Space>

        {loading ? (
          <TableSkeleton rows={8} />
        ) : error ? (
          <ErrorState message={error} onRetry={fetchData} />
        ) : data.length === 0 ? (
          <EmptyState description="暂无审计日志" />
        ) : (
          <Table
            dataSource={data}
            columns={columns}
            rowKey="id"
            scroll={{ x: 1300 }}
            size="middle"
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
              },
            }}
          />
        )}
      </Card>
    </div>
  )
}
