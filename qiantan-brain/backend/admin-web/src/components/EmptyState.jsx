import { Empty, Button } from 'antd'

/**
 * 统一空状态组件
 *
 * 用法:
 *   <EmptyState description="暂无租户数据" action={<Button>新建租户</Button>} />
 */
export default function EmptyState({ description = '暂无数据', action, image }) {
  return (
    <div style={{ textAlign: 'center', padding: '60px 0' }}>
      <Empty description={description} image={image || Empty.PRESENTED_IMAGE_SIMPLE}>
        {action}
      </Empty>
    </div>
  )
}

/**
 * 错误状态组件
 *
 * 用法:
 *   <ErrorState message="加载失败" requestId={id} onRetry={fetchData} />
 */
export function ErrorState({ message = '加载数据失败', requestId, onRetry }) {
  return (
    <div style={{ textAlign: 'center', padding: '60px 0' }}>
      <div style={{ color: '#DC2626', fontSize: 48, marginBottom: 16 }}>!</div>
      <div style={{ fontSize: 16, color: '#DC2626', marginBottom: 4 }}>{message}</div>
      {requestId && <div style={{ fontSize: 12, color: '#999', marginBottom: 16 }}>Request ID: {requestId}</div>}
      {onRetry && (
        <Button type="primary" onClick={onRetry} style={{ marginTop: 12 }}>
          重新加载
        </Button>
      )}
    </div>
  )
}
