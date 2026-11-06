import http from './http'

export function listRoles() {
  return http.get('/system/roles')
}
export function createRole(data) {
  return http.post('/system/roles', null, { params: data })
}
export function updateRole(id, data) {
  return http.put(`/system/roles/${id}`, data)
}
export function deleteRole(id) {
  return http.delete(`/system/roles/${id}`)
}
