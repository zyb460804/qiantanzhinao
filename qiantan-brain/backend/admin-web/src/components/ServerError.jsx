import { Result, Button } from 'antd'

export default function ServerError({ requestId }) {
  return (
    <Result
      status="500"
      title="服务器错误"
      subTitle={
        <span>
          服务器处理请求时发生错误，请稍后重试
          {requestId && (
            <span style={{ display: 'block', marginTop: 8, color: '#999', fontSize: 12 }}>Request ID: {requestId}</span>
          )}
        </span>
      }
      extra={[
        <Button type="primary" key="retry" onClick={() => window.location.reload()}>
          刷新页面
        </Button>,
        <Button key="back" onClick={() => window.history.back()}>
          返回上页
        </Button>,
      ]}
    />
  )
}
