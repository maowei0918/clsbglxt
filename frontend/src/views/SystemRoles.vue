<template>
  <div class="page-container">
    <div class="page-header">
      <h2>角色与权限</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新增角色</el-button>
    </div>

    <div class="data-card">
      <el-table :data="roles" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="角色名" width="150" />
        <el-table-column label="归属" width="100">
          <template #default="{ row }">
            <el-tag :type="row.side === 'external' ? 'warning' : 'info'" size="small">
              {{ row.side === 'external' ? '汽修厂' : '内部' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="内置" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_system" type="success" size="small">内置</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="权限点" min-width="240">
          <template #default="{ row }">
            <el-tag v-for="p in row.perms" :key="p" size="small" class="perm-tag">{{ p }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="!row.is_system" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!roles.length && !loading" class="empty-block">暂无角色</div>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑角色' : '新增角色'" width="560px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="角色名">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="归属">
          <el-radio-group v-model="form.side">
            <el-radio value="internal">内部</el-radio>
            <el-radio value="external">汽修厂</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="权限点">
          <el-select v-model="form.perms" multiple filterable placeholder="选择权限点" style="width: 100%">
            <el-option-group v-for="g in groupedPerms" :key="g.label" :label="g.label">
              <el-option v-for="p in g.items" :key="p" :label="p" :value="p" />
            </el-option-group>
          </el-select>
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
const roles = ref([])
const dialog = ref(false)
const form = ref({})

const KNOWN_PERMS = [
  'repair:view','repair:report','repair:quote','repair:approve','repair:manage',
  'contract:view','contract:manage',
  'vehicle:view','vehicle:manage',
  'daily_check:view','daily_check:review',
  'system:user_manage','system:role_manage','system:department_manage','system:log_view',
  'system:setting_ai','system:setting_mail','system:setting_backup',
  'system:setting_datadir','system:setting_vehicle_use','system:setting_repair_days',
  'system:setting_network','system:setting_app_update',
  'vehicle_use:view','vehicle_account:view','fence:view',
  'data:scope_all',
]
const groupedPerms = [
  { label: '维修', items: KNOWN_PERMS.filter(p => p.startsWith('repair')) },
  { label: '车辆/合同', items: KNOWN_PERMS.filter(p => p.startsWith('vehicle') || p.startsWith('contract')) },
  { label: '日检', items: KNOWN_PERMS.filter(p => p.startsWith('daily_check')) },
  { label: '系统', items: KNOWN_PERMS.filter(p => p.startsWith('system')) },
  { label: '其他', items: KNOWN_PERMS.filter(p => p.startsWith('data:') || p.startsWith('fence') || p.startsWith('vehicle_use') || p.startsWith('vehicle_account')) },
]

async function load() {
  loading.value = true
  try {
    const res = await http.get('/system/roles')
    roles.value = Array.isArray(res) ? res : (res?.items || [])
  } finally { loading.value = false }
}

function openCreate() {
  form.value = { name: '', side: 'internal', perms: [] }
  dialog.value = true
}
function openEdit(row) {
  form.value = { id: row.id, name: row.name, side: row.side, perms: [...(row.perms || [])] }
  dialog.value = true
}

async function save() {
  const f = form.value
  if (!f.name || !f.name.trim()) { ElMessage.warning('请填写角色名'); return }
  saving.value = true
  try {
    if (f.id) {
      await http.put(`/system/roles/${f.id}`, {
        name: f.name, side: f.side,
        perms: f.perms?.length ? f.perms : undefined,
      })
      ElMessage.success('已保存')
    } else {
      await http.post('/system/roles', null, {
        params: { name: f.name, side: f.side, perms: f.perms?.join(',') },
      })
      ElMessage.success('已创建')
    }
    dialog.value = false
    load()
  } finally { saving.value = false }
}

async function remove(row) {
  const ok = await ElMessageBox.confirm(`删除角色「${row.name}」？`, '确认', { type: 'warning' }).catch(() => false)
  if (!ok) return
  await http.delete(`/system/roles/${row.id}`)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>

<style scoped>
.perm-tag { margin: 0 4px 4px 0; }
</style>
