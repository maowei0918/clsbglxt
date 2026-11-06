import http from './http'

// 车辆（GET /vehicles 返回裸数组，不含分页）
export function listVehicles(params) {
  return http.get('/vehicles', { params })
}
export function getVehicle(id) {
  return http.get(`/vehicles/${id}`)
}
export function createVehicle(data) {
  return http.post('/vehicles', null, { params: data })
}
export function updateVehicle(id, payload) {
  return http.put(`/vehicles/${id}`, payload)
}
export function deleteVehicle(id) {
  return http.delete(`/vehicles/${id}`)
}
export function listVehicleTypes() {
  return http.get('/vehicles/types/all')
}
export function listVehicleNatures() {
  return http.get('/vehicles/natures/all')
}
