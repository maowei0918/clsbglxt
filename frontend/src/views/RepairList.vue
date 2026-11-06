<template>
  <div class="page-container">
    <div class="page-header">
      <h2>维修单</h2>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建维修单</el-button>
    </div>

    <div class="filter-card">
      <el-form :inline="true" @submit.prevent="load">
        <el-form-item label="关键词">
          <el-input v-model="filter.keyword" placeholder="单号/描述" clearable style="width:180px" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filter.status" placeholder="全部" clearable style="width:130px">
            <el-option label="处理中" value="processing" />
            <el-option label="已完成" value="completed" />
            <el-option label="已驳回" value="rejected" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="data-card">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="order_no" label="单号" width="180" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="步骤" width="80">
          <template #default="{ row }">第 {{ row.current_seq }} 步</template>
        </el-table-column>
        <el-table-column label="创建时间">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/repairs/${row.id}`)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page" v-model:page-size="limit"
        :total="total" layout="total, prev, next" background style="margin-top:16px"
      />
      <div v-if="!rows.length && !loading" class="empty-block">暂无维修单</div>
    </div>

    <el-dialog v-model="dialog" title="新建维修单" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="车辆"><el-input :model-value="selectedVehicle" disabled placeholder="先选择车辆" /></el-form-item>
        <el-form-item label="车牌搜索">
          <el-select v-model="form.vehicle_id" filterable remote :remote-method="searchVehicle" :loading="searching" placeholder="搜索车牌号" style="width:100%">
            <el-option v-for="v in vehicleOpts" :key="v.id" :label="v.plate_no" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="指派汽修厂">
          <el-select v-model="form.supplier_id" placeholder="可选" clearable style="width:100%">
            <el-option v-for="s in suppliers" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/repairs'
import * as vehiclesApi from '@/api/vehicles'
import * as systemApi from '@/api/system'

const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const limit = ref(20)
const dateRange = ref([])
const vehicleOpts = ref([])
const searching = ref(false)
const suppliers = ref([])
const selectedVehicle = ref('')

const filter = reactive({ keyword: '', status: '' })
const form = reactive({ vehicle_id: null, description: '', supplier_id: null })

watch(() => filter.keyword, () => { page.value = 1 })
watch(dateRange, () => { page.value = 1 })

function statusText(s) {
  return { processing: '处理中', completed: '已完成', rejected: '已驳回', cancelled: '已取消' }[s] || s
}
function statusType(s) {
  return { processing: 'warning', completed: 'success', rejected: 'danger', cancelled: 'info' }[s] || 'info'
}
function fmt(t) { return t ? t.replace('T', ' ').slice(0, 16) : '—' }

async function load() {
  loading.value = true
  try {
    const params = {
      keyword: filter.keyword || undefined,
      status: filter.status || undefined,
      offset: (page.value - 1) * limit.value,
      limit: limit.value,
    }
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    // 后端返回 [rows, total] 元组
    const res = await api.listRepairs(params)
    rows.value = Array.isArray(res) ? res[0] : res
    total.value = Array.isArray(res) ? res[1] : (res?.total || 0)
  } finally {
    loading.value = false
  }
}
watch([page, limit], load)

let searchTimer = null
function searchVehicle(q) {
  clearTimeout(searchTimer)
  if (!q) { vehicleOpts.value = []; return }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      const vs = await vehiclesApi.listVehicles({ keyword: q })
      vehicleOpts.value = vs
      const m = vs.find(v => v.id === form.vehicle_id)
      selectedVehicle.value = m ? m.plate_no : ''
    } finally { searching.value = false }
  }, 300)
}
function openCreate() {
  Object.assign(form, { vehicle_id: null, description: '', supplier_id: null })
  selectedVehicle.value = ''
  vehicleOpts.value = []
  dialog.value = true
}
async function save() {
  if (!form.vehicle_id) { ElMessage.warning('请选择车辆'); return }
  saving.value = true
  try {
    const r = await api.createRepair({ ...form, idem_key: `fe_${Date.now()}` })
    ElMessage.success(`提交成功：${r.order_no}`)
    dialog.value = false
    load()
  } finally { saving.value = false }
}

onMounted(async () => {
  load()
  suppliers.value = await systemApi.listSuppliers().catch(() => [])
})
</script>
