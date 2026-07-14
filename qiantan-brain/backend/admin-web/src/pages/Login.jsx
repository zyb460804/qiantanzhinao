import React from 'react'
import { Card, Form, Input, Button, Typography, message } from 'antd'
import { LockOutlined, MailOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [loading, setLoading] = React.useState(false)

  const onFinish = async (values) => {
    setLoading(true)
    try {
      await login(values.email, values.password)
      message.success('登录成功')
      navigate('/dashboard')
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || '登录失败'
      message.error(typeof detail === 'string' ? detail : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* 装饰圆 */}
      <div
        style={{
          position: 'absolute',
          width: 600,
          height: 600,
          borderRadius: '50%',
          background: 'rgba(255,255,255,0.05)',
          top: '-200px',
          right: '-100px',
        }}
      />
      <div
        style={{
          position: 'absolute',
          width: 400,
          height: 400,
          borderRadius: '50%',
          background: 'rgba(255,255,255,0.05)',
          bottom: '-150px',
          left: '-100px',
        }}
      />

      <Card
        style={{
          width: 420,
          borderRadius: 16,
          boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
          border: 'none',
        }}
        styles={{ body: { padding: '40px 36px' } }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 56,
              height: 56,
              borderRadius: 14,
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              marginBottom: 16,
              fontSize: 28,
            }}
          >
            <span style={{ filter: 'brightness(0) invert(1)' }}>🧠</span>
          </div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            千摊智脑
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 14 }}>
            SaaS 管理后台
          </Typography.Text>
        </div>

        <Form name="login" onFinish={onFinish} autoComplete="off" size="large">
          <Form.Item
            name="email"
            rules={[
              { required: true, message: '请输入邮箱' },
              { type: 'email', message: '邮箱格式不正确' },
            ]}
          >
            <Input prefix={<MailOutlined style={{ color: '#bfbfbf' }} />} placeholder="管理员邮箱" />
          </Form.Item>

          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined style={{ color: '#bfbfbf' }} />} placeholder="密码" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 12 }}>
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={loading}
              style={{
                height: 44,
                borderRadius: 8,
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                border: 'none',
                fontWeight: 500,
                fontSize: 16,
              }}
            >
              登 录
            </Button>
          </Form.Item>

          <div style={{ textAlign: 'center' }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              默认账号 admin@qiantan.com / Admin123!
            </Typography.Text>
          </div>
        </Form>
      </Card>
    </div>
  )
}
