<template>
  <div class="swiss">
    <!-- Header -->
    <header class="header">
      <div class="header-grid">
        <div class="header-brand">
          <h1 class="logo">LAN Video</h1>
          <span class="logo-divider" aria-hidden="true"></span>
          <span class="subtitle">个人空间</span>
        </div>
        <nav class="header-nav">
          <router-link to="/" class="nav-link">首页</router-link>
          <span class="nav-sep">/</span>
          <span class="user-label">{{ profile.display_name }}</span>
          <button class="nav-link nav-link--signout" @click="handleLogout">退出登录</button>
        </nav>
      </div>
    </header>

    <!-- Cover + Avatar Section -->
    <section class="cover-section">
      <div class="cover-wrapper">
        <div
          class="cover-img"
          :style="{ backgroundImage: profile.cover_url ? `url(${API_STATIC}${profile.cover_url})` : 'none' }"
          @click="triggerUpload('cover')"
        >
          <div class="cover-overlay">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
              <circle cx="12" cy="13" r="4" />
            </svg>
            <span>更换封面</span>
          </div>
        </div>
        <div class="avatar-row">
          <div class="avatar-wrapper" @click="triggerUpload('avatar')">
            <div class="avatar-img" :style="{ backgroundImage: profile.avatar_url ? `url(${API_STATIC}${profile.avatar_url})` : 'none' }">
              <span v-if="!profile.avatar_url" class="avatar-placeholder">{{ (profile.display_name || '?')[0] }}</span>
            </div>
            <div class="avatar-overlay">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                <circle cx="12" cy="13" r="4" />
              </svg>
            </div>
          </div>
          <div class="user-info">
            <div class="name-row">
              <span class="display-name">{{ profile.display_name }}</span>
              <span v-if="profile.verified" class="verified-badge" title="已认证">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#5A7D9A" stroke="none">
                  <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z" />
                </svg>
              </span>
            </div>
            <p class="bio">{{ profile.bio || '这个人很懒，什么都没写...' }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Tabs -->
    <main class="main">
      <div class="tab-bar">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab-btn', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
        >{{ tab.label }}</button>
      </div>

      <div class="tab-content">
        <!-- 我的会议 -->
        <div v-if="activeTab === 'meetings'" class="meetings-panel">
          <div class="meeting-block" v-if="myMeetings.created.length">
            <div class="block-header">
              <span class="block-dot block-dot--created"></span>
              <h3 class="block-title">我创建的会议</h3>
              <span class="block-count">{{ myMeetings.created.length }}</span>
            </div>
            <div class="table">
              <div class="table-row table-row--header">
                <span class="col col--code">会议号</span>
                <span class="col col--title">标题</span>
                <span class="col col--status">状态</span>
                <span class="col col--meta">人数</span>
                <span class="col col--time">创建时间</span>
              </div>
              <div class="table-row" v-for="m in myMeetings.created" :key="m.meeting_no">
                <span class="col col--code">{{ m.meeting_no }}</span>
                <span class="col col--title">{{ m.title || '未命名' }}</span>
                <span class="col col--status">
                  <span :class="['status-tag', statusClass(m.status)]">{{ statusLabel(m.status) }}</span>
                </span>
                <span class="col col--meta">{{ m.participant_count }} 人</span>
                <span class="col col--time">{{ formatDate(m.created_at) }}</span>
              </div>
            </div>
          </div>

          <div class="meeting-block" v-if="myMeetings.participated.length">
            <div class="block-header">
              <span class="block-dot block-dot--joined"></span>
              <h3 class="block-title">参加过的会议</h3>
              <span class="block-count">{{ myMeetings.participated.length }}</span>
            </div>
            <div class="table">
              <div class="table-row table-row--header">
                <span class="col col--code">会议号</span>
                <span class="col col--title">标题</span>
                <span class="col col--status">状态</span>
                <span class="col col--meta">人数</span>
                <span class="col col--time">创建时间</span>
              </div>
              <div class="table-row" v-for="m in myMeetings.participated" :key="m.meeting_no">
                <span class="col col--code">{{ m.meeting_no }}</span>
                <span class="col col--title">{{ m.title || '未命名' }}</span>
                <span class="col col--status">
                  <span :class="['status-tag', statusClass(m.status)]">{{ statusLabel(m.status) }}</span>
                </span>
                <span class="col col--meta">{{ m.participant_count }} 人</span>
                <span class="col col--time">{{ formatDate(m.created_at) }}</span>
              </div>
            </div>
          </div>

          <div class="empty" v-if="!myMeetings.created.length && !myMeetings.participated.length">
            <p class="empty-text">暂无会议记录</p>
          </div>
        </div>

        <!-- 我的录制 -->
        <div v-if="activeTab === 'recordings'" class="recordings-panel">
          <div class="meeting-block" v-if="myRecordings.length">
            <div class="block-header">
              <span class="block-dot block-dot--recording"></span>
              <h3 class="block-title">我发起的录制</h3>
              <span class="block-count">{{ myRecordings.length }}</span>
            </div>
            <div class="table">
              <div class="table-row table-row--header">
                <span class="col col--code">会议号</span>
                <span class="col col--title">文件名</span>
                <span class="col col--status">状态</span>
                <span class="col col--meta">大小</span>
                <span class="col col--time">时间</span>
              </div>
              <div class="table-row" v-for="r in myRecordings" :key="r.id">
                <span class="col col--code">{{ r.meeting_no }}</span>
                <span class="col col--title">{{ r.file_name }}</span>
                <span class="col col--status">
                  <span :class="['status-tag', r.status === 'completed' ? 'status-ended' : 'status-ongoing']">{{ r.status === 'completed' ? '已完成' : '录制中' }}</span>
                </span>
                <span class="col col--meta">{{ formatSize(r.file_size) }}</span>
                <span class="col col--time">{{ formatDate(r.started_at) }}</span>
              </div>
            </div>
          </div>
          <div class="empty" v-else>
            <p class="empty-text">暂无录制记录</p>
          </div>
        </div>

        <!-- 账号安全 -->
        <div v-if="activeTab === 'security'" class="security-panel">
          <div class="form-card">
            <h3 class="form-title">修改密码</h3>
            <el-form :model="passwordForm" label-position="top" class="swiss-form">
              <el-form-item label="原密码">
                <el-input v-model="passwordForm.old_password" type="password" show-password placeholder="输入原密码" />
              </el-form-item>
              <el-form-item label="新密码">
                <el-input v-model="passwordForm.new_password" type="password" show-password placeholder="至少6位" />
              </el-form-item>
              <el-form-item label="确认新密码">
                <el-input v-model="passwordForm.confirm_password" type="password" show-password placeholder="再次输入新密码" />
              </el-form-item>
              <el-button type="primary" @click="handleChangePassword" :loading="changingPwd" class="swiss-btn-confirm">
                修改密码
              </el-button>
            </el-form>
          </div>
        </div>

        <!-- 个人中心 -->
        <div v-if="activeTab === 'center'" class="center-panel">
          <div class="form-card">
            <h3 class="form-title">编辑资料</h3>
            <el-form :model="editForm" label-position="top" class="swiss-form">
              <el-form-item label="昵称">
                <el-input v-model="editForm.display_name" placeholder="你的昵称" maxlength="32" />
              </el-form-item>
              <el-form-item label="个性签名">
                <el-input v-model="editForm.bio" type="textarea" :rows="3" placeholder="写一句话介绍自己..." maxlength="128" />
              </el-form-item>
              <el-form-item label="认证标识">
                <div class="verified-row">
                  <el-switch v-model="editForm.verified" />
                  <span class="verified-hint">{{ editForm.verified ? '已认证' : '未认证' }}</span>
                </div>
              </el-form-item>
              <el-button type="primary" @click="handleUpdateProfile" :loading="updating" class="swiss-btn-confirm">
                保存修改
              </el-button>
            </el-form>
          </div>
        </div>
      </div>
    </main>

    <!-- Hidden file input -->
    <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="handleFileChange" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const API_BASE = '/api/v1'
const API_STATIC = window.location.origin
const router = useRouter()

const activeTab = ref('meetings')
const tabs = [
  { key: 'meetings', label: '我的会议' },
  { key: 'recordings', label: '我的录制' },
  { key: 'security', label: '账号安全' },
  { key: 'center', label: '个人中心' }
]

const profile = reactive({
  id: 0,
  display_name: '',
  avatar_url: '',
  cover_url: '',
  bio: '',
  verified: false
})

const myMeetings = reactive({ created: [] as any[], participated: [] as any[] })
const myRecordings = ref<any[]>([])
const changingPwd = ref(false)
const updating = ref(false)

const passwordForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const editForm = reactive({ display_name: '', bio: '', verified: false })

const fileInput = ref<HTMLInputElement | null>(null)
let uploadTarget: 'avatar' | 'cover' = 'avatar'

const getAuthHeaders = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })

const handleLogout = () => { localStorage.clear(); router.push('/login') }

const fetchProfile = async () => {
  try {
    const res = await axios.get(`${API_BASE}/auth/me`, getAuthHeaders())
    const d = res.data.data
    Object.assign(profile, d)
    editForm.display_name = d.display_name
    editForm.bio = d.bio || ''
    editForm.verified = d.verified
  } catch { /* silent */ }
}

const fetchMyMeetings = async () => {
  try {
    const res = await axios.get(`${API_BASE}/meetings/my`, getAuthHeaders())
    const d = res.data.data
    myMeetings.created = d.created || []
    myMeetings.participated = d.participated || []
  } catch { /* silent */ }
}

const fetchMyRecordings = async () => {
  try {
    const res = await axios.get(`${API_BASE}/recordings/my`, getAuthHeaders())
    myRecordings.value = res.data.data || []
  } catch { /* silent */ }
}

const triggerUpload = (target: 'avatar' | 'cover') => {
  uploadTarget = target
  fileInput.value?.click()
}

const handleFileChange = async (e: Event) => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  const formData = new FormData()
  formData.append('file', file)

  try {
    const res = await axios.post(`${API_BASE}/auth/upload`, formData, {
      headers: {
        Authorization: `Bearer ${localStorage.getItem('token')}`,
        'Content-Type': 'multipart/form-data'
      }
    })
    const url = res.data.data.url
    if (uploadTarget === 'avatar') {
      profile.avatar_url = url
      await axios.put(`${API_BASE}/auth/profile`, { avatar_url: url }, getAuthHeaders())
    } else {
      profile.cover_url = url
      await axios.put(`${API_BASE}/auth/profile`, { cover_url: url }, getAuthHeaders())
    }
    ElMessage.success('上传成功')
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '上传失败')
  }
  input.value = ''
}

const handleUpdateProfile = async () => {
  if (!editForm.display_name.trim()) {
    ElMessage.warning('昵称不能为空')
    return
  }
  updating.value = true
  try {
    await axios.put(`${API_BASE}/auth/profile`, {
      display_name: editForm.display_name.trim(),
      bio: editForm.bio.trim() || '',
      verified: editForm.verified
    }, getAuthHeaders())
    profile.display_name = editForm.display_name.trim()
    profile.bio = editForm.bio.trim()
    profile.verified = editForm.verified
    localStorage.setItem('displayName', editForm.display_name.trim())
    ElMessage.success('资料已保存')
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '保存失败')
  } finally {
    updating.value = false
  }
}

const handleChangePassword = async () => {
  if (!passwordForm.old_password) { ElMessage.warning('请输入原密码'); return }
  if (passwordForm.new_password.length < 6) { ElMessage.warning('新密码至少6位'); return }
  if (passwordForm.new_password !== passwordForm.confirm_password) { ElMessage.warning('两次密码不一致'); return }
  changingPwd.value = true
  try {
    await axios.put(`${API_BASE}/auth/password`, {
      old_password: passwordForm.old_password,
      new_password: passwordForm.new_password
    }, getAuthHeaders())
    ElMessage.success('密码修改成功，请重新登录')
    passwordForm.old_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    setTimeout(() => { localStorage.clear(); router.push('/login') }, 1500)
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '修改失败')
  } finally {
    changingPwd.value = false
  }
}

const statusLabel = (s: string) => {
  const map: Record<string, string> = { ongoing: '进行中', scheduled: '已预约', ended: '已结束' }
  return map[s] || s
}
const statusClass = (s: string) => {
  const map: Record<string, string> = { ongoing: 'status-ongoing', scheduled: 'status-scheduled', ended: 'status-ended' }
  return map[s] || ''
}
const formatDate = (iso: string) => {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
const formatSize = (bytes: number) => {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

onMounted(() => {
  fetchProfile()
  fetchMyMeetings()
  fetchMyRecordings()
})
</script>

<style scoped>
/* ===== Font ===== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

.swiss {
  min-height: 100vh;
  background: #F2F0EB;
  color: #2C2C2C;
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* ===== Header ===== */
.header {
  border-bottom: 1px solid #E8E4DE;
  background: #FAFAF8;
}
.header-grid {
  max-width: 1040px;
  margin: 0 auto;
  padding: 0 40px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-brand { display: flex; align-items: baseline; }
.logo { font-size: 18px; font-weight: 700; letter-spacing: -0.3px; margin: 0; color: #2C2C2C; text-transform: uppercase; }
.logo-divider { display: inline-block; width: 1px; height: 14px; background: #D5D2CC; margin: 0 12px; align-self: center; }
.subtitle { font-size: 11px; font-weight: 400; color: #9E9E9E; letter-spacing: 1px; }
.header-nav { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 500; color: #6B6B6B; letter-spacing: 0.3px; }
.user-label { color: #3D3D3D; font-weight: 600; padding: 0 2px; }
.nav-link { background: none; border: none; font-size: 12px; font-weight: 500; color: #6B6B6B; cursor: pointer; letter-spacing: 0.3px; padding: 0; text-decoration: none; transition: color 0.15s; font-family: inherit; }
.nav-link:hover { color: #2C2C2C; }
.nav-link--signout { color: #9E9E9E; margin-left: 4px; }
.nav-link--signout:hover { color: #C0392B; }
.nav-sep { color: #D5D2CC; font-weight: 300; }

/* ===== Cover Section ===== */
.cover-section {
  background: #FAFAF8;
  border-bottom: 1px solid #E8E4DE;
}
.cover-wrapper {
  max-width: 1040px;
  margin: 0 auto;
}
.cover-img {
  height: 200px;
  background: linear-gradient(135deg, #D5D2CC 0%, #A09890 100%);
  background-size: cover;
  background-position: center;
  position: relative;
  cursor: pointer;
}
.cover-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(44, 44, 44, 0);
  color: transparent;
  transition: all 0.25s;
  font-size: 13px;
  font-weight: 500;
}
.cover-img:hover .cover-overlay {
  background: rgba(44, 44, 44, 0.35);
  color: #FAFAF8;
}

/* ===== Avatar Row ===== */
.avatar-row {
  display: flex;
  align-items: flex-end;
  gap: 20px;
  padding: 0 40px 24px;
  margin-top: -48px;
  position: relative;
  z-index: 1;
}
.avatar-wrapper {
  width: 96px;
  height: 96px;
  border-radius: 50%;
  border: 4px solid #FAFAF8;
  overflow: hidden;
  cursor: pointer;
  position: relative;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(44,44,44,0.08);
}
.avatar-img {
  width: 100%;
  height: 100%;
  background: #D5D2CC;
  background-size: cover;
  background-position: center;
  display: flex;
  align-items: center;
  justify-content: center;
}
.avatar-placeholder {
  font-size: 36px;
  font-weight: 700;
  color: #FAFAF8;
  text-transform: uppercase;
}
.avatar-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(44,44,44,0);
  color: transparent;
  border-radius: 50%;
  transition: all 0.25s;
}
.avatar-wrapper:hover .avatar-overlay {
  background: rgba(44,44,44,0.4);
  color: #FAFAF8;
}
.user-info {
  padding-bottom: 8px;
}
.name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.display-name {
  font-size: 20px;
  font-weight: 700;
  color: #2C2C2C;
  letter-spacing: -0.3px;
}
.verified-badge {
  display: flex;
  align-items: center;
}
.bio {
  font-size: 13px;
  color: #8C8C8C;
  margin: 0;
  line-height: 1.5;
}

/* ===== Main ===== */
.main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 0 40px 80px;
}

/* ===== Tab Bar ===== */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid #E8E4DE;
  margin-bottom: 40px;
  padding-top: 32px;
}
.tab-btn {
  background: none;
  border: none;
  padding: 12px 24px;
  font-size: 13px;
  font-weight: 500;
  color: #8C8C8C;
  cursor: pointer;
  font-family: inherit;
  letter-spacing: 0.3px;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: all 0.15s;
}
.tab-btn:hover { color: #3D3D3D; }
.tab-btn.active {
  color: #2C2C2C;
  font-weight: 600;
  border-bottom-color: #2C2C2C;
}

/* ===== Table ===== */
.meeting-block { margin-bottom: 48px; }
.block-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 2px solid #D5D2CC;
}
.block-dot { width: 8px; height: 8px; flex-shrink: 0; }
.block-dot--created { background: #5A7D9A; }
.block-dot--joined { background: #6B9E7A; }
.block-dot--recording { background: #C0392B; }
.block-title { font-size: 12px; font-weight: 600; letter-spacing: 0.5px; color: #2C2C2C; margin: 0; }
.block-count { font-size: 10px; font-weight: 600; color: #A8A8A8; background: #EEECE8; padding: 1px 6px; }

.table { border-top: 1px solid #E8E4DE; }
.table-row {
  display: grid;
  grid-template-columns: 108px 1fr 90px 72px 160px;
  align-items: center;
  padding: 13px 0;
  border-bottom: 1px solid #EFECE7;
  font-size: 13px;
  cursor: default;
  transition: background 0.12s;
}
.table-row--header { cursor: default; padding: 10px 0; border-bottom: 1px solid #E8E4DE; }
.table-row--header .col { font-size: 11px; font-weight: 600; letter-spacing: 0.3px; color: #A8A8A8; }
.table-row:hover:not(.table-row--header) { background: #F5F2EE; }
.col { padding: 0 8px; }
.col--code { font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; font-weight: 600; font-size: 14px; letter-spacing: 1px; color: #2C2C2C; }
.col--title { font-weight: 500; color: #3D3D3D; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col--meta { font-size: 12px; color: #8C8C8C; }
.col--time { font-size: 12px; color: #8C8C8C; }
.col--status { display: flex; }

.status-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  letter-spacing: 0.2px;
}
.status-ongoing { color: #6B9E7A; background: #E8F0E8; }
.status-scheduled { color: #5A7D9A; background: #E8EDF2; }
.status-ended { color: #9E9E9E; background: #EEECE8; }

/* ===== Forms ===== */
.form-card {
  max-width: 480px;
  background: #FAFAF8;
  border: 1px solid #E8E4DE;
  padding: 32px;
}
.form-title {
  font-size: 15px;
  font-weight: 600;
  color: #2C2C2C;
  margin: 0 0 24px;
  letter-spacing: 0.3px;
}
.swiss-form .el-form-item { margin-bottom: 20px; }
.swiss-form .el-form-item__label {
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #8C8C8C;
  margin-bottom: 6px;
}
.swiss-form .el-input__wrapper {
  border-radius: 0;
  box-shadow: 0 0 0 1px #D5D2CC;
  background: #FAFAF8;
  transition: box-shadow 0.15s;
}
.swiss-form .el-input__wrapper:hover { box-shadow: 0 0 0 1px #A09890; }
.swiss-form .el-input__wrapper.is-focus,
.swiss-form .el-input__wrapper.is-focus:hover { box-shadow: 0 0 0 1px #5A7D9A; }
.swiss-form .el-textarea__inner {
  border-radius: 0;
  box-shadow: 0 0 0 1px #D5D2CC;
  background: #FAFAF8;
  font-family: inherit;
  font-size: 13px;
  transition: box-shadow 0.15s;
}
.swiss-form .el-textarea__inner:hover { box-shadow: 0 0 0 1px #A09890; }
.swiss-form .el-textarea__inner:focus { box-shadow: 0 0 0 1px #5A7D9A; }

.verified-row { display: flex; align-items: center; gap: 10px; }
.verified-hint { font-size: 12px; color: #8C8C8C; }

.swiss-btn-confirm {
  border-radius: 0;
  border: 1px solid #2C2C2C;
  background: #2C2C2C;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.3px;
  padding: 8px 22px;
  color: #FAFAF8;
  transition: background 0.15s;
  margin-top: 4px;
}
.swiss-btn-confirm:hover { background: #5A7D9A; border-color: #5A7D9A; }

/* ===== Empty ===== */
.empty { padding: 60px 0; text-align: center; }
.empty-text { font-size: 13px; color: #A8A8A8; font-weight: 400; }

/* ===== Responsive ===== */
@media (max-width: 768px) {
  .header-grid { padding: 0 24px; }
  .cover-img { height: 140px; }
  .avatar-row { padding: 0 24px 20px; margin-top: -40px; }
  .avatar-wrapper { width: 72px; height: 72px; }
  .display-name { font-size: 17px; }
  .main { padding: 0 24px 60px; }
  .table-row { grid-template-columns: 90px 1fr 72px; }
  .col--status, .col--meta, .col--time { display: none; }
  .form-card { padding: 24px; }
}
</style>