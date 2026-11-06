import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const TOKEN_KEY = 'clglxt_token'

export const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 请求注入 Bearer token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应统一处理
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    // 401：令牌失效 → 清 token 跳登录（安装向导/登录接口本身不跳）
    if (status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      const path = router.currentRoute.value.path
      if (path !== '/login' && path !== '/install') {
        ElMessage.warning('登录已过期，请重新登录')
        const safePath = path.startsWith('/') && !path.startsWith('//') ? path : '/dashboard'
        router.replace({ path: '/login', query: { redirect: safePath } })

      }
    } else if (status === 403) {
      ElMessage.error(detail || '无权操作')
    } else if (status === 429) {
      ElMessage.warning(detail || '操作过于频繁，请稍后再试')
    } else if (detail) {
      ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    } else if (status >= 500) {
      ElMessage.error('服务器错误，请稍后再试')
    }
    return Promise.reject(err)
  }
)

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export default http
