import http from './http'

export function listFenceAlerts(params) {
  return http.get('/fence/alerts', { params })
}
export function listFencePoints() {
  return http.get('/fence/points')
}
export function listVehicleUse() {
  return http.get('/vehicle_use')
}
export function listVehicleAccount() {
  return http.get('/vehicle_account')
}