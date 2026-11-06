import http from './http'

// 安全：凭据一律走 JSON body，绝不放进 query string
// （query 会落到 nginx access log / 浏览器历史 / Referer，等同明文泄露密码）
export function login(username, password) {
  return http.post('/auth/login', { username, password })
}

export function logout() {
  return http.post('/auth/logout')
}

export function fetchMe() {
  return http.get('/auth/me')
}

export function changePassword(oldPassword, newPassword) {
  return http.post('/auth/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}
