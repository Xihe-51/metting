<template>
  <div class="recordings-page">
    <div class="header">
      <el-button @click="$router.push('/')" :icon="'ArrowLeft'">返回首页</el-button>
      <h1>录制回放</h1>
    </div>

    <div class="search-bar">
      <el-input v-model="searchNo" placeholder="输入会议号" maxlength="6" style="width: 200px" />
      <el-button type="primary" @click="fetchRecordings" :loading="loading">查询</el-button>
    </div>

    <div class="list" v-if="recordings.length > 0">
      <div class="item" v-for="r in recordings" :key="r.id">
        <div class="info">
          <span class="name">{{ r.file_name }}</span>
          <span class="meta">{{ formatSize(r.file_size) }} | {{ formatDuration(r.duration) }}</span>
          <el-tag :type="r.status === 'completed' ? 'success' : 'warning'" size="small">
            {{ r.status === 'completed' ? '已完成' : '录制中' }}
          </el-tag>
        </div>
        <div class="actions">
          <el-button size="small" type="primary" @click="playRecording(r.id)" v-if="r.status === 'completed'">
            播放
          </el-button>
          <el-button size="small" @click="downloadRecording(r.id)" v-if="r.status === 'completed'">
            下载
          </el-button>
        </div>
      </div>
    </div>

    <div class="empty" v-else-if="searched && !loading">
      暂无录制记录
    </div>

    <!-- 播放器 -->
    <el-dialog v-model="showPlayer" title="录制回放" width="800px" @close="stopPlay">
      <video ref="playerRef" controls autoplay style="width: 100%; max-height: 500px;"></video>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const API_BASE = 'http://localhost:8000/api/v1'

const searchNo = ref('')
const loading = ref(false)
const searched = ref(false)
const recordings = ref<Array<{
  id: number; file_name: string; file_size: number; duration: number; status: string
}>>([])
const showPlayer = ref(false)
const playerRef = ref<HTMLVideoElement>()

const getAuthHeaders = () => ({
  headers: { Authorization: `Bearer ${localStorage.getItem('token')}` }
})

const fetchRecordings = async () => {
  if (!searchNo.value) {
    ElMessage.warning('请输入会议号')
    return
  }
  loading.value = true
  searched.value = true
  try {
    const res = await axios.get(`${API_BASE}/meetings/${searchNo.value}/recordings`, getAuthHeaders())
    recordings.value = res.data.data || []
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '查询失败')
  } finally {
    loading.value = false
  }
}

const playRecording = (id: number) => {
  showPlayer.value = true
  setTimeout(() => {
    if (playerRef.value) {
      playerRef.value.src = `${API_BASE}/recordings/${id}/play`
      playerRef.value.play()
    }
  }, 100)
}

const stopPlay = () => {
  if (playerRef.value) {
    playerRef.value.pause()
    playerRef.value.src = ''
  }
}

const downloadRecording = (id: number) => {
  window.open(`${API_BASE}/recordings/${id}/download`, '_blank')
}

const formatSize = (bytes: number) => {
  if (!bytes) return '0 B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

const formatDuration = (seconds: number) => {
  if (!seconds) return '0秒'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}分${s}秒` : `${s}秒`
}
</script>

<style scoped>
.recordings-page {
  min-height: 100vh;
  background: linear-gradient(135deg, #1A535C 0%, #4ECDC4 100%);
  padding: 20px 40px;
}

.header {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 30px;
}

.header h1 {
  color: white;
  margin: 0;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.list {
  max-width: 700px;
}

.item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255,255,255,0.9);
  border-radius: 10px;
  padding: 14px 20px;
  margin-bottom: 10px;
}

.info {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.name {
  font-weight: bold;
  color: #333;
}

.meta {
  color: #999;
  font-size: 13px;
}

.actions {
  display: flex;
  gap: 8px;
}

.empty {
  text-align: center;
  color: rgba(255,255,255,0.7);
  margin-top: 40px;
  font-size: 16px;
}
</style>