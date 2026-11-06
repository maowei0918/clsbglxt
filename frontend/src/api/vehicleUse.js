import http from './http'

// 临时用车备案（GET /vehicle-use 返回裸数组，不含分页）
export function listVehicleUse(params) {
  return http.get('/vehicle-use', { params })
}
export function createVehicleUse(data) {
  return http.post('/vehicle-use', null, { params: data })
}
export function deleteVehicleUse(id) {
  return http.delete(`/vehicle-use/${id}`)
}
// 归档 / 恢复：后端需 vehicle:manage 权限（403 由 http 拦截器统一提示）
export function archiveVehicleUse(id) {
  return http.post(`/vehicle-use/${id}/archive`)
}
export function restoreVehicleUse(id) {
  return http.post(`/vehicle-use/${id}/restore`)
}
