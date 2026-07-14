import { useState } from 'react'
import { Modal, Input, Typography } from 'antd'

/**
 * 高风险操作确认弹窗 — 强制填写原因。
 *
 * 用法:
 *   <ConfirmWithReason
 *     title="标记支付"
 *     description="将发票 INV-202607-001 标记为已支付"
 *     impact="立即生效，将记录到审计日志"
 *     onSubmit={(reason) => handleMarkPaid(id, reason)}
 *   >
 *     <Button danger>标记已付</Button>
 *   </ConfirmWithReason>
 */
export default function ConfirmWithReason({ title, description, impact, onSubmit, children }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)

  const handleOk = async () => {
    if (!reason.trim()) return
    setLoading(true)
    try {
      await onSubmit(reason.trim())
      setOpen(false)
      setReason('')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <span onClick={() => setOpen(true)}>{children}</span>
      <Modal
        title={title}
        open={open}
        onOk={handleOk}
        onCancel={() => {
          setOpen(false)
          setReason('')
        }}
        confirmLoading={loading}
        okText="确认执行"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled: !reason.trim() }}
        width={480}
      >
        <Typography.Paragraph style={{ marginBottom: 8 }}>
          <Typography.Text strong>{description}</Typography.Text>
        </Typography.Paragraph>
        {impact && (
          <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
            影响：{impact}
          </Typography.Paragraph>
        )}
        <div style={{ marginTop: 16 }}>
          <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>
            执行原因 <Typography.Text type="danger">*</Typography.Text>
          </Typography.Text>
          <Input.TextArea
            rows={3}
            placeholder="请填写执行此操作的原因，将记录到审计日志"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
            showCount
          />
        </div>
      </Modal>
    </>
  )
}
