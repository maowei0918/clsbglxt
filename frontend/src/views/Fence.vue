<template>
  <div class="page-container">
    <div class="page-header"><h2>围栏告警</h2></div>
    <div class="data-card">
      <el-table :data="alerts" v-loading="loading" stripe>
        <el-table-column prop="plate_no" label="车牌" width="130" />
        <el-table-column prop="alert_time" label="告警时间">
          <template #default="{ row }">{{ fmt(row.alert_time) }}</template>
        </el-table-column>
        <el-table-column prop="address" label="位置" />
        <el-table-column prop="distance_m" label="超出(米)" width="100" />
      </el-table>
      <div v-if="!alerts.length && !loading" class="empty-block">暂无告警</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import * as api from '@/api/system'

const loading = ref(false)
const alerts = ref([])
function fmt(t) { return t ? t.replace('T', ' ').slice(0, 16) : '—' }
async function load() {
  loading.value = true
  try { alerts.value = await api.listFenceAlerts() } finally { loading.value = false }
}
onMounted(load)
</script>
