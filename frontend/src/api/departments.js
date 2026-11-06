import http from './http'

export function listDepartments() {
  return http.get('/departments')
}
export function createDepartment(data) {
  return http.post('/departments', null, { params: data })
}
export function updateDepartment(id, data) {
  return http.put(`/departments/${id}`, data)
}
export function deleteDepartment(id) {
  return http.delete(`/departments/${id}`)
}
