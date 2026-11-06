import http from './http'

export function listUsers(params) {
  return http.get('/personnel', { params })
}
export function createUser(data) {
  return http.post('/personnel', null, { params: data })
}
export function updateUser(id, data) {
  return http.put(`/personnel/${id}`, data)
}
export function deleteUser(id) {
  return http.delete(`/personnel/${id}`)
}
export function resetPassword(id) {
  return http.post(`/personnel/${id}/reset-password`)
}
