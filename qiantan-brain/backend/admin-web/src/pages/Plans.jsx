import { useEffect, useState } from 'react'
import { Table, Card, Button, Modal, Form, Input, InputNumber, Switch, Tag, Space, message, Popconfirm } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { PERMISSIONS } from '../permissions'
import PermissionGate from '../permissions/PermissionGate'

export default function Plans() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)

  const fetchData = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get('/plans')
      setData(res || [])
    } catch (err) {
      setError(err?.response?.data?.detail || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const onAdd = () => {
    setEditingId(null)
    form.resetFields()
    form.setFieldsValue({
      price_monthly: 0,
      price_yearly: 0,
      max_merchants: 1,
      max_api_calls_monthly: 1000,
      max_storage_mb: 100,
      is_public: true,
      is_active: true,
      sort_order: 0,
    })
    setModalOpen(true)
  }

  const onEdit = (record) => {
    setEditingId(record.id)
    form.setFieldsValue(record)
    setModalOpen(true)
  }

  const onSave = async () => {
    setSaving(true)
    try {
      const values = await form.validateFields()
      if (editingId) {
        await api.put(`/plans/${editingId}`, values)
        message.success('更新成功')
      } else {
        await api.post('/plans', values)
        message.success('创建成功')
      }
      setModalOpen(false)
      fetchData()
    } catch (err) {
      if (err.errorFields) return
    } finally {
      setSaving(false)
    }
  }

  const onDelete = async (id) => {
    try {
      await api.delete(`/plans/${id}`)
      message.success('已停用')
      fetchData()
    } catch {
      // error handled globally
    }
  }

  const columns = [
    { title: '代码', dataIndex: 'code', key: 'code', width: 100 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 120 },
    {
      title: '月费',
      dataIndex: 'price_monthly',
      key: 'price_monthly',
      width: 90,
      align: 'right',
      render: (v) => `¥${v}`,
    },
    {
      title: '年费',
      dataIndex: 'price_yearly',
      key: 'price_yearly',
      width: 90,
      align: 'right',
      render: (v) => `¥${v}`,
    },
    { title: '商户', dataIndex: 'max_merchants', key: 'max_merchants', width: 70, align: 'center' },
    {
      title: 'API/月',
      dataIndex: 'max_api_calls_monthly',
      key: 'max_api_calls_monthly',
      width: 90,
      align: 'right',
      render: (v) => v?.toLocaleString(),
    },
    { title: '存储MB', dataIndex: 'max_storage_mb', key: 'max_storage_mb', width: 90, align: 'right' },
    {
      title: '公开',
      dataIndex: 'is_public',
      key: 'is_public',
      width: 70,
      render: (v) => (v ? <Tag color="blue">是</Tag> : <Tag>否</Tag>),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v) => (v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>),
    },
    { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 60, align: 'right' },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, record) => (
        <Space size="small">
          <PermissionGate permission={PERMISSIONS.PLAN_UPDATE}>
            <a onClick={() => onEdit(record)}>编辑</a>
          </PermissionGate>
          <PermissionGate permission={PERMISSIONS.PLAN_DELETE}>
            <Popconfirm title="确认停用此套餐？" onConfirm={() => onDelete(record.id)}>
              <a style={{ color: '#DC2626' }}>停用</a>
            </Popconfirm>
          </PermissionGate>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="套餐管理"
        subtitle={`${data.length} 个套餐，管理定价和功能配置`}
        extra={
          <PermissionGate permission={PERMISSIONS.PLAN_CREATE}>
            <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>
              新增套餐
            </Button>
          </PermissionGate>
        }
      />
      <Card style={{ borderRadius: 10 }}>
        {error ? (
          <EmptyState description={error} action={<Button onClick={fetchData}>重试</Button>} />
        ) : data.length === 0 && !loading ? (
          <EmptyState
            description="暂无套餐"
            action={
              <PermissionGate permission={PERMISSIONS.PLAN_CREATE}>
                <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>
                  新增套餐
                </Button>
              </PermissionGate>
            }
          />
        ) : (
          <Table
            dataSource={data}
            columns={columns}
            rowKey="id"
            loading={loading}
            pagination={false}
            scroll={{ x: 1000 }}
            size="middle"
          />
        )}
      </Card>

      <Modal
        title={editingId ? '编辑套餐' : '新增套餐'}
        open={modalOpen}
        onOk={onSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="code" label="套餐代码" rules={[{ required: true }]}>
            <Input disabled={!!editingId} placeholder="free / pro / enterprise" />
          </Form.Item>
          <Form.Item name="name" label="套餐名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="price_monthly" label="月费(¥)" style={{ width: '50%', paddingRight: 8 }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="price_yearly" label="年费(¥)" style={{ width: '50%' }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="max_merchants" label="商户上限" style={{ width: '33%', paddingRight: 8 }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="max_api_calls_monthly" label="API月调上限" style={{ width: '33%', paddingRight: 8 }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="max_storage_mb" label="存储上限(MB)" style={{ width: '34%' }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="is_public" label="公开" valuePropName="checked" style={{ width: '33%', paddingRight: 8 }}>
              <Switch />
            </Form.Item>
            <Form.Item name="is_active" label="启用" valuePropName="checked" style={{ width: '33%', paddingRight: 8 }}>
              <Switch />
            </Form.Item>
            <Form.Item name="sort_order" label="排序" style={{ width: '34%' }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
        </Form>
      </Modal>
    </div>
  )
}
