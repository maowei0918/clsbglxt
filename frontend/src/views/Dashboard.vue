<template>
  <div class="page-container">
    <div class="page-header"><h2>仪表盘</h2></div>

    <el-row :gutter="16">
      <el-col :span="6" v-for="k in cards" :key="k.label">
        <div class="stat-card">
          <div class="stat-icon">{{ k.icon }}</div>
          <div>
            <div class="stat-num">{{ k.value }}</div>
            <div class="stat-label">{{ k.label }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="14">
        <div class="data-card">
          <div class="card-title">近期维修单</div>
          <el-table :data="repairs" stripe>
            <el-table-column prop="order_no" label="单号" width="140" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="提报时间">
              <template #default="{ row }">{{ fmt(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <div v-if="!repairs.length" class="empty-block">暂无维修单</div>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="data-card">
          <div class="card-title">围栏告警</div>
          <el-table :data="alerts" stripe>
            <el-table-column prop="plate_no" label="车牌" width="130" />
            <el-table-column prop="alert_time" label="时间">
              <template #default="{ row }">{{ fmt(row.alert_time) }}</template>
            </el-table-column>
            <el-table-column prop="distance_m" label="超出" width="80" />
          </el-table>
          <div v-if="!alerts.length" class="empty-block">暂无告警</div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import * as api from '@/api/system'
import * as repairsApi from '@/api/repairs'
import * as vehiclesApi from '@/api/vehicles'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const repairs = ref([])
const alerts = ref([])
const stat = ref({ vehicles: 0, orders: 0, active: 0, today: 0 })

function fmt(t) { return t ? t.replace('T', ' ').slice(0, 16) : '—' }
function statusType(s) {
  return { processing: 'warning', completed: 'success', rejected: 'danger', cancelled: 'info' }[s] || 'info'
}
function statusText(s) {
  return { processing: '处理中', completed: '已完工', rejected: '已拒', cancelled: '已取消' }[s] || s
}

async function load() {
  const [rv, al] = await Promise.allSettled([repairsApi.listRepairs({ limit: 10 }), api.listFenceAlerts({ limit: 10 })])
  repairs.value = rv.status === 'fulfilled' ? (rv.value[0] || []) : []
  alerts.value = al.status === 'fulfilled' ? (al.value || []) : []

  const [vv, rt] = await Promise.allSettled([vehiclesApi.listVehicles(), repairsApi.listRepairs({ limit: 100 })])
  stat.value.vehicles = vv.status === 'fulfilled' ? (vv.value || []).length : 0
  stat.value.orders = rt.status === 'fulfilled' ? (rt.value?.[1] || rt.value?.[0]?.length || 0) : 0
  stat.value.active = rt.status === 'fulfilled'
    ? (rt.value?.[0] || []).filter(r => r.status === 'processing').length : 0
  stat.value.today = new Date().toISOString().slice(0, 10)
}

const cards = ref([])
watch(stat, (s) => {
  cards.value = [
    { label: '车辆总数', value: s.vehicles, icon: '🚗' },
    { label: '维修单', value: s.orders, icon: '🔧' },
    { label: '处理中', value: s.active, icon: '🔄' },
    { label: '今日', value: s.today, icon: '📅' },
  ]
}, { immediate: true })

onMounted(load)
</script>

<style scoped>
.stat-card {
  background: #fff; border-radius: 8px; padding: 20px;
  display: flex; align-items: center; gap: 14px;
}
.stat-icon { font-size: 32px; }
.stat-num { font-size: 26px; font-weight: 700; color: #1f2937; }
.stat-label { font-size: 13px; color: #6b7280; }
.card-title { padding: 14px 16px; font-size: 15px; font-weight: 600; border-bottom: 1px solid #f0f0f0; }
</style>
