import http from './http'

export function listRepairs(params) {
  return http.get('/repairs', { params })
}
export function getRepair(id) {
  return http.get(`/repairs/${id}`)
}
export function createRepair(data) {
  return http.post('/repairs', null, { params: data })
}
// 推进流程（query 参数；seen_seq 必传）
export function advanceRepair(id, action, note = '', seenSeq) {
  return http.post(`/repairs/${id}/advance`, null, {
    params: { action, note, seen_seq: seenSeq },
  })
}
// 报价
export function claimQuote(id) {
  return http.post(`/repairs/${id}/quote/claim`)
}
export function submitQuote(id, payload) {
  return http.post(`/repairs/${id}/quote`, payload)
}
export function cancelRepair(id, reason = '') {
  return http.post(`/repairs/${id}/cancel`, { reason })
}
