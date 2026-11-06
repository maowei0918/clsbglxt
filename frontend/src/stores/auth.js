import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const token = ref(localStorage.getItem('clglxt_token'))

  const isLoggedIn = computed(() => !!user.value)
  const isSuperUser = computed(() => user.value?.is_superuser || false)
  // 超管始终放行；其他用户按 perms 数组判断
  const perms = computed(() => user.value?.perms || [])

  function can(code) {
    if (!user.value) return false
    if (user.value.is_superuser) return true
    const p = perms.value
    return p.includes('*') || p.includes(code)
  }

  async function doLogin(username, password) {
    const data = await authApi.login(username, password)
    localStorage.setItem('clglxt_token', data.token)
    token.value = data.token
    user.value = data.user
    return data.user
  }

  async function fetchUser() {
    try {
      user.value = await authApi.fetchMe()
      return user.value
    } catch {
      user.value = null
      return null
    }
  }

  async function changePassword(oldPwd, newPwd) {
    const data = await authApi.changePassword(oldPwd, newPwd)
    localStorage.setItem('clglxt_token', data.token)
    token.value = data.token
    user.value = data.user
    return data.user
  }

  function logout() {
    localStorage.removeItem('clglxt_token')
    user.value = null
    token.value = null
  }

  return { user, token, isLoggedIn, isSuperUser, perms, can, doLogin, fetchUser, changePassword, logout }
})
