/**
 * Axios API client for SaaS admin Web.
 * Authentication is delivered only through an HttpOnly, Secure,
 * SameSite=Strict cookie; browser JavaScript never stores the JWT.
 */
import axios from 'axios'
import { message } from 'antd'

const api = axios.create({
  baseURL: '/api/admin',
  timeout: 30000,
  withCredentials: true,
})

// Extract request-id from response headers
function getRequestId(error) {
  return error?.response?.headers?.['x-request-id'] || error?.response?.data?.request_id || ''
}

// Global error messages for common status codes
const STATUS_MESSAGES = {
  400: '请求参数错误',
  403: '无权限执行此操作',
  404: '请求的资源不存在',
  409: '操作冲突，请刷新后重试',
  422: '请求数据格式不正确',
  429: '操作过于频繁，请稍后重试',
  500: '服务器内部错误',
}

// Response interceptor
api.interceptors.response.use(
  // Success: unwrap .data
  (response) => response.data,

  // Error: unified handling
  (error) => {
    // Cancelled request — ignore silently
    if (axios.isCancel(error)) {
      return Promise.reject(error)
    }

    const status = error.response?.status
    const serverMessage = error.response?.data?.detail || error.response?.data?.message
    const requestId = getRequestId(error)

    // 401: auto-logout
    if (status === 401) {
      // Don't show error message for 401 — just redirect
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
      return Promise.reject(error)
    }

    // 403: permission denied
    if (status === 403) {
      message.error(serverMessage || '无权限执行此操作')
      return Promise.reject(error)
    }

    // Network error
    if (!status) {
      message.error('网络连接失败，请检查网络')
      return Promise.reject(error)
    }

    // Other errors with server message
    if (serverMessage) {
      const suffix = requestId ? ` (${requestId})` : ''
      message.error(`${serverMessage}${suffix}`)
      return Promise.reject(error)
    }

    // Fallback
    const defaultMsg = STATUS_MESSAGES[status] || `请求失败 (${status})`
    const suffix2 = requestId ? ` [${requestId}]` : ''
    message.error(`${defaultMsg}${suffix2}`)

    return Promise.reject(error)
  },
)

export default api
