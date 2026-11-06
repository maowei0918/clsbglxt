import http from './http'

export function listSuppliers() {
  return http.get('/suppliers')
}
export function createSupplier(data) {
  return http.post('/suppliers', null, { params: data })
}
export function updateSupplier(id, data) {
  return http.put(`/suppliers/${id}`, data)
}
export function deleteSupplier(id) {
  return http.delete(`/suppliers/${id}`)
}
