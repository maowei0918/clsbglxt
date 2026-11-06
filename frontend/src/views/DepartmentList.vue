<template>
  <div class="page-container">
    <div class="page-header">
      <h2>部门管理</h2>
      <el-button type="primary" @click="openAdd" v-if="auth.can('system:department_manage')">+ 新建部门</el-button>
    </div>
    <div class="data-card">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="部门名称" />
        <el-table-column prop="remark" label="备注" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)" v-if="auth.can('system:department_manage')">编辑</el-button>
            <el-button size="small" type="danger" @click="onDelete(row)" v-if="auth.can('system:department_manage')">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!rows.length && !loading" class="empty-block">暂无部门</div>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑部门' : '新建部门'" width="480px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '@/api/departments'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const rows = ref([])
const dialog = ref(false)
const form = ref({ id: null, name: '', remark: '' })

async function load() {
  loading.value = true
  try { rows.value = await api.listDepartments() } finally { loading.value = false }
}
function openAdd() { form.value = { id: null, name: '', remark: '' }; dialog.value = true }
function openEdit(row) { form.value = { ...row }; dialog.value = true }
async function onSave() {
  if (!form.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  try {
    if (form.value.id) await api.updateDepartment(form.value.id, form.value)
    else await api.createDepartment(form.value)
    ElMessage.success('保存成功'); dialog.value = false; load()
  } catch {}
}
async function onDelete(row) {
  await ElMessageBox.confirm(`删除部门「${row.name}」？`, '提示', { type: 'warning' }).catch(() => null)
  if (!row) return
  try { await api.deleteDepartment(row.id); ElMessage.success('已删除'); load() } catch {}
}
onMounted(load)
</script>
