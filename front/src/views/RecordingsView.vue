<template>
  <div class="swiss">
    <!-- Header -->
    <header class="header">
      <div class="header-grid">
        <div class="header-brand">
          <router-link to="/" class="logo">LAN Video</router-link>
          <span class="logo-divider" aria-hidden="true"></span>
          <span class="subtitle">录制回放</span>
        </div>
        <nav class="header-nav">
          <router-link to="/" class="nav-link">首页</router-link>
          <span class="nav-sep">/</span>
          <button class="nav-link nav-link--signout" @click="$router.push('/')">返回</button>
        </nav>
      </div>
    </header>

    <main class="main">
      <!-- Search -->
      <section class="search-section">
        <div class="search-row">
          <input
            v-model="searchNo"
            class="search-input"
            placeholder="输入会议号"
            maxlength="6"
            @keyup.enter="fetchRecordings"
          />
          <button class="btn-primary" @click="fetchRecordings" :disabled="loading">
            {{ loading ? '查询中...' : '查询' }}
          </button>
        </div>
      </section>

      <!-- Recordings List -->
      <section class="recording-list" v-if="recordings.length > 0">
        <article class="recording-card" v-for="r in recordings" :key="r.id">
          <div class="card-left">
            <div class="recording-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"/>
                <polygon points="10,8 16,12 10,16"/>
              </svg>
            </div>
            <div class="recording-info">
              <span class="recording-name">{{ r.file_name }}</span>
              <span class="recording-meta">{{ formatSize(r.file_size) }} &middot; {{ formatDuration(r.duration) }}</span>
            </div>
            <span class="status-tag" :class="r.status">
              {{ r.status === 'completed' ? '已完成' : '录制中' }}
            </span>
          </div>
          <div class="card-right">
            <button
              v-if="r.status === 'completed'"
              class="btn-action btn-play"
              @click="playRecording(r)"
            >
              播放
            </button>
            <button
              v-if="r.status === 'completed'"
              class="btn-action"
              @click="downloadRecording(r)"
            >
              下载
            </button>
          </div>
        </article>
      </section>

      <!-- Empty -->
      <section class="empty-state" v-else-if="searched && !loading">
        <p class="empty-text">暂无录制记录</p>
        <p class="empty-hint">输入会议号查询该会议的录制文件</p>
      </section>
    </main>

    <!-- Player Dialog -->
    <el-dialog v-model="showPlayer" title="录制回放" width="800px" @close="stopPlay" class="player-dialog">
      <video ref="playerRef" controls autoplay class="player-video"></video>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const API_BASE = '/api/v1'

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

let playerObjectUrl = ''
const playRecording = async (rec: { id: number; file_name: string }) => {
  try {
    if (playerObjectUrl) URL.revokeObjectURL(playerObjectUrl)
    const res = await axios.get(`${API_BASE}/recordings/${rec.id}/play`, {
      ...getAuthHeaders(),
      responseType: 'blob'
    })
    playerObjectUrl = URL.createObjectURL(res.data)
    showPlayer.value = true
    setTimeout(() => {
      if (playerRef.value) {
        playerRef.value.src = playerObjectUrl
        playerRef.value.play()
      }
    }, 100)
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '播放失败')
  }
}

const stopPlay = () => {
  if (playerRef.value) {
    playerRef.value.pause()
    playerRef.value.src = ''
    playerRef.value.load()
  }
  if (playerObjectUrl) {
    URL.revokeObjectURL(playerObjectUrl)
    playerObjectUrl = ''
  }
}

const downloadRecording = async (rec: { id: number; file_name: string }) => {
  try {
    const res = await axios.get(`${API_BASE}/recordings/${rec.id}/download`, {
      ...getAuthHeaders(),
      responseType: 'blob'
    })
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = rec.file_name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '下载失败')
  }
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
/* ========== 基础布局 ========== */
.swiss {
  min-height: 100vh;
  background: #F2F0EB;
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  color: #2C2C2C;
}

/* ========== Header ========== */
.header {
  padding: 16px 40px;
  border-bottom: 1px solid #E0DCD5;
  background: #FAFAF8;
}

.header-grid {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: #2C2C2C;
  text-decoration: none;
}

.logo-divider {
  width: 1px;
  height: 18px;
  background: #C8C3B8;
}

.subtitle {
  font-size: 14px;
  color: #8C8C8C;
  font-weight: 400;
}

.header-nav {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #8C8C8C;
}

.nav-link {
  color: #5A7D9A;
  text-decoration: none;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  padding: 0;
}

.nav-link:hover {
  color: #4A6D8A;
}

.nav-sep {
  color: #C8C3B8;
}

.nav-link--signout {
  color: #B0A090;
}

.nav-link--signout:hover {
  color: #8C7A6A;
}

/* ========== Main ========== */
.main {
  max-width: 800px;
  margin: 0 auto;
  padding: 40px 24px;
}

/* ========== Search ========== */
.search-section {
  margin-bottom: 32px;
}

.search-row {
  display: flex;
  gap: 12px;
}

.search-input {
  flex: 1;
  max-width: 220px;
  height: 42px;
  padding: 0 16px;
  border: 1px solid #D8D3C8;
  border-radius: 6px;
  background: #FAFAF8;
  font-size: 14px;
  font-family: inherit;
  color: #2C2C2C;
  outline: none;
  transition: border-color 0.2s;
}

.search-input:focus {
  border-color: #5A7D9A;
}

.search-input::placeholder {
  color: #B0A898;
}

.btn-primary {
  height: 42px;
  padding: 0 24px;
  border: none;
  border-radius: 6px;
  background: #5A7D9A;
  color: #FAFAF8;
  font-size: 14px;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-primary:hover {
  background: #4A6D8A;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* ========== Recording Cards ========== */
.recording-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.recording-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #FAFAF8;
  border: 1px solid #E8E3D8;
  border-radius: 8px;
  padding: 16px 20px;
  transition: box-shadow 0.2s;
}

.recording-card:hover {
  box-shadow: 0 2px 12px rgba(0,0,0,0.04);
}

.card-left {
  display: flex;
  align-items: center;
  gap: 14px;
}

.recording-icon {
  color: #5A7D9A;
  flex-shrink: 0;
}

.recording-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.recording-name {
  font-size: 14px;
  font-weight: 600;
  color: #2C2C2C;
}

.recording-meta {
  font-size: 12px;
  color: #A09888;
}

.status-tag {
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 10px;
  font-weight: 500;
}

.status-tag.completed {
  background: #E8EDE8;
  color: #5A8A6A;
}

.status-tag.recording {
  background: #F5E8D8;
  color: #B08050;
}

.card-right {
  display: flex;
  gap: 8px;
}

.btn-action {
  height: 34px;
  padding: 0 16px;
  border: 1px solid #D8D3C8;
  border-radius: 6px;
  background: #FAFAF8;
  font-size: 13px;
  font-family: inherit;
  color: #5A5A5A;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-action:hover {
  border-color: #B0A898;
  color: #2C2C2C;
}

.btn-play {
  background: #5A7D9A;
  border-color: #5A7D9A;
  color: #FAFAF8;
}

.btn-play:hover {
  background: #4A6D8A;
  border-color: #4A6D8A;
  color: #FAFAF8;
}

/* ========== Empty State ========== */
.empty-state {
  text-align: center;
  padding: 60px 0;
}

.empty-text {
  font-size: 16px;
  color: #A09888;
  margin: 0 0 8px;
}

.empty-hint {
  font-size: 13px;
  color: #C8C3B8;
  margin: 0;
}

/* ========== Player ========== */
.player-video {
  width: 100%;
  max-height: 500px;
  border-radius: 6px;
  background: #000;
}
</style>