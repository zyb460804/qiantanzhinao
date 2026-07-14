import { useCallback, useEffect, useMemo, useState } from 'react'
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
import PageHeader from '../components/PageHeader'
import EmptyState, { ErrorState } from '../components/EmptyState'

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

export default function Devices() {
  const [data, setData] = useState({ items: [], total: 0, online: 0, offline: 0, warning: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState()
  const [typeFilter, setTypeFilter] = useState()

  const fetchDevices = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.get('/devices?page_size=100')
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
  }, [])

  useEffect(() => {
    fetchDevices()
  }, [fetchDevices])

  const filteredDevices = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return data.items.filter((device) => {
      if (statusFilter && device.status !== statusFilter) return false
      if (typeFilter && device.device_type !== typeFilter) return false
      if (!keyword) return true
      return [
        device.device_name,
        device.serial_number,
        device.tenant_name,
        device.merchant_name,
        device.firmware_version,
      ].some((value) => value?.toLowerCase().includes(keyword))
    })
  }, [data.items, search, statusFilter, typeFilter])

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
    return (
      <div>
        <PageHeader title="设备监控" subtitle="设备心跳与运行状态" />
        <ErrorState message={error} onRetry={fetchDevices} />
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="设备监控"
        subtitle={`${data.total} 台已注册设备`}
        extra={
          <Button icon={<ReloadOutlined />} loading={loading} onClick={fetchDevices}>
            刷新
          </Button>
        }
      />

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
              valueStyle={{ color: '#16803C' }}
              suffix={<Progress percent={onlinePercent} size="small" style={{ width: 64 }} showInfo={false} />}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="异常" value={data.warning} valueStyle={{ color: '#D97706' }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="离线" value={data.offline} valueStyle={{ color: '#DC2626' }} />
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
            onChange={(event) => setSearch(event.target.value)}
            style={{ width: 260 }}
          />
          <Select
            allowClear
            placeholder="运行状态"
            value={statusFilter}
            onChange={setStatusFilter}
            style={{ width: 130 }}
            options={Object.entries(statusConfig).map(([value, config]) => ({ value, label: config.label }))}
          />
          <Select
            allowClear
            placeholder="设备类型"
            value={typeFilter}
            onChange={setTypeFilter}
            style={{ width: 130 }}
            options={Object.entries(typeConfig).map(([value, config]) => ({ value, label: config.label }))}
          />
          {(search || statusFilter || typeFilter) && (
            <Button
              onClick={() => {
                setSearch('')
                setStatusFilter(undefined)
                setTypeFilter(undefined)
              }}
            >
              清除筛选
            </Button>
          )}
        </Space>

        {filteredDevices.length === 0 && !loading ? (
          <EmptyState description={data.total ? '没有符合条件的设备' : '暂无设备数据'} />
        ) : (
          <Table
            dataSource={filteredDevices}
            columns={columns}
            rowKey="id"
            loading={loading}
            scroll={{ x: 1050 }}
            pagination={{
              pageSize: 20,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 台`,
            }}
          />
        )}
      </Card>
    </div>
  )
}
