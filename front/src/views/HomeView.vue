<template>
  <div class="swiss">
    <!-- Header -->
    <header class="header">
      <div class="header-grid">
        <div class="header-brand">
          <h1 class="logo">LAN Video</h1>
          <span class="logo-divider" aria-hidden="true"></span>
          <span class="subtitle">视频会议</span>
        </div>
        <nav class="header-nav">
          <router-link to="/profile" class="nav-link">个人空间</router-link>
          <span class="nav-sep">/</span>
          <router-link to="/recordings" class="nav-link">录制文件</router-link>
          <span class="nav-sep">/</span>
          <router-link to="/audit_logs" class="nav-link">审计日志</router-link>
          <span class="nav-sep">/</span>
          <span class="user-label">{{ displayName }}</span>
          <button class="nav-link nav-link--signout" @click="handleLogout">退出登录</button>
        </nav>
      </div>
    </header>

    <!-- Main Content -->
    <main class="main">
      <!-- Action Cards -->
      <section class="actions">
        <div class="grid-3">
          <!-- Instant Meeting -->
          <article class="card" @click="openCreateDialog('instant')" role="button" tabindex="0">
            <div class="card-body">
              <div class="card-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <polygon points="5,3 19,12 5,21" />
                </svg>
              </div>
              <div class="card-text">
                <h2 class="card-title">新会议</h2>
                <p class="card-desc">立即开始即时会议</p>
              </div>
            </div>
            <div class="card-footer">
              <span class="card-label">立即创建</span>
              <span class="card-arrow">&rarr;</span>
            </div>
          </article>

          <!-- Schedule Meeting -->
          <article class="card" @click="openCreateDialog('scheduled')" role="button" tabindex="0">
            <div class="card-body">
              <div class="card-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="3" y="4" width="18" height="18" rx="2" />
                  <line x1="16" y1="2" x2="16" y2="6" />
                  <line x1="8" y1="2" x2="8" y2="6" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
              </div>
              <div class="card-text">
                <h2 class="card-title">预约会议</h2>
                <p class="card-desc">计划未来会议时间</p>
              </div>
            </div>
            <div class="card-footer">
              <span class="card-label">设定时间</span>
              <span class="card-arrow">&rarr;</span>
            </div>
          </article>

          <!-- Join Meeting -->
          <article class="card" @click="showJoinDialog = true" role="button" tabindex="0">
            <div class="card-body">
              <div class="card-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M15 3h6v6" />
                  <path d="M21 3l-7 7" />
                  <path d="M9 21H3v-6" />
                  <path d="M3 21l7-7" />
                </svg>
              </div>
              <div class="card-text">
                <h2 class="card-title">加入会议</h2>
                <p class="card-desc">输入会议号加入</p>
              </div>
            </div>
            <div class="card-footer">
              <span class="card-label">输入号码</span>
              <span class="card-arrow">&rarr;</span>
            </div>
          </article>
        </div>
      </section>

      <!-- Meeting Lists -->
      <section class="meetings">
        <div class="block-header">
          <h3 class="block-title">我的会议</h3>
          <span class="block-count">{{ allMeetings.length }}</span>
        </div>

        <div class="table" v-if="allMeetings.length > 0">
          <div class="table-row table-row--header">
            <span class="col col--code">会议号</span>
            <span class="col col--title">标题</span>
            <span class="col col--host">发起人</span>
            <span class="col col--time">开始时间</span>
            <span class="col col--time">结束时间</span>
            <span class="col col--status">状态</span>
            <span class="col col--action"></span>
          </div>
          <div
            class="table-row"
            v-for="m in allMeetings"
            :key="m.meeting_no"
            @click="m.status === 'ongoing' ? joinByNo(m.meeting_no) : undefined"
          >
            <span class="col col--code">{{ m.meeting_no }}</span>
            <span class="col col--title">
              {{ m.title || '未命名会议' }}
              <span class="role-tag" :class="m.role">{{ m.role === 'creator' ? '我发起' : '我参加' }}</span>
            </span>
            <span class="col col--host">{{ m.creator_name }}</span>
            <span class="col col--time">{{ formatTime(m.created_at) }}</span>
            <span class="col col--time">{{ m.ended_at ? formatTime(m.ended_at) : '-' }}</span>
            <span class="col col--status">
              <span class="status-badge" :class="m.status">
                {{ statusMap[m.status] || m.status }}
              </span>
            </span>
            <span class="col col--action">
              <template v-if="m.status === 'ongoing'">加入 &rarr;</template>
              <template v-else-if="m.status === 'scheduled'">
                <button v-if="m.role === 'creator'" class="start-btn" @click.stop="startMeeting(m.meeting_no)">开始</button>
                <template v-else>等待开始</template>
              </template>
              <template v-else>
                <button class="report-btn" @click.stop="openReport(m.meeting_no)">统计</button>
              </template>
            </span>
          </div>
        </div>

        <!-- Empty State -->
        <section class="empty" v-else>
          <p class="empty-text">暂无会议记录</p>
          <p class="empty-hint">创建或加入一个会议开始吧</p>
        </section>
      </section>
    </main>

    <!-- Create Meeting Dialog -->
    <el-dialog
      v-model="showCreateDialog"
      :title="createMode === 'instant' ? '新会议' : '预约会议'"
      width="420px"
      class="swiss-dialog"
    >
      <el-form :model="createForm" label-position="top">
        <el-form-item label="标题">
          <el-input v-model="createForm.title" placeholder="会议标题（可选）" />
        </el-form-item>
        <el-form-item v-if="createMode === 'scheduled'" label="日期时间">
          <el-date-picker
            v-model="createForm.scheduled_at"
            type="datetime"
            placeholder="选择日期和时间"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="createForm.password" placeholder="6位数字密码（可选）" maxlength="6" show-password />
        </el-form-item>
        <el-form-item label="等候室">
          <el-switch v-model="createForm.waiting_room" />
          <span style="margin-left:8px;font-size:11px;color:#8C8C8C">开启后需主持人准入才能加入</span>
        </el-form-item>
        <el-form-item label="白名单">
          <el-switch v-model="createForm.whitelist" />
          <span style="margin-left:8px;font-size:11px;color:#8C8C8C">仅白名单用户可加入</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false" class="swiss-btn-cancel">取消</el-button>
        <el-button type="primary" @click="handleCreate" :loading="creating" class="swiss-btn-confirm">创建</el-button>
      </template>
    </el-dialog>

    <!-- Join Meeting Dialog -->
    <el-dialog v-model="showJoinDialog" title="加入会议" width="380px" class="swiss-dialog">
      <el-form :model="joinForm" label-position="top">
        <el-form-item label="会议号">
          <el-input v-model="joinForm.meetingNo" placeholder="输入6位会议号" maxlength="6" />
        </el-form-item>
        <el-form-item v-if="joinNeedPassword" label="密码">
          <el-input v-model="joinForm.password" placeholder="会议密码" maxlength="6" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showJoinDialog = false; joinNeedPassword = false" class="swiss-btn-cancel">取消</el-button>
        <el-button type="primary" @click="handleJoin" :loading="joining" class="swiss-btn-confirm">加入</el-button>
      </template>
    </el-dialog>

    <!-- Meeting Report Dialog -->
    <el-dialog v-model="showReportDialog" :title="`会议统计报告`" width="640px" class="swiss-dialog">
      <div v-if="report" class="report">
        <div class="report-head">
          <div class="report-title">{{ report.title || '未命名会议' }}</div>
          <div class="report-meta">
            <span>会议号 {{ report.meeting_no }}</span>
            <span>发起人 {{ report.creator_name }}</span>
          </div>
          <div class="report-meta">
            <span>开始 {{ formatTime(report.created_at) }}</span>
            <span>结束 {{ report.ended_at ? formatTime(report.ended_at) : '进行中' }}</span>
          </div>
        </div>
        <div class="report-table">
          <div class="report-row report-row--head">
            <span class="r-col r-col--name">成员</span>
            <span class="r-col r-col--role">身份</span>
            <span class="r-col r-col--time">进入时间</span>
            <span class="r-col r-col--time">离开时间</span>
            <span class="r-col r-col--dur">参会时长</span>
            <span class="r-col r-col--speech">发言</span>
          </div>
          <div class="report-row" v-for="p in report.participants" :key="p.participant_id">
            <span class="r-col r-col--name">{{ p.display_name }}</span>
            <span class="r-col r-col--role">
              <span class="report-role" :class="p.role">{{ roleMap[p.role] || p.role }}</span>
            </span>
            <span class="r-col r-col--time">{{ formatTime(p.joined_at) || '-' }}</span>
            <span class="r-col r-col--time">{{ p.left_at ? formatTime(p.left_at) : '在会中' }}</span>
            <span class="r-col r-col--dur">{{ p.duration_minutes }} 分钟</span>
            <span class="r-col r-col--speech">{{ p.speech_count }} 条</span>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showReportDialog = false" class="swiss-btn-confirm">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import axios from 'axios'

const API_BASE = '/api/v1'
const router = useRouter()

const displayName = localStorage.getItem('displayName') || ''

const showCreateDialog = ref(false)
const showJoinDialog = ref(false)
const showReportDialog = ref(false)
const creating = ref(false)
const joining = ref(false)
const createMode = ref<'instant' | 'scheduled'>('instant')
const joinNeedPassword = ref(false)

interface ReportParticipant {
  participant_id: number; user_id: number; display_name: string
  role: string; status: string; joined_at: string | null; left_at: string | null
  duration_minutes: number; speech_count: number
}
interface MeetingReport {
  meeting_no: string; title: string; creator_name: string
  created_at: string | null; ended_at: string | null; status: string
  participants: ReportParticipant[]
}
const report = ref<MeetingReport | null>(null)

const roleMap: Record<string, string> = { creator: '发起人', host: '主持人', member: '成员' }

const createForm = ref({ title: '', password: '', scheduled_at: '', waiting_room: false, whitelist: false })
const joinForm = ref({ meetingNo: '', password: '' })
interface MeetingItem {
  meeting_no: string; title: string; status: string; role: string
  creator_name: string; participant_count: number; has_password: boolean
  created_at: string; ended_at: string | null; scheduled_at: string | null
}

const allMeetings = ref<MeetingItem[]>([])

const statusMap: Record<string, string> = {
  ongoing: '进行中',
  ended: '已结束',
  scheduled: '已预约'
}

const getAuthHeaders = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })

const handleLogout = () => { localStorage.clear(); router.push('/login') }

const fetchMeetings = async () => {
  try {
    const res = await axios.get(`${API_BASE}/meetings/my`, getAuthHeaders())
    const created = res.data.data?.created || []
    const participated = res.data.data?.participated || []
    allMeetings.value = [...created, ...participated].sort(
      (a: MeetingItem, b: MeetingItem) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )
    console.log('我的会议:', allMeetings.value.length, '条')
  } catch (e: any) {
    console.error('获取会议列表失败:', e.message || e)
  }
}

const openCreateDialog = (mode: 'instant' | 'scheduled') => {
  createMode.value = mode
  createForm.value = { title: '', password: '', scheduled_at: '', waiting_room: false, whitelist: false }
  showCreateDialog.value = true
}

const handleCreate = async () => {
  if (createMode.value === 'scheduled' && !createForm.value.scheduled_at) {
    ElMessage.warning('请选择日期和时间')
    return
  }
  creating.value = true
  try {
    const res = await axios.post(`${API_BASE}/meetings`, {
      title: createForm.value.title,
      password: createForm.value.password,
      scheduled_at: createForm.value.scheduled_at,
      waiting_room: createForm.value.waiting_room,
      whitelist: createForm.value.whitelist
    }, getAuthHeaders())
    const d = res.data.data
    ElMessage.success(`会议 ${d.meeting_no} 已创建`)
    // 后端只保存密码哈希，创建者无法再读回明文；这里在本地留一份供后续查看/复制
    if (createForm.value.password) {
      localStorage.setItem(`meeting_pwd_${d.meeting_no}`, createForm.value.password)
    }
    showCreateDialog.value = false
    if (d.status === 'ongoing') {
      router.push(`/meeting/${d.meeting_no}`)
    } else {
      fetchMeetings()
    }
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

const joinByNo = async (meetingNo: string) => {
  joinForm.value = { meetingNo, password: '' }
  joinNeedPassword.value = false
  showJoinDialog.value = true
}

const handleJoin = async () => {
  if (joinForm.value.meetingNo.length !== 6) {
    ElMessage.warning('请输入6位会议号')
    return
  }
  joining.value = true
  try {
    await axios.post(`${API_BASE}/meetings/${joinForm.value.meetingNo}/join`, {
      password: joinForm.value.password
    }, getAuthHeaders())
    showJoinDialog.value = false
    joinNeedPassword.value = false
    router.push(`/meeting/${joinForm.value.meetingNo}`)
  } catch (err: any) {
    const msg = err.response?.data?.message || ''
    if (msg === '需要会议密码') {
      joinNeedPassword.value = true
      ElMessage.warning('需要输入密码')
    } else if (msg === '会议密码错误') {
      ElMessage.error('密码错误')
    } else {
      ElMessage.error(msg || '加入失败')
    }
  } finally {
    joining.value = false
  }
}

const startMeeting = async (meetingNo: string) => {
  try {
    await axios.post(`${API_BASE}/meetings/${meetingNo}/start`, {}, getAuthHeaders())
    ElMessage.success('会议已开始')
    fetchMeetings()
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '开始失败')
  }
}

const openReport = async (meetingNo: string) => {
  try {
    const res = await axios.get(`${API_BASE}/meetings/${meetingNo}/report`, getAuthHeaders())
    report.value = res.data.data
    showReportDialog.value = true
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '获取报告失败')
  }
}

const formatTime = (iso: string | null | undefined) => {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const REMINDER_WINDOW_MS = 10 * 60 * 1000 // 提前10分钟提醒
const remindedMeetings = new Set<string>()
let reminderTimer: number | undefined

const checkScheduledReminders = () => {
  const now = Date.now()
  for (const m of allMeetings.value) {
    if (m.status !== 'scheduled' || !m.scheduled_at) continue
    const startTime = new Date(m.scheduled_at).getTime()
    const diff = startTime - now
    if (diff < 0 || diff > REMINDER_WINDOW_MS) continue
    if (remindedMeetings.has(m.meeting_no)) continue
    remindedMeetings.add(m.meeting_no)
    const mins = Math.max(1, Math.round(diff / 60000))
    ElNotification({
      title: '会议即将开始',
      message: `「${m.title || '未命名会议'}」(${m.meeting_no}) 将在 ${mins} 分钟后开始`,
      type: 'info',
      duration: 8000
    })
  }
}

onMounted(async () => {
  await fetchMeetings()
  checkScheduledReminders()
  reminderTimer = window.setInterval(checkScheduledReminders, 30 * 1000)
})

onUnmounted(() => { if (reminderTimer) clearInterval(reminderTimer) })
</script>

<style>
/* ===== Element Plus Dialog Overrides ===== */
.swiss-dialog {
  --el-dialog-border-radius: 0;
}
.swiss-dialog .el-dialog {
  border-radius: 0;
  box-shadow: 0 4px 24px rgba(44, 44, 44, 0.06);
  background: #FAFAF8;
}
.swiss-dialog .el-dialog__header {
  padding: 28px 32px 0;
  margin: 0;
}
.swiss-dialog .el-dialog__title {
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #2C2C2C;
}
.swiss-dialog .el-dialog__headerbtn {
  top: 28px;
  right: 28px;
}
.swiss-dialog .el-dialog__body {
  padding: 24px 32px;
}
.swiss-dialog .el-dialog__footer {
  padding: 0 32px 28px;
}
.swiss-dialog .el-form-item {
  margin-bottom: 20px;
}
.swiss-dialog .el-form-item__label {
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #8C8C8C;
  margin-bottom: 6px;
}
.swiss-dialog .el-input__wrapper {
  border-radius: 0;
  box-shadow: 0 0 0 1px #D5D2CC;
  background: #FAFAF8;
  transition: box-shadow 0.15s;
}
.swiss-dialog .el-input__wrapper:hover {
  box-shadow: 0 0 0 1px #A09890;
}
.swiss-dialog .el-input__wrapper.is-focus,
.swiss-dialog .el-input__wrapper.is-focus:hover {
  box-shadow: 0 0 0 1px #5A7D9A;
}

.swiss-btn-cancel {
  border-radius: 0;
  border: 1px solid #D5D2CC;
  background: #FAFAF8;
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.3px;
  padding: 8px 22px;
  color: #6B6B6B;
  transition: border-color 0.15s, color 0.15s;
}
.swiss-btn-cancel:hover {
  border-color: #5A7D9A;
  color: #5A7D9A;
}

.swiss-btn-confirm {
  border-radius: 0;
  border: 1px solid #2C2C2C;
  background: #2C2C2C;
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.3px;
  padding: 8px 22px;
  color: #FAFAF8;
  transition: background 0.15s;
}
.swiss-btn-confirm:hover {
  background: #5A7D9A;
  border-color: #5A7D9A;
}
</style>

<style scoped>
/* ===== Font ===== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ===== Root ===== */
.swiss {
  min-height: 100vh;
  background: #F2F0EB;
  color: #2C2C2C;
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
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

.header-brand {
  display: flex;
  align-items: baseline;
  gap: 0;
}

.logo {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.3px;
  margin: 0;
  color: #2C2C2C;
  text-transform: uppercase;
}

.logo-divider {
  display: inline-block;
  width: 1px;
  height: 14px;
  background: #D5D2CC;
  margin: 0 12px;
  align-self: center;
}

.subtitle {
  font-size: 11px;
  font-weight: 400;
  color: #9E9E9E;
  letter-spacing: 1px;
}

.header-nav {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  color: #6B6B6B;
  letter-spacing: 0.3px;
}

.user-label {
  color: #3D3D3D;
  font-weight: 600;
  padding: 0 2px;
}

.nav-link {
  background: none;
  border: none;
  font-size: 12px;
  font-weight: 500;
  color: #6B6B6B;
  cursor: pointer;
  letter-spacing: 0.3px;
  padding: 0;
  text-decoration: none;
  transition: color 0.15s;
  font-family: inherit;
}

.nav-link:hover {
  color: #2C2C2C;
}

.nav-link--signout {
  color: #9E9E9E;
  margin-left: 4px;
}

.nav-link--signout:hover {
  color: #C0392B;
}

.nav-sep {
  color: #D5D2CC;
  font-weight: 300;
}

/* ===== Main ===== */
.main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 72px 40px 100px;
}

/* ===== Actions Grid ===== */
.actions {
  margin-bottom: 80px;
}

.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}

/* ===== Card ===== */
.card {
  background: #FAFAF8;
  border: 1px solid #E8E4DE;
  padding: 0;
  cursor: pointer;
  transition: border-color 0.25s ease, transform 0.25s ease, box-shadow 0.25s ease;
  display: flex;
  flex-direction: column;
  position: relative;
}

.card:hover {
  border-color: #C8C4BC;
  transform: translateY(-1px);
  box-shadow: 0 2px 16px rgba(44, 44, 44, 0.04);
}

.card:focus-visible {
  outline: 2px solid #5A7D9A;
  outline-offset: 2px;
}

.card-body {
  padding: 28px 28px 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  flex: 1;
}

.card-icon {
  color: #5A7D9A;
  line-height: 1;
  display: flex;
  align-items: center;
}

.card-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  letter-spacing: -0.3px;
  color: #2C2C2C;
  transition: color 0.15s;
}

.card:hover .card-title {
  color: #5A7D9A;
}

.card-desc {
  font-size: 12px;
  color: #8C8C8C;
  margin: 0;
  line-height: 1.5;
  font-weight: 400;
}

.card-footer {
  padding: 14px 28px;
  border-top: 1px solid #EFECE7;
  display: flex;
  align-items: center;
  justify-content: space-between;
  transition: border-color 0.15s, background 0.15s;
}

.card:hover .card-footer {
  border-color: #E8E4DE;
  background: #F7F5F2;
}

.card-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #A8A8A8;
  transition: color 0.15s;
}

.card:hover .card-label {
  color: #5A7D9A;
}

.card-arrow {
  font-size: 14px;
  color: #C8C8C8;
  transition: color 0.15s, transform 0.15s;
}

.card:hover .card-arrow {
  color: #5A7D9A;
  transform: translateX(3px);
}

/* ===== Meeting Lists ===== */
.meetings {
  display: flex;
  flex-direction: column;
  gap: 56px;
}

.block-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 2px solid #D5D2CC;
}

.block-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #2C2C2C;
  margin: 0;
}

.block-count {
  font-size: 10px;
  font-weight: 600;
  color: #A8A8A8;
  background: #EEECE8;
  padding: 1px 6px;
  margin-left: 2px;
}

/* ===== Table ===== */
.table {
  border-top: 1px solid #E8E4DE;
}

.table-row {
  display: grid;
  grid-template-columns: 92px 1fr 80px 120px 120px 70px 72px;
  align-items: center;
  padding: 13px 0;
  border-bottom: 1px solid #EFECE7;
  cursor: pointer;
  transition: background 0.12s;
  font-size: 13px;
}

.table-row--header {
  cursor: default;
  padding: 10px 0;
  border-bottom: 1px solid #E8E4DE;
}

.table-row--header .col {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #A8A8A8;
}

.table-row:hover:not(.table-row--header) {
  background: #F5F2EE;
}

.table-row:hover .col--action {
  color: #5A7D9A;
  transform: translateX(2px);
}

.col {
  padding: 0 8px;
}

.col--code {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 1px;
  color: #2C2C2C;
}

.col--title {
  font-weight: 500;
  color: #3D3D3D;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col--meta {
  font-size: 12px;
  color: #8C8C8C;
}

.col--host {
  font-size: 12px;
  color: #5A7D9A;
  font-weight: 500;
}

.col--time {
  font-size: 11px;
  color: #8C8C8C;
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  letter-spacing: -0.3px;
}

.col--status {
  display: flex;
  align-items: center;
}

.status-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  padding: 2px 8px;
}

.status-badge.ongoing {
  color: #6B9E7A;
  background: #EDF5EF;
}

.status-badge.ended {
  color: #A8A8A8;
  background: #F0EEE9;
}

.status-badge.scheduled {
  color: #5A7D9A;
  background: #EDF2F7;
}

.role-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.2px;
  padding: 1px 6px;
  margin-left: 8px;
  vertical-align: 2px;
}

.role-tag.creator {
  color: #5A7D9A;
  background: #EDF2F7;
}

.role-tag.participant {
  color: #8C8C8C;
  background: #F0EEE9;
}

.col--lock {
  text-align: center;
  color: #A8A8A8;
  display: flex;
  align-items: center;
  justify-content: center;
}

.col--action {
  text-align: right;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #C8C8C8;
  transition: color 0.15s, transform 0.15s;
}

.start-btn {
  padding: 4px 14px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #1a1a2e;
  background: #fff;
  border: 1px solid #1a1a2e;
  border-radius: 14px;
  cursor: pointer;
  transition: all 0.15s;
}

.start-btn:hover {
  background: #1a1a2e;
  color: #fff;
}

.report-btn {
  padding: 4px 14px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #5A7D9A;
  background: transparent;
  border: 1px solid #5A7D9A;
  border-radius: 0;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.report-btn:hover {
  background: #5A7D9A;
  color: #fff;
}

/* ===== Report Dialog ===== */
.report {
  font-family: 'Inter', 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.report-head {
  border-bottom: 2px solid #D5D2CC;
  padding-bottom: 14px;
  margin-bottom: 16px;
}
.report-title {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.3px;
  color: #2C2C2C;
  margin-bottom: 8px;
}
.report-meta {
  display: flex;
  gap: 20px;
  font-size: 12px;
  color: #8C8C8C;
  margin-top: 4px;
}
.report-table {
  border-top: 1px solid #E8E4DE;
}
.report-row {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr 1.1fr 1.1fr 0.8fr 0.5fr;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid #EFECE7;
  font-size: 12px;
}
.report-row--head {
  border-bottom: 1px solid #E8E4DE;
}
.report-row--head .r-col {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #A8A8A8;
}
.r-col {
  padding: 0 6px;
  color: #3D3D3D;
}
.r-col--name {
  font-weight: 500;
  color: #2C2C2C;
}
.r-col--time {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 11px;
  color: #8C8C8C;
}
.r-col--dur {
  color: #5A7D9A;
  font-weight: 500;
}
.r-col--speech {
  text-align: right;
  font-weight: 600;
}
.report-role {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.2px;
  padding: 1px 7px;
}
.report-role.creator {
  color: #5A7D9A;
  background: #EDF2F7;
}
.report-role.host {
  color: #B8935A;
  background: #F5F0E8;
}
.report-role.member {
  color: #8C8C8C;
  background: #F0EEE9;
}

/* ===== Empty State ===== */
.empty {
  padding: 60px 0;
  text-align: center;
}

.empty-text {
  font-size: 13px;
  color: #A8A8A8;
  font-weight: 400;
  letter-spacing: 0.2px;
}

.empty-hint {
  font-size: 11px;
  color: #C8C8C8;
  margin-top: 8px;
}

/* ===== Responsive ===== */
@media (max-width: 768px) {
  .header-grid {
    padding: 0 24px;
  }

  .main {
    padding: 48px 24px 64px;
  }

  .grid-3 {
    grid-template-columns: 1fr;
    gap: 16px;
  }

  .actions {
    margin-bottom: 48px;
  }

  .table-row {
    grid-template-columns: 90px 1fr 48px;
  }

  .col--host,
  .col--time,
  .col--status {
    display: none;
  }

  .card-body {
    padding: 22px 22px 16px;
  }

  .card-footer {
    padding: 12px 22px;
  }
}

@media (max-width: 480px) {
  .header-grid {
    height: auto;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    padding: 16px 20px;
  }

  .main {
    padding: 32px 20px 48px;
  }

  .meetings {
    gap: 40px;
  }

  .table-row {
    grid-template-columns: 80px 1fr 40px;
  }
}
</style>