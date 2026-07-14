import { Result, Button } from 'antd'
import { useNavigate } from 'react-router-dom'

export default function NotFound() {
  const navigate = useNavigate()
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="请检查 URL 是否正确，或返回首页重新导航"
      extra={[
        <Button type="primary" key="home" onClick={() => navigate('/dashboard')}>
          返回首页
        </Button>,
        <Button key="back" onClick={() => navigate(-1)}>
          返回上页
        </Button>,
      ]}
    />
  )
}
