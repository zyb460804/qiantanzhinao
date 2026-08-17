import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Select, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined, RedoOutlined, CheckOutlined } from '@ant-design/icons'
import api from '../api/client'
import EmptyState from './EmptyState'

const statusColors = { pending: 'orange', retrying: 'blue', resolved: 'green', failed: 'red' }
const statusLabels = { pending: '待处理', retrying: '重试中', resolved: '已解决', failed: '失败' }

function formatDateTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

/** 死信队列面板 — 原 /dead-letters 独立页迁入 Monitoring Tab。「重试」按钮触发服务端真实重放。 */
export default function DeadLettersPanel() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [retryingId, setRetryingId] = useState(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState(undefined)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: pageSize }
      if (statusFilter) params.status = statusFilter
      const res = await api.get('/dead-letters', { params })
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

  const handleRetry = async (id) => {
    setRetryingId(id)
    try {
      const result = await api.post(`/dead-letters/${id}/retry`)
      if (result.status === 'resolved') message.success(result.message || '重放成功，事件已解决')
      else if (result.status === 'failed') message.error(result.message || '重放失败，已达最大重试次数')
      else message.warning(result.message || '重放失败，已安排下次重试')
      fetchData()
    } catch {
      /* error handled globally */
    } finally {
      setRetryingId(null)
    }
  }

  const handleResolve = async (id) => {
    try {
      await api.post(`/dead-letters/${id}/resolve`)
      message.success('已标记为已解决')
      fetchData()
    } catch {
      /* error handled globally */
    }
  }

  const columns = [
    { title: '事件类型', dataIndex: 'event_type', key: 'event_type', width: 140 },
    {
      title: '商户',
      dataIndex: 'merchant_name',
      key: 'merchant_name',
      width: 140,
      render: (v) => v || '-',
    },
    { title: '幂等键', dataIndex: 'idempotency_key', key: 'idempotency_key', ellipsis: true, width: 200 },
    {
      title: '错误信息',
      dataIndex: 'error_message',
      key: 'error_message',
      ellipsis: true,
      render: (v) => (
        <Typography.Text type="danger" title={v}>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v) => <Tag color={statusColors[v] || 'default'}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '重试',
      key: 'retry',
      width: 80,
      render: (_, r) => `${r.retry_count}/${r.max_retries}`,
    },
    { title: '下次重试', dataIndex: 'next_retry_at', key: 'next_retry_at', width: 160, render: formatDateTime },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160, render: formatDateTime },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, r) => (
        <Space size="small">
          {(r.status === 'pending' || r.status === 'retrying') && (
            <Button
              size="small"
              icon={<RedoOutlined />}
              loading={retryingId === r.id}
              onClick={() => handleRetry(r.id)}
            >
              重试
            </Button>
          )}
          {r.status !== 'resolved' && (
            <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => handleResolve(r.id)}>
              标记解决
            </Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Card style={{ borderRadius: 10 }}>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="状态筛选"
          style={{ width: 130 }}
          value={statusFilter}
          onChange={(value) => {
            setStatusFilter(value)
            setPage(1)
          }}
          options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))}
        />
        <Button icon={<ReloadOutlined />} onClick={fetchData}>
          刷新
        </Button>
        <Typography.Text type="secondary">重试 = 立即按原事件重放一次并返回真实结果</Typography.Text>
      </Space>
      {data.items.length === 0 && !loading ? (
        <EmptyState description="暂无死信事件" />
      ) : (
        <Table
          dataSource={data.items}
          columns={columns}
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
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPage)
              setPageSize(nextPageSize)
            },
          }}
        />
      )}
    </Card>
  )
}
