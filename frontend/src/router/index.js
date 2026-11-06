import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getToken } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/change-password',
    name: 'change-password',
    component: () => import('@/views/ChangePassword.vue'),
    meta: { title: '修改密码', allowMustChange: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '仪表盘', icon: 'DataLine' },
      },
      {
        path: 'repairs',
        name: 'repairs',
        component: () => import('@/views/RepairList.vue'),
        meta: { title: '维修单', perm: 'repair:view', icon: 'Tools' },
      },
      {
        path: 'repairs/:id',
        name: 'repair-detail',
        component: () => import('@/views/RepairDetail.vue'),
        meta: { title: '维修单详情', perm: 'repair:view' },
      },
      {
        path: 'vehicles',
        name: 'vehicles',
        component: () => import('@/views/VehicleList.vue'),
        meta: { title: '车辆档案', perm: 'vehicle:view', icon: 'Van' },
      },
      {
        path: 'vehicle-use',
        name: 'vehicle-use',
        component: () => import('@/views/VehicleUse.vue'),
        meta: { title: '用车记录', perm: 'vehicle_use:view', icon: 'Promotion' },
      },
      {
        path: 'daily-check',
        name: 'daily-check',
        component: () => import('@/views/DailyCheck.vue'),
        meta: { title: '日检', perm: 'daily_check:view', icon: 'Calendar' },
      },
      {
        path: 'fence',
        name: 'fence',
        component: () => import('@/views/Fence.vue'),
        meta: { title: '围栏告警', perm: 'fence:view', icon: 'LocationFilled' },
      },
      {
        path: 'personnel',
        name: 'personnel',
        component: () => import('@/views/PersonnelList.vue'),
        meta: { title: '人员管理', perm: 'system:user_manage', icon: 'User' },
      },
      {
        path: 'roles',
        name: 'roles',
        component: () => import('@/views/SystemRoles.vue'),
        meta: { title: '角色权限', perm: 'system:role_manage', icon: 'Lock' },
      },
      {
        path: 'departments',
        name: 'departments',
        component: () => import('@/views/DepartmentList.vue'),
        meta: { title: '部门管理', perm: 'system:department_manage', icon: 'OfficeBuilding' },
      },
      {
        path: 'suppliers',
        name: 'suppliers',
        component: () => import('@/views/SupplierList.vue'),
        meta: { title: '汽修厂管理', perm: 'system:user_manage', icon: 'Shop' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const hasToken = !!getToken()

  // 公开页
  if (to.meta.public) {
    if (to.name === 'login' && hasToken) return { path: '/dashboard' }
    return true
  }

  // 未登录 → 登录页
  if (!hasToken) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // 已登录：确保用户信息就绪
  const auth = useAuthStore()
  if (!auth.user) {
    const user = await auth.fetchUser()
    if (!user) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }

  // 强制改密：除白名单外全部拦到改密页
  if (auth.user?.must_change_password && !to.meta.allowMustChange) {
    return { path: '/change-password' }
  }

  // 权限校验（后端才是兜底，这里只是体验层）
  if (to.meta.perm && !auth.can(to.meta.perm)) {
    ElMessage.error('无权访问该页面')
    return { path: '/dashboard' }
  }

  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · clglxt` : 'clglxt 车辆管理系统'
})

export default router
