<template>
  <div class="page-container">
    <div class="page-header"><h2>日检</h2></div>
    <div class="filter-card">
      <el-form :inline="true">
        <el-form-item label="日期"><el-date-picker v-model="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-button type="primary" @click="load">查询</el-button>
      </el-form>
    </div>
    <div class="data-card">
      <el-table :data="records" v-loading="loading" stripe>
        <el-table-column prop="vehicle_plate" label="车牌" width="130" />
        <el-table-column prop="template_name" label="模板" width="140" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="row.ai_result === 'pass' ? 'success' : 'warning'" size="small">
              {{ row.ai_result === 'pass' ? '通过' : '待审' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div v-if="!records.length && !loading" class="empty-block">暂无日检记录</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import * as api from '@/api/system'

const loading = ref(false)
const records = ref([])
const date = ref(new Date().toISOString().slice(0, 10))

function fmt(t) { return t ? t.replace('T', ' ').slice(0, 16) : '—' }
async function load() {
  loading.value = true
  try {
    const res = await api.listDailyRecords({ date: date.value })
    records.value = Array.isArray(res) ? res : (res?.items || res[0] || [])
  } finally { loading.value = false }
}
onMounted(load)
</script>
