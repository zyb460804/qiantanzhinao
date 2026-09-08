import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Col, Input, Progress, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import {
  CameraOutlined,
  DashboardOutlined,
  DisconnectOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
  SearchOutlined,
  WifiOutlined,
} from '@ant-design/icons'
import api from '../api/client'
import EmptyState, { ErrorState } from './EmptyState'

const statusConfig = {
  online: { color: 'green', icon: <WifiOutlined />, label: '在线' },
  offline: { color: 'red', icon: <DisconnectOutlined />, label: '离线' },
  warning: { color: 'orange', icon: <ExclamationCircleOutlined />, label: '异常' },
}

const typeConfig = {
  camera: { color: 'blue', icon: <CameraOutlined />, label: '摄像头' },
  scale: { color: 'purple', icon: <DashboardOutlined />, label: '智能秤' },
  esl: { color: 'cyan', icon: <DashboardOutlined />, label: '电子价签' },
  printer: { color: 'gold', icon: <DashboardOutlined />, label: '打印机' },
}

function formatDateTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

/** 设备监控面板 — 原 /devices 独立页迁入 Monitoring Tab，表格与筛选功能不变。 */
export default function DevicesPanel() {
  const [data, setData] = useState({ items: [], total: 0, online: 0, offline: 0, warning: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState()
  const [typeFilter, setTypeFilter] = useState()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const fetchDevices = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = { page, page_size: pageSize }
      if (search.trim()) params.search = search.trim()
      if (statusFilter) params.status = statusFilter
      if (typeFilter) params.device_type = typeFilter
      const response = await api.get('/devices', { params })
      setData({
        items: response.items || [],
        total: response.total || 0,
        online: response.online || 0,
        offline: response.offline || 0,
        warning: response.warning || 0,
      })
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || '设备数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, search, statusFilter, typeFilter])

  useEffect(() => {
    fetchDevices()
  }, [fetchDevices])

  const onlinePercent = data.total > 0 ? Math.round((data.online / data.total) * 100) : 0

  const columns = [
    {
      title: '设备',
      key: 'device',
      width: 180,
      render: (_, device) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{device.device_name}</Typography.Text>
          <Typography.Text type="secondary">{device.serial_number || '无序列号'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '归属',
      key: 'owner',
      width: 170,
      render: (_, device) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{device.tenant_name || '-'}</Typography.Text>
          <Typography.Text type="secondary">{device.merchant_name || '-'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'device_type',
      key: 'device_type',
      width: 105,
      render: (value) => {
        const config = typeConfig[value] || { color: 'default', label: value || '未知' }
        return (
          <Tag color={config.color} icon={config.icon}>
            {config.label}
          </Tag>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (value) => {
        const config = statusConfig[value] || { color: 'default', label: value || '未知' }
        return (
          <Tag color={config.color} icon={config.icon}>
            {config.label}
          </Tag>
        )
      },
    },
    {
      title: '最后心跳',
      dataIndex: 'last_heartbeat',
      key: 'last_heartbeat',
      width: 180,
      render: formatDateTime,
    },
    {
      title: '固件',
      dataIndex: 'firmware_version',
      key: 'firmware_version',
      width: 100,
      render: (value) => value || '-',
    },
    {
      title: '错误信息',
      dataIndex: 'last_error',
      key: 'last_error',
      ellipsis: true,
      render: (value) =>
        value ? (
          <Typography.Text type="danger" title={value}>
            {value}
          </Typography.Text>
        ) : (
          '-'
        ),
    },
  ]

  if (error) {
    return <ErrorState message={error} onRetry={fetchDevices} />
  }

  return (
    <div>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="设备总数" value={data.total} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic
              title="在线"
              value={data.online}
              valueStyle={{ color: '#00B578' }}
              suffix={<Progress percent={onlinePercent} size="small" style={{ width: 64 }} showInfo={false} />}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="异常" value={data.warning} valueStyle={{ color: '#F08C00' }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="离线" value={data.offline} valueStyle={{ color: '#FA5151' }} />
          </Card>
        </Col>
      </Row>

      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索设备、序列号或租户"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value)
              setPage(1)
            }}
            style={{ width: 260 }}
          />
          <Select
            allowClear
            placeholder="运行状态"
            value={statusFilter}
            onChange={(value) => {
              setStatusFilter(value)
              setPage(1)
            }}
            style={{ width: 130 }}
            options={Object.entries(statusConfig).map(([value, config]) => ({ value, label: config.label }))}
          />
          <Select
            allowClear
            placeholder="设备类型"
            value={typeFilter}
            onChange={(value) => {
              setTypeFilter(value)
              setPage(1)
            }}
            style={{ width: 130 }}
            options={Object.entries(typeConfig).map(([value, config]) => ({ value, label: config.label }))}
          />
          {(search || statusFilter || typeFilter) && (
            <Button
              onClick={() => {
                setSearch('')
                setStatusFilter(undefined)
                setTypeFilter(undefined)
                setPage(1)
              }}
            >
              清除筛选
            </Button>
          )}
          <Button icon={<ReloadOutlined />} loading={loading} onClick={fetchDevices}>
            刷新
          </Button>
        </Space>

        {data.items.length === 0 && !loading ? (
          <EmptyState description={data.total ? '没有符合条件的设备' : '暂无设备数据'} />
        ) : (
          <Table
            dataSource={data.items}
            columns={columns}
            rowKey="id"
            loading={loading}
            scroll={{ x: 1050 }}
            pagination={{
              current: page,
              pageSize,
              total: data.total,
              showSizeChanger: true,
              showQuickJumper: true,
              showTotal: (total) => `共 ${total} 台`,
              onChange: (nextPage, nextPageSize) => {
                setPage(nextPage)
                setPageSize(nextPageSize)
              },
            }}
          />
        )}
      </Card>
    </div>
  )
}
