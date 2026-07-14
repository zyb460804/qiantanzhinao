import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Result,
  Row,
  Select,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  ShopOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'

const stepItems = [
  { title: '组织信息' },
  { title: '套餐试用' },
  { title: '首个商户' },
  { title: '确认开通' },
]

const stepFields = [
  ['name', 'slug', 'contact_email', 'contact_phone'],
  ['plan_id', 'trial_days'],
  ['merchant_name', 'admin_notes'],
]

function normalizeSlug(value) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
}

function formatPrice(plan) {
  const monthly = Number(plan?.price_monthly || 0)
  return monthly > 0 ? `¥${monthly.toFixed(2)}/月` : '免费'
}

export default function Onboarding() {
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [current, setCurrent] = useState(0)
  const [plans, setPlans] = useState([])
  const [loadingPlans, setLoadingPlans] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)
  const [slugEdited, setSlugEdited] = useState(false)
  const values = Form.useWatch([], form) || {}

  useEffect(() => {
    let active = true
    api
      .get('/plans')
      .then((data) => {
        if (active) setPlans((data || []).filter((plan) => plan.is_active))
      })
      .catch(() => {
        if (active) setPlans([])
      })
      .finally(() => {
        if (active) setLoadingPlans(false)
      })
    return () => {
      active = false
    }
  }, [])

  const selectedPlan = useMemo(
    () => plans.find((plan) => plan.id === values.plan_id),
    [plans, values.plan_id],
  )

  const next = async () => {
    try {
      await form.validateFields(stepFields[current])
      setCurrent((value) => Math.min(value + 1, stepItems.length - 1))
    } catch {
      // Ant Design displays the field-level validation messages.
    }
  }

  const submit = async () => {
    try {
      const payload = await form.validateFields()
      setSubmitting(true)
      const data = await api.post('/tenants', {
        ...payload,
        slug: normalizeSlug(payload.slug),
        contact_email: payload.contact_email || undefined,
        contact_phone: payload.contact_phone || undefined,
        admin_notes: payload.admin_notes || undefined,
      })
      setResult(data)
      message.success('租户开通成功')
    } catch (error) {
      if (error?.errorFields?.length) {
        const firstField = error.errorFields[0]?.name?.[0]
        const stepIndex = stepFields.findIndex((fields) => fields.includes(firstField))
        if (stepIndex >= 0) setCurrent(stepIndex)
      }
    } finally {
      setSubmitting(false)
    }
  }

  const restart = () => {
    form.resetFields()
    form.setFieldsValue({ trial_days: 14 })
    setSlugEdited(false)
    setCurrent(0)
    setResult(null)
  }

  if (result) {
    return (
      <div>
        <PageHeader title="接入向导" subtitle="新租户开通" />
        <Card>
          <Result
            status="success"
            title="租户已开通"
            subTitle={`${result.tenant_name} · ${result.plan_name}`}
            extra={[
              <Button type="primary" key="detail" onClick={() => navigate(`/tenants/${result.tenant_id}`)}>
                查看租户
              </Button>,
              <Button key="again" onClick={restart}>
                继续接入
              </Button>,
            ]}
          >
            <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="租户标识">{result.slug}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color="blue">试用中</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="订阅">{result.subscription_status}</Descriptions.Item>
              <Descriptions.Item label="试用截止">
                {result.trial_ends_at ? new Date(result.trial_ends_at).toLocaleDateString('zh-CN') : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="首个商户" span={2}>
                {result.merchant_name}
              </Descriptions.Item>
            </Descriptions>
          </Result>
        </Card>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="接入向导" subtitle="新租户开通" />
      <Steps current={current} items={stepItems} responsive style={{ marginBottom: 24 }} />

      <Card>
        <Form form={form} layout="vertical" initialValues={{ trial_days: 14 }} preserve>
          <div hidden={current !== 0}>
            <Row gutter={16}>
              <Col xs={24} md={12}>
                <Form.Item
                  name="name"
                  label="租户名称"
                  rules={[
                    { required: true, message: '请输入租户名称' },
                    { min: 2, max: 100, message: '请输入 2 至 100 个字符' },
                  ]}
                >
                  <Input
                    placeholder="组织或市场名称"
                    onChange={(event) => {
                      if (!slugEdited) {
                        const slug = normalizeSlug(event.target.value)
                        if (slug) form.setFieldValue('slug', slug)
                      }
                    }}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item
                  name="slug"
                  label="租户标识"
                  normalize={normalizeSlug}
                  rules={[
                    { required: true, message: '请输入租户标识' },
                    {
                      pattern: /^[a-z0-9]+(?:-[a-z0-9]+)*$/,
                      message: '仅支持小写字母、数字和单个连字符',
                    },
                  ]}
                >
                  <Input placeholder="例如 shanghai-market" onChange={() => setSlugEdited(true)} />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="contact_email" label="联系邮箱" rules={[{ type: 'email', message: '邮箱格式不正确' }]}>
                  <Input placeholder="owner@example.com" />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="contact_phone" label="联系电话">
                  <Input placeholder="手机号或座机" maxLength={30} />
                </Form.Item>
              </Col>
            </Row>
          </div>

          <div hidden={current !== 1}>
            {plans.length === 0 && !loadingPlans ? (
              <EmptyState description="暂无可用套餐" action={<Button onClick={() => navigate('/plans')}>管理套餐</Button>} />
            ) : (
              <Row gutter={16}>
                <Col xs={24} md={16}>
                  <Form.Item name="plan_id" label="初始套餐" rules={[{ required: true, message: '请选择套餐' }]}>
                    <Select
                      loading={loadingPlans}
                      placeholder="选择套餐"
                      options={plans.map((plan) => ({
                        value: plan.id,
                        label: `${plan.name} (${plan.code}) · ${formatPrice(plan)}`,
                      }))}
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="trial_days" label="试用天数" rules={[{ required: true, message: '请输入试用天数' }]}>
                    <InputNumber min={1} max={90} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                {selectedPlan && (
                  <Col span={24}>
                    <Descriptions size="small" bordered column={{ xs: 1, sm: 3 }}>
                      <Descriptions.Item label="商户上限">{selectedPlan.max_merchants}</Descriptions.Item>
                      <Descriptions.Item label="API 月额度">
                        {Number(selectedPlan.max_api_calls_monthly || 0).toLocaleString()}
                      </Descriptions.Item>
                      <Descriptions.Item label="存储额度">{selectedPlan.max_storage_mb} MB</Descriptions.Item>
                    </Descriptions>
                  </Col>
                )}
              </Row>
            )}
          </div>

          <div hidden={current !== 2}>
            <Form.Item
              name="merchant_name"
              label="首个商户名称"
              rules={[
                { required: true, message: '请输入首个商户名称' },
                { min: 2, max: 100, message: '请输入 2 至 100 个字符' },
              ]}
            >
              <Input prefix={<ShopOutlined />} placeholder="首个摊位或门店名称" />
            </Form.Item>
            <Form.Item name="admin_notes" label="平台备注">
              <Input.TextArea rows={4} maxLength={2000} showCount placeholder="客户来源、交付范围或服务约定" />
            </Form.Item>
          </div>

          <div hidden={current !== 3}>
            <Descriptions bordered column={{ xs: 1, md: 2 }}>
              <Descriptions.Item label="租户名称">{values.name || '-'}</Descriptions.Item>
              <Descriptions.Item label="租户标识">{values.slug || '-'}</Descriptions.Item>
              <Descriptions.Item label="套餐">{selectedPlan?.name || '-'}</Descriptions.Item>
              <Descriptions.Item label="试用期">{values.trial_days || 14} 天</Descriptions.Item>
              <Descriptions.Item label="首个商户">{values.merchant_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="联系人">
                {values.contact_email || values.contact_phone || '-'}
              </Descriptions.Item>
              {values.admin_notes && (
                <Descriptions.Item label="平台备注" span={2}>
                  <Typography.Text>{values.admin_notes}</Typography.Text>
                </Descriptions.Item>
              )}
            </Descriptions>
          </div>
        </Form>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
          <Button
            icon={<ArrowLeftOutlined />}
            disabled={current === 0}
            onClick={() => setCurrent((value) => Math.max(value - 1, 0))}
          >
            上一步
          </Button>
          <Space>
            <Button onClick={() => navigate('/tenants')}>取消</Button>
            {current < stepItems.length - 1 ? (
              <Button type="primary" icon={<ArrowRightOutlined />} iconPosition="end" onClick={next}>
                下一步
              </Button>
            ) : (
              <Button type="primary" icon={<CheckOutlined />} loading={submitting} onClick={submit}>
                确认开通
              </Button>
            )}
          </Space>
        </div>
      </Card>
    </div>
  )
}
