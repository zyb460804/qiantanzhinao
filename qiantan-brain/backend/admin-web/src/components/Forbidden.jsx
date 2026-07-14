import { Result, Button } from 'antd'
import { useNavigate } from 'react-router-dom'

export default function Forbidden() {
  const navigate = useNavigate()
  return (
    <Result
      status="403"
      title="无权限访问"
      subTitle="您没有访问此页面的权限，如需开通请联系超级管理员"
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
