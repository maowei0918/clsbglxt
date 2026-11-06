<template>
  <el-container class="main-layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">clglxt</div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#001529"
        text-color="#94a3b8"
        active-text-color="#38bdf8"
      >
        <el-menu-item
          v-for="item in menuItems"
          :key="item.path"
          :index="item.path"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div class="breadcrumb">{{ $route.meta.title }}</div>
        <div class="user-area">
          <el-dropdown @command="onUserCommand">
            <span class="user-name">
              <el-icon><UserFilled /></el-icon>
              {{ auth.user?.name || auth.user?.username }}
              <el-tag v-if="auth.user?.is_superuser" size="small" type="warning" style="margin-left:6px">超管</el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="change-password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessageBox, ElMessage } from 'element-plus'
import * as authApi from '@/api/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

// 菜单按权限过滤；超管全显
const menuItems = computed(() => {
  return router.options.routes
    .find(r => r.path === '/')?.children
    .filter(c => !c.meta?.hidden && auth.can(c.meta?.perm || ''))
    .map(c => ({ path: '/' + c.path, title: c.meta?.title, icon: c.meta?.icon }))
})

async function onUserCommand(cmd) {
  if (cmd === 'logout') {
    await ElMessageBox.confirm('确定退出登录？', '提示', { type: 'warning' })
    try { await authApi.logout() } catch { /* 无状态，忽略 */ }
    auth.logout()
    router.replace('/login')
  } else if (cmd === 'change-password') {
    router.push('/change-password')
  }
}
</script>

<style scoped>
.main-layout { height: 100vh; }
.sidebar { background: #001529; display: flex; flex-direction: column; }
.logo {
  height: 60px; display: flex; align-items: center; justify-content: center;
  color: #38bdf8; font-weight: 700; font-size: 20px; letter-spacing: 1px;
  border-bottom: 1px solid #1e293b;
}
.sidebar :deep(.el-menu) { border-right: none; }
.topbar {
  background: #fff; display: flex; justify-content: space-between; align-items: center;
  border-bottom: 1px solid #e2e8f0; height: 56px; padding: 0 20px;
}
.breadcrumb { font-size: 16px; font-weight: 600; }
.user-name { display: flex; align-items: center; gap: 6px; cursor: pointer; color: #1f2937; }
.content { background: #f0f2f5; padding: 20px; overflow-y: auto; }
</style>
