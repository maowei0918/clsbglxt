/* clglxt Service Worker
 *
 * 策略：
 *  - 静态资源（/assets/*）：Cache First（带版本号，长期缓存）
 *  - 导航请求（HTML）：Network First，失败回退 index.html（SPA 离线可用）
 *  - API 请求（/api/*）：一律走网络，不缓存（数据必须实时）
 *  - 照片（/api/daily-check/photos/*）：Stale While Revalidate（省流量）
 */

const VERSION = 'clglxt-v1'
const STATIC_CACHE = `${VERSION}-static`
const PHOTO_CACHE = `${VERSION}-photo`

const PRECACHE = ['/', '/index.html', '/manifest.json']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.startsWith(VERSION))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)

  // 只处理同源 GET
  if (request.method !== 'GET' || url.origin !== self.location.origin) return

  // 照片：Stale While Revalidate
  if (url.pathname.startsWith('/api/daily-check/photos/')) {
    event.respondWith(
      caches.open(PHOTO_CACHE).then(async (cache) => {
        const cached = await cache.match(request)
        const fetching = fetch(request)
          .then((res) => {
            if (res.ok) cache.put(request, res.clone())
            return res
          })
          .catch(() => cached)
        return cached || fetching
      })
    )
    return
  }

  // 其他 API：纯网络，不缓存
  if (url.pathname.startsWith('/api/')) return

  // 静态资源：Cache First
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((res) => {
            if (res.ok) {
              const copy = res.clone()
              caches.open(STATIC_CACHE).then((c) => c.put(request, copy))
            }
            return res
          })
      )
    )
    return
  }

  // 导航请求：Network First → 离线回退 index.html
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match('/index.html').then((r) => r || new Response('离线', { status: 503 }))
      )
    )
  }
})
