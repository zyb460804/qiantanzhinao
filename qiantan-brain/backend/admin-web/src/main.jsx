import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { cssVars } from './theme/tokens'
import './index.css'

// 注入品牌 CSS 变量到 :root（与小程序 app.wxss 色彩体系对齐）
Object.entries(cssVars).forEach(([key, value]) => {
  document.documentElement.style.setProperty(key, value)
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <ConfigProvider locale={zhCN}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ConfigProvider>
    </BrowserRouter>
  </StrictMode>,
)
