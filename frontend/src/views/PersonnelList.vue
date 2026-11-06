<template>
  <div class="page-container">
    <div class="page-header">
      <h2>人员管理</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新增人员</el-button>
    </div>

    <div class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="关键词">
          <el-input v-model="keyword" placeholder="账号 / 姓名" clearable style="width: 200px" @keyup.enter="load" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="data-card">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="username" label="登录账号" width="140" />
        <el-table-column prop="name" label="姓名" width="120" />
        <el-table-column prop="phone" label="手机" width="130" />
        <el-table-column label="归属" width="150">
          <template #default="{ row }">
            {{ row.supplier_name || row.department_name || '—' }}
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_supplier ? 'warning' : 'info'" size="small">
              {{ row.is_supplier ? '汽修厂' : '内部' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" @click="resetPwd(row)">重置密码</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!rows.length && !loading" class="empty-block">暂无人员</div>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑人员' : '新增人员'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="登录账号" v-if="!form.id">
          <el-input v-model="form.username" placeholder="小写字母/数字" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="手机">
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item label="归属部门">
          <el-select v-model="form.department_id" clearable placeholder="内部账号选部门" style="width: 100%">
            <el-option v-for="d in depts" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="归属汽修厂">
          <el-select v-model="form.supplier_id" clearable placeholder="汽修厂账号选这里" style="width: 100%">
            <el-option v-for="s in shops" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role_ids" multiple placeholder="分配角色" style="width: 100%">
            <el-option v-for="r in roles" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.id" label="状态">
          <el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import http from '@/api/http'

const loading = ref(false)
const saving = ref(false)
const rows = ref([])
const depts = ref([])
const shops = ref([])
const roles = ref([])
const keyword = ref('')
const dialog = ref(false)
const form = ref({})

async function load() {
  loading.value = true
  try {
    const res = await http.get('/personnel', { params: { keyword: keyword.value } })
    rows.value = Array.isArray(res) ? res : (res?.items || [])
  } finally { loading.value = false }
}

function reset() { keyword.value = ''; load() }

async function loadRefs() {
  const [d, s, r] = await Promise.allSettled([
    http.get('/departments'),
    http.get('/suppliers'),
    http.get('/system/roles'),
  ])
  depts.value = d.status === 'fulfilled' ? (d.value || []) : []
  shops.value = s.status === 'fulfilled' ? (s.value || []) : []
  roles.value = r.status === 'fulfilled' ? (r.value || []) : []
}

function openCreate() {
  form.value = { username: '', name: '', phone: '', department_id: null, supplier_id: null, role_ids: [] }
  dialog.value = true
}

function openEdit(row) {
  form.value = {
    id: row.id, name: row.name, phone: row.phone,
    department_id: row.department_id || null,
    supplier_id: row.supplier_id || null,
    is_active: row.is_active,
    role_ids: (row.roles || []).map(r => r.id),
  }
  dialog.value = true
}

async function save() {
  const f = form.value
  if (!f.name) { ElMessage.warning('请填写姓名'); return }
  saving.value = true
  try {
    if (f.id) {
      await http.put(`/personnel/${f.id}`, {
        name: f.name, phone: f.phone || '',
        department_id: f.department_id ?? undefined,
        supplier_id: f.supplier_id ?? undefined,
        is_active: f.is_active,
        role_ids: f.role_ids?.length ? f.role_ids : undefined,
      })
      ElMessage.success('已保存')
    } else {
      if (!f.username) { ElMessage.warning('请填写登录账号'); return }
      const r = await http.post('/personnel', null, {
        params: {
          username: f.username, name: f.name, phone: f.phone || '',
          department_id: f.department_id ?? undefined,
          supplier_id: f.supplier_id ?? undefined,
          role_ids: f.role_ids?.length ? f.role_ids.join(',') : undefined,
        },
      })
      ElMessage.success(`已创建，初始密码：${r.temp_password}（请转告本人）`)
    }
    dialog.value = false
    load()
  } finally { saving.value = false }
}

async function resetPwd(row) {
  const ok = await ElMessageBox.confirm(`重置「${row.name}」的密码？旧密码立即失效。`, '确认', { type: 'warning' })
    .catch(() => false)
  if (!ok) return
  const r = await http.post(`/personnel/${row.id}/reset-password`)
  ElMessageBox.alert(`新密码：${r.temp_password}`, '重置成功', { type: 'success' })
}

async function remove(row) {
  const ok = await ElMessageBox.confirm(`删除「${row.name}」？账号将停用并软删除。`, '确认', { type: 'warning' })
    .catch(() => false)
  if (!ok) return
  await http.delete(`/personnel/${row.id}`)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadRefs() })
</script>
