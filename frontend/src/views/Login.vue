<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <div class="logo">clglxt</div>
        <p>车辆管理系统</p>
      </div>
      <el-form @submit.prevent="onSubmit" :model="form" ref="formRef">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" size="large" autofocus>
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password>
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-button type="primary" size="large" native-type="submit" :loading="loading" style="width:100%">
          登 录
        </el-button>
      </el-form>
      <div class="hint">{{ siteTitle }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const siteTitle = ref('clglxt 车辆管理系统')
const loading = ref(false)
const form = reactive({ username: '', password: '' })

async function onSubmit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const user = await auth.doLogin(form.username, form.password)
    if (user.must_change_password) {
      router.replace('/change-password')
      return
    }
    const redirect = route.query.redirect || '/dashboard'
    // 安全：只允许相对路径跳转，防开放重定向（钓鱼页通过 redirect=?url 劫持）
    const safe = redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/dashboard'
    router.replace(safe)
  } catch (e) {
    // http 拦截器已提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #0c4a6e 100%);
  padding: 24px;
}
.login-card {
  background: #fff; border-radius: 16px; padding: 40px 44px;
  width: 100%; max-width: 420px;
  box-shadow: 0 25px 60px rgba(0,0,0,.35);
}
.brand { text-align: center; margin-bottom: 28px; }
.logo {
  font-size: 32px; font-weight: 800; letter-spacing: 1px;
  background: linear-gradient(90deg, #0ea5e9, #22d3ee);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.brand p { color: #64748b; font-size: 14px; margin: 6px 0 0; }
.hint { text-align: center; color: #94a3b8; font-size: 12px; margin-top: 20px; }
</style>
