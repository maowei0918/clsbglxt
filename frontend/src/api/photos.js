import http from './http'

/**
 * 日检照片 API
 *
 * 设计要点：
 *  - APP 内（Android WebView）优先走原生压缩，再上传（节省流量 + 快）
 *  - 网页端直接上传原图，由服务端 Pillow 兜底压缩（长边 1920 / JPEG 78）
 *  - 上传支持多选 + 进度回调
 */

/** 是否运行在 Android APP 壳内（由 MainActivity 注入 window.Android） */
export function isNativeApp() {
  return typeof window !== 'undefined' && !!window.Android
}

/**
 * APP 端压缩：调用原生 compressImage()，失败则原样返回。
 * @param {File} file
 * @param {number} quality 30-95
 * @returns {Promise<Blob>}
 */
export async function compressInApp(file, quality = 80) {
  if (!isNativeApp() || !window.Android.compressImage) return file
  try {
    const base64 = await fileToBase64(file)
    const out = window.Android.compressImage(base64, quality)
    if (!out) return file
    return base64ToBlob(out, 'image/jpeg')
  } catch (e) {
    console.warn('APP 压缩失败，回退原图', e)
    return file
  }
}

/**
 * 上传照片
 * @param {File[]} files
 * @param {(percent:number)=>void} onProgress
 * @returns {Promise<{items:Array,errors:Array}>}
 */
export async function uploadPhotos(files, onProgress) {
  const form = new FormData()
  const prepared = isNativeApp()
    ? await Promise.all(Array.from(files).map((f) => compressInApp(f)))
    : Array.from(files)

  prepared.forEach((blob, i) => {
    const name = files[i]?.name || `photo_${Date.now()}_${i}.jpg`
    form.append('files', blob, name)
  })

  return http.post('/daily-check/photos', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000, // 大图 + 弱网，放宽超时
    onUploadProgress: (e) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    },
  })
}

/** 读取照片（size='thumb' 取缩略图） */
export function photoUrl(id, size = '') {
  return size === 'thumb'
    ? `/api/daily-check/photos/${id}?size=thumb`
    : `/api/daily-check/photos/${id}`
}

/** 删除照片 */
export function deletePhoto(id) {
  return http.delete(`/daily-check/photos/${id}`)
}

// ── 工具 ──

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function base64ToBlob(b64, mime) {
  const bin = atob(b64)
  const len = bin.length
  const buf = new Uint8Array(len)
  for (let i = 0; i < len; i++) buf[i] = bin.charCodeAt(i)
  return new Blob([buf], { type: mime })
}
