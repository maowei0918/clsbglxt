<template>
  <div class="page-container">
    <div class="page-header"><h2>用车记录</h2></div>
    <div class="data-card">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="vehicle_plate" label="车牌" width="130" />
        <el-table-column prop="driver_name" label="驾驶员" width="120" />
        <el-table-column prop="start_time" label="出车时间">
          <template #default="{ row }">{{ fmt(row.start_time) }}</template>
        </el-table-column>
        <el-table-column prop="end_time" label="回车时间">
          <template #default="{ row }">{{ fmt(row.end_time) }}</template>
        </el-table-column>
        <el-table-column prop="purpose" label="事由" />
        <el-table-column prop="miles" label="里程" width="100" />
      </el-table>
      <div v-if="!rows.length && !loading" class="empty-block">暂无用车记录</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import * as api from '@/api/system'

const loading = ref(false)
const rows = ref([])
function fmt(t) { return t ? t.replace('T', ' ').slice(0, 16) : '—' }
async function load() {
  loading.value = true
  try {
    rows.value = await api.listVehicleUse()
  } finally { loading.value = false }
}
onMounted(load)
</script>
