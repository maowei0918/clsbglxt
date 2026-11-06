<!-- 日检照片上传组件（可复用）

用法：
  <PhotoUpload v-model="photos" />

照片上传：
- APP 端自动压缩（长边 1920 / JPEG 80%）
- 网页端直接上传，由服务端压缩
- 支持多选 + 进度条
- 上传成功后 v-model 得到数组 {id, url, thumb_url, width, height, size} -->
<template>
  <div class="photo-upload">
    <div v-if="photos.length" class="grid">
      <div v-for="(p, i) in photos" :key="p.id ?? i" class="card">
        <img :src="p.thumb_url || p.url" alt="日检照片" />
        <p>{{ Math.round((p.size || 0) / 1024) }} KB</p>
        <button type="button" @click="remove(i)">删除</button>
      </div>
    </div>

    <div class="actions">
      <button type="button" class="pick" :disabled="uploading" @click="pick">
        {{ uploading ? `上传中 ${progress}%` : '拍照 / 上传照片' }}
      </button>
      <input
        ref="inputRef"
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        hidden
        @change="onFilesPicked"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { uploadPhotos } from '@/api/photos'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  maxCount: { type: Number, default: 12 },
})
const emit = defineEmits(['update:modelValue', 'uploaded'])

const photos = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const inputRef = ref(null)
const uploading = ref(false)
const progress = ref(0)

function pick() {
  inputRef.value?.click()
}

async function onFilesPicked(e) {
  const picked = Array.from(e.target.files || [])
  e.target.value = '' // 允许重复选择同一文件
  if (!picked.length) return

  const remain = props.maxCount - photos.value.length
  if (remain <= 0) {
    ElMessage.warning(`最多上传 ${props.maxCount} 张`)
    return
  }
  const files = picked.slice(0, remain)

  // 前端基本校验
  for (const f of files) {
    if (!f.type.startsWith('image/')) {
      ElMessage.error('只能上传图片文件')
      return
    }
    if (f.size > 20 * 1024 * 1024) {
      ElMessage.error('单个文件不能超过 20MB')
      return
    }
  }

  uploading.value = true
  progress.value = 0
  try {
    const res = await uploadPhotos(files, (p) => (progress.value = p))
    const items = res.items || []
    if (items.length) {
      photos.value = [...photos.value, ...items]
      emit('uploaded', items)
      ElMessage.success(`成功上传 ${items.length} 张`)
    }
    if (res.errors?.length) {
      ElMessage.warning(`${res.errors.length} 张失败：${res.errors[0]?.error || ''}`)
    }
  } catch (err) {
    ElMessage.error('照片上传失败')
    console.error(err)
  } finally {
    uploading.value = false
    progress.value = 0
  }
}

function remove(index) {
  photos.value = photos.value.filter((_, i) => i !== index)
}
</script>

<style scoped>
.photo-upload { width: 100%; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
  background: #f8fafc;
}
.card img { width: 100%; height: 120px; object-fit: cover; display: block; }
.card p { text-align: center; font-size: 12px; color: #64748b; margin: 6px 0; }
.card button {
  width: 100%; border: none; background: #e11d48; color: #fff;
  padding: 6px; cursor: pointer; font-size: 13px;
}
.actions { display: flex; gap: 8px; }
.pick {
  border: 1px dashed #0ea5e9; background: #f0f9ff; color: #0284c7;
  padding: 10px 18px; border-radius: 8px; cursor: pointer; font-size: 14px;
}
.pick:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
