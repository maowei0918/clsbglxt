<template>
  <div class="guide-wrap">
    <div class="guide-card">
      <div class="icon">🚗</div>
      <h2>服务暂时不可用</h2>
      <p>检测到后端服务未就绪（可能未启动、重启中，或数据库未连接）。</p>
      <p>请在服务器终端执行以下命令启动/重启：</p>
      <pre>cd /path/to/clglxt/server
bash deploy/deploy.sh</pre>
      <el-button type="primary" :loading="checking" @click="check">重新检测</el-button>
      <p class="tip">首次部署需先安装 PostgreSQL 与 Redis；启动约需 10-30 秒</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const checking = ref(false)

function check() {
  checking.value = true
  fetch('/api/healthz', { cache: 'no-store' })
    .then(r => {
      if (r.ok) { location.replace('/login') }
      else { checking.value = false; alert('服务仍未就绪，请稍后重试') }
    })
    .catch(() => {
      checking.value = false
      alert('无法连接服务器，请确认服务已启动')
    })
}
</script>

<style scoped>
.guide-wrap {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #0c4a6e 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.guide-card {
  background: #fff;
  border-radius: 16px;
  padding: 36px 40px;
  width: 100%;
  max-width: 520px;
  box-shadow: 0 25px 60px rgba(0, 0, 0, 0.35);
}
.icon { font-size: 42px; margin-bottom: 14px; }
h2 { font-size: 20px; margin-bottom: 10px; color: #1e293b; }
p { color: #64748b; font-size: 14px; margin-bottom: 18px; line-height: 1.6; }
pre {
  background: #0f172a;
  color: #22d3ee;
  padding: 14px 18px;
  border-radius: 8px;
  font-size: 13px;
  font-family: monospace;
  overflow-x: auto;
  margin-bottom: 20px;
  white-space: pre-wrap;
  word-break: break-all;
}
.tip { color: #94a3b8; font-size: 12px; margin-top: 16px; margin-bottom: 0; }
</style>
