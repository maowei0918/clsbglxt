<template>
  <div class="page-container">
    <div class="page-header">
      <h2>车辆档案</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新增车辆</el-button>
    </div>

    <div class="filter-card">
      <el-form :inline="true" @submit.prevent="load">
        <el-form-item label="车牌">
          <el-input v-model="filter.keyword" placeholder="输入车牌号" clearable style="width:180px" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="filter.vehicle_type_id" placeholder="全部" clearable style="width:150px">
            <el-option v-for="t in types" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="data-card">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="plate_no" label="车牌号" width="130" />
        <el-table-column prop="vehicle_type_name" label="类型" width="120" />
        <el-table-column prop="department_name" label="部门" width="140" />
        <el-table-column prop="nature" label="性质" width="100" />
        <el-table-column prop="brand" label="品牌" width="120" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === '在用' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!rows.length && !loading" class="empty-block">暂无车辆</div>
    </div>

    <el-dialog v-model="dialog" :title="editing ? '编辑车辆' : '新增车辆'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="车牌号">
          <el-input v-model="form.plate_no" placeholder="如 鲁B12345" />
        </el-form-item>
        <el-form-item label="车辆类型">
          <el-select v-model="form.vehicle_type_id" placeholder="请选择" style="width:100%">
            <el-option v-for="t in types" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="form.department_id" placeholder="请选择" style="width:100%">
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="性质">
          <el-select v-model="form.nature" placeholder="可选" clearable style="width:100%">
            <el-option v-for="n in natures" :key="n.id" :label="n.name" :value="n.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="品牌">
          <el-input v-model="form.brand" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="在用">在用</el-radio>
            <el-radio value="停用">停用</el-radio>
          </el-radio-group>
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
import { ref, reactive, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '@/api/vehicles'

const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editing = ref(null)
const rows = ref([])
const types = ref([])
const natures = ref([])
const departments = ref([])
const filter = reactive({ keyword: '', vehicle_type_id: null })
const form = reactive({ plate_no: '', vehicle_type_id: null, department_id: null, nature: '', brand: '', status: '在用' })

async function load() {
  loading.value = true
  try {
    rows.value = await api.listVehicles({ keyword: filter.keyword, vehicle_type_id: filter.vehicle_type_id || undefined })
  } finally {
    loading.value = false
  }
}
function reset() {
  filter.keyword = ''
  filter.vehicle_type_id = null
  load()
}
function openCreate() {
  editing.value = null
  Object.assign(form, { plate_no: '', vehicle_type_id: null, department_id: null, nature: '', brand: '', status: '在用' })
  dialog.value = true
}
function openEdit(row) {
  editing.value = row
  Object.assign(form, { ...row })
  dialog.value = true
}
async function save() {
  saving.value = true
  try {
    if (editing.value) {
      await api.updateVehicle(editing.value.id, { ...form })
    } else {
      await api.createVehicle({ ...form })
    }
    ElMessage.success('保存成功')
    dialog.value = false
    load()
  } finally {
    saving.value = false
  }
}
async function remove(row) {
  await ElMessageBox.confirm(`确定删除车辆 ${row.plate_no}？`, '提示', { type: 'warning' })
  await api.deleteVehicle(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(async () => {
  load()
  types.value = await api.listVehicleTypes().catch(() => [])
  natures.value = await api.listVehicleNatures().catch(() => [])
  departments.value = await api.listDepartments().catch(() => [])
})
</script>
