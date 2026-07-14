import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Tag, Select, Space, Row, Col, Statistic, Button } from 'antd'
import { ReloadOutlined, BulbOutlined, CheckCircleOutlined, ClockCircleOutlined } from '@ant-design/icons'
import EmptyState from '../components/EmptyState'
import PageHeader from '../components/PageHeader'
import api from '../api/client'

const actionTypes = {
  restock_suggest: { label: '补货建议', color: 'blue', icon: <BulbOutlined /> },
  price_adjust: { label: '调价建议', color: 'orange', icon: <BulbOutlined /> },
  waste_alert: { label: '损耗预警', color: 'red', icon: <BulbOutlined /> },
  promotion: { label: '促销建议', color: 'purple', icon: <BulbOutlined /> },
  auto_reorder: { label: '自动补货', color: 'green', icon: <CheckCircleOutlined /> },
  daily_report: { label: '日报推送', color: 'cyan', icon: <CheckCircleOutlined /> },
}

export default function AiOps() {
  const [data, setData] = useState([])
  const [_stats, setStats] = useState(null)
  const [filterType, setFilterType] = useState(undefined)
  const [_loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [actionsRes, statsRes] = await Promise.all([
        api.get('/aiops/actions?page_size=100').catch(() => ({ items: [], total: 0 })),
        api.get('/aiops/stats').catch(() => null),
      ])
      setData(actionsRes.items || [])
      setStats(statsRes)
    } catch {
      // keep empty state
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const filtered = filterType ? data.filter((d) => d.action_type === filterType) : data
  const executedCount = data.filter((d) => d.executed).length
  const successCount = data.filter((d) => d.result === 'success').length

  const columns = [
    { title: '租户', dataIndex: 'tenant_name', key: 'tenant_name', width: 120 },
    {
      title: '类型',
      dataIndex: 'action_type',
      key: 'action_type',
      width: 100,
      render: (v) => {
        const t = actionTypes[v] || { label: v, color: 'default' }
        return (
          <Tag color={t.color} icon={t.icon}>
            {t.label}
          </Tag>
        )
      },
    },
    { title: '内容', dataIndex: 'title', key: 'title', width: 240 },
    { title: '详情', dataIndex: 'detail', key: 'detail', ellipsis: true },
    {
      title: '执行状态',
      key: 'executed',
      width: 100,
      render: (_, r) =>
        r.executed ? (
          <Tag color="green" icon={<CheckCircleOutlined />}>
            已执行
          </Tag>
        ) : (
          <Tag color="default" icon={<ClockCircleOutlined />}>
            待处理
          </Tag>
        ),
    },
    {
      title: '结果',
      dataIndex: 'result',
      key: 'result',
      width: 80,
      render: (v) =>
        v === 'success' ? (
          <Tag color="green">成功</Tag>
        ) : v === 'failed' ? (
          <Tag color="red">失败</Tag>
        ) : (
          <Tag>待定</Tag>
        ),
    },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 150 },
  ]

  return (
    <div>
      <PageHeader title="AI 运营中心" subtitle="AI 自动生成的经营建议与自动执行的动作记录" />

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={8}>
          <Card style={{ borderRadius: 10 }}>
            <Statistic title="AI 动作总数" value={data.length} prefix={<BulbOutlined style={{ color: '#167A5A' }} />} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card style={{ borderRadius: 10 }}>
            <Statistic
              title="已自动执行"
              value={executedCount}
              prefix={<CheckCircleOutlined style={{ color: '#16803C' }} />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card style={{ borderRadius: 10 }}>
            <Statistic
              title="成功率"
              value={executedCount > 0 ? Math.round((successCount / executedCount) * 100) : 100}
              suffix="%"
              prefix={<CheckCircleOutlined style={{ color: '#2563EB' }} />}
            />
          </Card>
        </Col>
      </Row>

      <Card style={{ borderRadius: 10 }}>
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            allowClear
            placeholder="动作类型"
            style={{ width: 140 }}
            value={filterType}
            onChange={setFilterType}
            options={Object.entries(actionTypes).map(([v, t]) => ({ value: v, label: t.label }))}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
        </Space>
        {filtered.length === 0 ? (
          <EmptyState description="暂无 AI 运营记录" />
        ) : (
          <Table
            dataSource={filtered}
            columns={columns}
            rowKey="id"
            size="middle"
            pagination={{ showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
            scroll={{ x: 900 }}
          />
        )}
      </Card>
    </div>
  )
}
