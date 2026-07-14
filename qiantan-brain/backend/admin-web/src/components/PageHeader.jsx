import { Typography } from 'antd'

/**
 * 统一页面标题组件
 *
 * 用法:
 *   <PageHeader title="租户管理" subtitle="管理所有租户的接入、状态和套餐" />
 */
export default function PageHeader({ title, subtitle, extra }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div>
          <Typography.Title level={4} style={{ margin: 0, marginBottom: 4 }}>
            {title}
          </Typography.Title>
          {subtitle && <Typography.Text type="secondary">{subtitle}</Typography.Text>}
        </div>
        {extra && <div>{extra}</div>}
      </div>
    </div>
  )
}
