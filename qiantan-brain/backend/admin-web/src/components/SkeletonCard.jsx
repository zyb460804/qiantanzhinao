import { Card, Skeleton } from 'antd'

/** 卡片骨架屏 */
export function SkeletonCard({ rows = 3 }) {
  return (
    <Card>
      <Skeleton active paragraph={{ rows }} />
    </Card>
  )
}

/** 统计卡片骨架屏 */
export function StatSkeleton() {
  return (
    <Card>
      <Skeleton active paragraph={{ rows: 1 }} title={{ width: '60%' }} />
    </Card>
  )
}

/** 表格骨架屏 */
export function TableSkeleton({ rows = 5 }) {
  return (
    <Card>
      <Skeleton active paragraph={{ rows }} title={{ width: '30%' }} />
    </Card>
  )
}
