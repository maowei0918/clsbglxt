import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import SetupGuide from '@/components/SetupGuide.vue'
import './assets/main.css'

// ── 启动前探针 ──────────────────────────────────────────────
// 探 /api/healthz：后端正常 → 挂载完整 SPA；后端挂了 → 挂载 SetupGuide。
// nginx 把 / 透传给后端，后端托管 index.html，所以这个探针是唯一的降级入口。
// 5 秒超时：后端半死（TCP 连上但不响应）时快速降级，避免白屏 75 秒。
async function probeHealth() {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), 5000)
  try {
    const r = await fetch('/api/healthz', { cache: 'no-store', signal: ctl.signal })
    return r.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}

probeHealth().then((isUp) => {
  const app = createApp(isUp ? App : SetupGuide)

  if (isUp) {
    // 只有完整 SPA 才需要这套全局注册
    for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
      app.component(key, component)
    }
    app.use(createPinia())
    app.use(router)
  }

  app.use(ElementPlus)
  app.mount('#app')

  // PWA Service Worker（只在完整 SPA 时注册）
  if (isUp && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch((e) => {
        console.warn('SW 注册失败（可忽略，仅影响离线缓存）', e)
      })
    })
  }
})
