<template>
  <div class="cp-page">
    <div class="cp-card">
      <div class="brand">
        <div class="logo">修改密码</div>
        <p>{{ isFirst ? '首次登录请设置新密码' : '请输入旧密码与新密码' }}</p>
      </div>
      <el-form :model="form" label-width="80px" @submit.prevent="onSubmit">
        <el-form-item label="旧密码">
          <el-input v-model="form.oldPassword" type="password" show-password size="large" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="form.newPassword" type="password" show-password size="large" placeholder="至少8位，含字母和数字" />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="form.confirm" type="password" show-password size="large" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width:100%" size="large">
          {{ isFirst ? '设置并登录' : '确认修改' }}
        </el-button>
        <el-button v-if="!isFirst" @click="router.replace('/dashboard')" style="width:100%;margin-top:8px" size="large">
          取消
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

const auth = useAuthStore()
const router = useRouter()

const isFirst = computed(() => !!auth.user?.must_change_password)
const loading = ref(false)
const form = reactive({ oldPassword: '', newPassword: '', confirm: '' })

async function onSubmit() {
  if (!form.oldPassword) { ElMessage.warning('请输入旧密码'); return }
  if (form.newPassword.length < 8) { ElMessage.warning('新密码至少 8 位'); return }
  if (form.newPassword !== form.confirm) { ElMessage.warning('两次输入的新密码不一致'); return }
  if (form.oldPassword === form.newPassword) { ElMessage.warning('新密码不能与旧密码相同'); return }
  loading.value = true
  try {
    await auth.changePassword(form.oldPassword, form.newPassword)
    ElMessage.success('密码已修改')
    router.replace('/dashboard')
  } catch { /* http 拦截器已提示 */ }
  finally { loading.value = false }
}
</script>

<style scoped>
.cp-page {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #0c4a6e 100%);
}
.cp-card {
  background: #fff; border-radius: 16px; padding: 36px 40px;
  width: 100%; max-width: 440px;
}
.brand { text-align: center; margin-bottom: 24px; }
.logo { font-size: 22px; font-weight: 700; color: #0ea5e9; }
.brand p { color: #64748b; font-size: 13px; margin: 6px 0 0; }
</style>
