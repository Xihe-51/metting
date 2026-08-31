<template>
  <div class="swiss">
    <!-- Header -->
    <header class="header">
      <div class="header-grid">
        <div class="header-brand">
          <router-link to="/" class="logo">LAN Video</router-link>
          <span class="logo-divider" aria-hidden="true"></span>
          <span class="subtitle">审计日志</span>
        </div>
        <nav class="header-nav">
          <router-link to="/" class="nav-link">首页</router-link>
          <span class="nav-sep">/</span>
          <router-link to="/recordings" class="nav-link">录制文件</router-link>
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
            @keyup.enter="fetchLogs"
          />
          <button class="btn-primary" @click="fetchLogs" :disabled="loading">
            {{ loading ? '查询中...' : '查询' }}
          </button>
        </div>
        <p class="search-hint">仅会议创建者可查看审计日志</p>
      </section>

      <!-- Loading -->
      <section class="loading-state" v-if="loading">
        <p class="empty-text">加载中...</p>
      </section>

      <!-- Timeline -->
      <section class="timeline" v-else-if="logs.length > 0">
        <div class="timeline-item" v-for="(log, i) in logs" :key="log.id">
          <div class="timeline-dot" :class="log.action"></div>
          <div class="timeline-line" v-if="i < logs.length - 1"></div>
          <div class="timeline-card">
            <div class="tl-header">
              <span class="tl-action">{{ actionLabel(log.action) }}</span>
              <span class="tl-time">{{ formatTime(log.created_at) }}</span>
            </div>
            <div class="tl-body">
              <span class="tl-user">{{ log.user_name }}</span>
              <span class="tl-detail" v-if="log.details">{{ detailText(log) }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- Empty -->
      <section class="empty-state" v-else-if="searched && !loading">
        <p class="empty-text">暂无审计日志</p>
        <p class="empty-hint">输入你创建的会议号进行查询</p>
      </section>
    </main>
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
const logs = ref<Array<{
  id: number; action: string; user_name: string; details: string | null; created_at: string
}>>([])

const actionLabels: Record<string, string> = {
  create: '创建会议',
  join: '加入会议',
  end: '结束会议',
  record_start: '开始录制',
  record_stop: '停止录制',
  mute_all: '全体静音',
  kick: '踢出用户',
  admit: '准入等候室',
  reject: '拒绝等候室',
  leave: '离开会议',
}

const actionLabel = (action: string) => actionLabels[action] || action

const getAuthHeaders = () => ({
  headers: { Authorization: `Bearer ${localStorage.getItem('token')}` }
})

const fetchLogs = async () => {
  if (!searchNo.value) {
    ElMessage.warning('请输入会议号')
    return
  }
  loading.value = true
  searched.value = true
  try {
    const res = await axios.get(`${API_BASE}/meetings/${searchNo.value}/audit_logs`, getAuthHeaders())
    logs.value = res.data.data || []
    if (logs.value.length === 0) {
      ElMessage.info('暂无审计日志')
    }
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '查询失败')
  } finally {
    loading.value = false
  }
}

const detailText = (log: { action: string; details: string | null }) => {
  if (!log.details) return ''
  try {
    const d = JSON.parse(log.details)
    if (log.action === 'kick' || log.action === 'admit' || log.action === 'reject') {
      return `目标: ${d.target_name}`
    }
    if (log.action === 'join') {
      return d.status === 'waiting' ? '进入等候室' : '直接加入'
    }
    if (log.action === 'create') {
      return `会议号: ${d.meeting_no}`
    }
    return ''
  } catch {
    return ''
  }
}

const formatTime = (iso: string) => {
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
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

/* ========== Main ========== */
.main {
  max-width: 640px;
  margin: 0 auto;
  padding: 40px 24px;
}

/* ========== Search ========== */
.search-section {
  margin-bottom: 36px;
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

.search-hint {
  font-size: 12px;
  color: #B0A898;
  margin: 8px 0 0;
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

/* ========== Timeline ========== */
.timeline {
  padding-left: 20px;
}

.timeline-item {
  position: relative;
  padding-left: 28px;
  padding-bottom: 20px;
}

.timeline-dot {
  position: absolute;
  left: -5px;
  top: 6px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #C8C3B8;
  border: 2px solid #F2F0EB;
  z-index: 1;
}

.timeline-dot.create,
.timeline-dot.end {
  background: #5A7D9A;
}

.timeline-dot.kick,
.timeline-dot.reject {
  background: #C07060;
}

.timeline-dot.record_start,
.timeline-dot.record_stop {
  background: #D0A060;
}

.timeline-dot.admit {
  background: #5A9A6A;
}

.timeline-line {
  position: absolute;
  left: -1px;
  top: 18px;
  bottom: 0;
  width: 1px;
  background: #E0DCD5;
}

.timeline-card {
  background: #FAFAF8;
  border: 1px solid #E8E3D8;
  border-radius: 8px;
  padding: 14px 18px;
}

.tl-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.tl-action {
  font-size: 14px;
  font-weight: 600;
  color: #2C2C2C;
}

.tl-time {
  font-size: 12px;
  color: #B0A898;
  font-variant-numeric: tabular-nums;
}

.tl-body {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tl-user {
  font-size: 13px;
  color: #5A7D9A;
  font-weight: 500;
}

.tl-detail {
  font-size: 12px;
  color: #A09888;
}

/* ========== Empty / Loading ========== */
.loading-state,
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
</style>