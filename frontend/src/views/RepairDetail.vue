<template>
  <div class="page-container">
    <div class="page-header">
      <h2>维修单详情</h2>
      <el-button @click="$router.push('/repairs')">返回列表</el-button>
    </div>

    <el-card v-loading="loading">
      <template #header>
        <div style="display:flex;justify-content:space-between">
          <div>
            <span style="font-size:18px;font-weight:600">{{ order.order_no }}</span>
            <el-tag :type="statusType(order.status)" style="margin-left:10px">{{ statusText(order.status) }}</el-tag>
            <span style="color:#94a3b8;margin-left:12px">第 {{ order.current_seq }} 步</span>
          </div>
          <div>
            <el-button v-if="canAdvance" type="success" @click="openAdvance('accept')">通过</el-button>
            <el-button v-if="canAdvance" type="danger" @click="openAdvance('reject')">驳回</el-button>
            <el-button v-if="canQuote" type="warning" @click="openClaim">报价</el-button>
            <el-button v-if="canCancel" @click="openCancel">取消</el-button>
          </div>
        </div>
      </template>

      <el-descriptions :column="3" border>
        <el-descriptions-item label="车牌">{{ order.vehicle?.plate_no || '—' }}</el-descriptions-item>
        <el-descriptions-item label="品牌">{{ order.vehicle?.brand || '—' }}</el-descriptions-item>
        <el-descriptions-item label="性质">{{ order.vehicle?.nature || '—' }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="3">{{ order.description || '—' }}</el-descriptions-item>
        <el-descriptions-item label="指派汽修厂">{{ order.target_supplier_name || '未指派' }}</el-descriptions-item>
        <el-descriptions-item label="报价人">{{ order.quote_owner_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ fmt(order.created_at) }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header><div style="font-weight:600">流转记录</div></template>
      <el-timeline>
        <el-timeline-item v-for="a in order.actions" :key="a.id" :timestamp="fmt(a.created_at)" placement="top">
          <div class="action-step">第 {{ a.seq }} 步 · <b>{{ a.action }}</b></div>
          <div v-if="a.note" class="action-note">{{ a.note }}</div>
        </el-timeline-item>
      </el-timeline>
      <div v-if="!order.actions?.length" class="empty-block">暂无流转</div>
    </el-card>

    <el-dialog v-model="advanceDialog" title="推进维修流程" width="440px">
      <el-input v-model="advanceNote" type="textarea" :rows="3" placeholder="备注（可留空）" />
      <template #footer>
        <el-button @click="advanceDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmAdvance">确认{{ advancingAction === 'accept' ? '通过' : '驳回' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '@/api/repairs'

const route = useRoute()
const loading = ref(false)
const order = ref({})
const advanceDialog = ref(false)
const advancingAction = ref('')
const advanceNote = ref('')

const auth = JSON.parse(localStorage.getItem('clglxt_user') || '{}')

const canAdvance = computed(() => order.value.status === 'processing')
const canQuote = computed(() => order.value.status === 'processing' && !order.value.quote_owner_id)
const canCancel = computed(() => order.value.status === 'processing')

function statusText(s) {
  return { processing: '处理中', completed: '已完成', rejected: '已驳回', cancelled: '已取消' }[s] || s
}
function statusType(s) {
  return { processing: 'warning', completed: 'success', rejected: 'danger', cancelled: 'info' }[s] || 'info'
}
function fmt(t) { return t ? t.replace('T', ' ').slice(0, 16) : '—' }

async function load() {
  loading.value = true
  try { order.value = await api.getRepair(route.params.id) }
  finally { loading.value = false }
}
function openAdvance(action) {
  advancingAction.value = action
  advanceNote.value = ''
  advanceDialog.value = true
}
async function confirmAdvance() {
  try {
    await api.advanceRepair(order.value.id, advancingAction.value, advanceNote.value, order.value.current_seq)
    ElMessage.success('已推进')
    advanceDialog.value = false
    load()
  } catch (e) {
    if (e.response?.status === 409) {
      ElMessage.warning(e.response.data?.detail || '已被他人推进，已刷新')
      load()
      advanceDialog.value = false
    }
  }
}
async function openClaim() {
  const ok = await ElMessageBox.confirm('确认接收报价？', '提示', { type: 'info' }).catch(() => false)
  if (!ok) return
  try { await api.claimQuote(order.value.id); ElMessage.success('已接收报价'); load() } catch {}
}
async function openCancel() {
  const { value: reason } = await ElMessageBox.prompt('取消原因（可留空）', '取消维修单', { inputType: 'textarea' }).catch(() => ({}))
  try { await api.cancelRepair(order.value.id, reason || ''); ElMessage.success('已取消'); load() } catch {}
}

onMounted(load)
</script>

<style scoped>
.action-step { font-size: 14px; }
.action-note { color: #64748b; font-size: 13px; margin-top: 2px; }
</style>
