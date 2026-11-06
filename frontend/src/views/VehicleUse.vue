<template>
  <div class="page-container">
    <div class="page-header">
      <h2>临时用车备案</h2>
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="openCreate">新增备案</el-button>
    </div>

    <div class="filter-card">
      <el-form :inline="true" @submit.prevent="load">
        <el-form-item label="车牌">
          <el-input v-model="filter.keyword" placeholder="输入车牌号" clearable style="width:180px" />
        </el-form-item>
        <el-form-item label="开始日期">
          <el-date-picker
            v-model="filter.start_date_from"
            type="date"
            placeholder="从"
            value-format="YYYY-MM-DD"
            style="width:150px"
          />
        </el-form-item>
        <el-form-item label="至">
          <el-date-picker
            v-model="filter.start_date_to"
            type="date"
            placeholder="至"
            value-format="YYYY-MM-DD"
            style="width:150px"
          />
        </el-form-item>
        <el-form-item label="归档">
          <el-switch
            v-model="filter.include_archived"
            active-text="含已归档"
            inline-prompt
            @change="load"
          />
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
        <el-table-column prop="vehicle_type_name" label="类型" width="110">
          <template #default="{ row }">{{ row.vehicle_type_name || '—' }}</template>
        </el-table-column>
        <el-table-column prop="department_name" label="部门" width="130" />
        <el-table-column prop="nature" label="性质" width="90">
          <template #default="{ row }">{{ row.nature || '—' }}</template>
        </el-table-column>
        <el-table-column label="用车期间" width="200">
          <template #default="{ row }">
            {{ row.start_date || '—' }} ~ {{ row.end_date || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="事由" min-width="120">
          <template #default="{ row }">{{ row.reason || '—' }}</template>
        </el-table-column>
        <el-table-column prop="park_location" label="停放地点" min-width="120">
          <template #default="{ row }">{{ row.park_location || '—' }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="110">
          <template #default="{ row }">{{ row.remark || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.archived" type="info" size="small">已归档</el-tag>
            <el-tag v-else type="success" size="small">在用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <template v-if="canManage">
              <el-button
                v-if="row.archived"
                link
                type="primary"
                size="small"
                @click="restore(row)"
              >恢复</el-button>
              <el-button
                v-else
                link
                type="warning"
                size="small"
                @click="archive(row)"
              >归档</el-button>
            </template>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!rows.length && !loading" class="empty-block">
        {{ filter.include_archived ? '暂无记录' : '暂无未归档的用车记录' }}
      </div>
    </div>

    <el-dialog v-model="dialog" title="新增临时用车备案" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="车牌号" required>
          <el-input v-model="form.plate_no" placeholder="如 鲁B12345" />
        </el-form-item>
        <el-form-item label="开始日期" required>
          <el-date-picker
            v-model="form.start_date"
            type="date"
            placeholder="请选择"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="结束日期" required>
          <el-date-picker
            v-model="form.end_date"
            type="date"
            placeholder="请选择"
            value-format="YYYY-MM-DD"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="事由">
          <el-input v-model="form.reason" placeholder="选填" />
        </el-form-item>
        <el-form-item label="停放地点">
          <el-input v-model="form.park_location" placeholder="选填" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" placeholder="选填" />
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
import { useAuthStore } from '@/stores/auth'
import * as api from '@/api/vehicleUse'

const auth = useAuthStore()
// 归档/恢复/新增/删除需 vehicle:manage；仅查看看 vehicle_use:view
const canManage = () => auth.can('vehicle:manage')

const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const rows = ref([])

const filter = reactive({
  keyword: '',
  start_date_from: '',
  start_date_to: '',
  include_archived: false,
})
const form = reactive({
  plate_no: '',
  start_date: '',
  end_date: '',
  reason: '',
  park_location: '',
  remark: '',
})

async function load() {
  loading.value = true
  try {
    rows.value = await api.listVehicleUse({
      keyword: filter.keyword,
      start_date_from: filter.start_date_from || undefined,
      start_date_to: filter.start_date_to || undefined,
      include_archived: filter.include_archived || undefined,
    })
  } finally {
    loading.value = false
  }
}

function reset() {
  filter.keyword = ''
  filter.start_date_from = ''
  filter.start_date_to = ''
  filter.include_archived = false
  load()
}

function openCreate() {
  Object.assign(form, {
    plate_no: '',
    start_date: '',
    end_date: '',
    reason: '',
    park_location: '',
    remark: '',
  })
  dialog.value = true
}

async function save() {
  if (!form.plate_no.trim()) return ElMessage.warning('请填写车牌号')
  if (!form.start_date || !form.end_date) return ElMessage.warning('请选择开始/结束日期')
  if (form.end_date < form.start_date) return ElMessage.warning('结束日期不能早于开始日期')
  saving.value = true
  try {
    await api.createVehicleUse({
      plate_no: form.plate_no.trim().toUpperCase(),
      start_date: form.start_date,
      end_date: form.end_date,
      reason: form.reason || '',
      park_location: form.park_location || '',
      remark: form.remark || '',
    })
    ElMessage.success('备案成功')
    dialog.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function archive(row) {
  await ElMessageBox.confirm(
    `确定归档 ${row.plate_no} 的这条记录？归档后默认不展示，可随时恢复。`,
    '归档确认',
    { type: 'warning' },
  )
  await api.archiveVehicleUse(row.id)
  ElMessage.success('已归档')
  load()
}

async function restore(row) {
  await api.restoreVehicleUse(row.id)
  ElMessage.success('已恢复')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(
    `确定删除 ${row.plate_no} 的这条记录？删除不可恢复。`,
    '删除确认',
    { type: 'warning' },
  )
  await api.deleteVehicleUse(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>