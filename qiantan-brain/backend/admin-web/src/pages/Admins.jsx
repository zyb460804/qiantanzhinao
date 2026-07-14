import { useState, useEffect } from 'react'
import { Card, Table, Tag, Button, Modal, Form, Input, Select, Space, message, Switch } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { ROLE_LABELS } from '../permissions'

export default function Admins() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await api.get('/admins')
      setData(res || [])
    } catch {
      /* handled globally */
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
        const payload = {}
        if (values.name) payload.name = values.name
        if (values.role) payload.role = values.role
        if (values.is_active !== undefined) payload.is_active = values.is_active
        if (values.password) payload.password = values.password
        await api.put(`/admins/${editingId}`, payload)
        message.success('更新成功')
      } else {
        await api.post('/admins', values)
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

  const columns = [
    { title: '邮箱', dataIndex: 'email', key: 'email', width: 200 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 120 },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (v) => <Tag color={v === 'super_admin' ? 'red' : 'blue'}>{ROLE_LABELS[v] || v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v) => (v ? <Tag color="green">正常</Tag> : <Tag color="red">已停用</Tag>),
    },
    {
      title: '最近登录',
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      width: 160,
      render: (v) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    { title: '操作', key: 'action', width: 80, render: (_, record) => <a onClick={() => onEdit(record)}>编辑</a> },
  ]

  return (
    <div>
      <PageHeader
        title="管理员管理"
        subtitle="管理平台管理员账号、角色和状态"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>
            新建管理员
          </Button>
        }
      />
      <Card style={{ borderRadius: 10 }}>
        <Space style={{ marginBottom: 16 }}>
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
        </Space>
        {data.length === 0 && !loading ? (
          <EmptyState description="暂无管理员" />
        ) : (
          <Table dataSource={data} columns={columns} rowKey="id" loading={loading} size="middle" pagination={false} />
        )}
      </Card>
      <Modal
        title={editingId ? '编辑管理员' : '新建管理员'}
        open={modalOpen}
        onOk={onSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={500}
      >
        <Form form={form} layout="vertical" initialValues={{ role: 'ops_admin' }}>
          <Form.Item name="email" label="邮箱" rules={[{ required: !editingId, type: 'email' }]}>
            <Input disabled={!!editingId} placeholder="admin@example.com" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: !editingId }]}>
            <Input placeholder="张三" />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Select options={Object.entries(ROLE_LABELS).map(([v, l]) => ({ value: v, label: l }))} />
          </Form.Item>
          {editingId && (
            <Form.Item name="is_active" label="账号状态" valuePropName="checked">
              <Switch checkedChildren="正常" unCheckedChildren="停用" />
            </Form.Item>
          )}
          <Form.Item
            name="password"
            label={editingId ? '新密码（留空不修改）' : '密码'}
            rules={editingId ? [] : [{ required: true, min: 8 }]}
          >
            <Input.Password placeholder="至少8位" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
