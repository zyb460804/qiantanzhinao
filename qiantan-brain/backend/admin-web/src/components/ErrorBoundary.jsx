import { Component } from 'react'
import { Button, Result } from 'antd'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error('[ErrorBoundary]', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
    window.location.href = '/dashboard'
  }

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="500"
          title="页面发生异常"
          subTitle={this.state.error?.message || '未知错误，请刷新页面重试'}
          extra={[
            <Button type="primary" key="retry" onClick={() => window.location.reload()}>
              刷新页面
            </Button>,
            <Button key="home" onClick={this.handleReset}>
              返回首页
            </Button>,
          ]}
        />
      )
    }
    return this.props.children
  }
}
