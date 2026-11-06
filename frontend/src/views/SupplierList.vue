<template>
  <div class="page-container">
    <div class="page-header">
      <h2>汽修厂管理</h2>
      <el-button type="primary" @click="openAdd">+ 新建汽修厂</el-button>
    </div>
    <div class="data-card">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="contact" label="联系人" width="120" />
        <el-table-column prop="phone" label="电话" width="140" />
        <el-table-column prop="address" label="地址" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!rows.length && !loading" class="empty-block">暂无汽修厂</div>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑汽修厂' : '新建汽修厂'" width="520px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="联系人"><el-input v-model="form.contact" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="form.phone" /></el-form-item>
        <el-form-item label="地址"><el-input v-model="form.address" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
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
import * as api from '@/api/suppliers'

const loading = ref(false)
const rows = ref([])
const dialog = ref(false)
const form = ref({ id: null, name: '', contact: '', phone: '', address: '', is_active: true })

async function load() {
  loading.value = true
  try { rows.value = await api.listSuppliers() } finally { loading.value = false }
}
function openAdd() { form.value = { id: null, name: '', contact: '', phone: '', address: '', is_active: true }; dialog.value = true }
function openEdit(row) { form.value = { ...row }; dialog.value = true }
async function onSave() {
  if (!form.value.name?.trim()) { ElMessage.warning('请输入名称'); return }
  try {
    if (form.value.id) await api.updateSupplier(form.value.id, form.value)
    else await api.createSupplier(form.value)
    ElMessage.success('保存成功'); dialog.value = false; load()
  } catch {}
}
async function onDelete(row) {
  const ok = await ElMessageBox.confirm(`删除汽修厂「${row.name}」？`, '提示', { type: 'warning' }).catch(() => false)
  if (!ok) return
  try { await api.deleteSupplier(row.id); ElMessage.success('已删除'); load() } catch {}
}
onMounted(load)
</script>
